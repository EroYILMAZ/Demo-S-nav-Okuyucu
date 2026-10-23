"""Sınav Okuyucu - yerel web sunucusu.

Bilgisayarda çalışır; aynı Wi-Fi'deki telefon tarayıcıdan ya da Android uygulamasından bağlanıp kağıtların
fotoğrafını gönderir. Bilgisayarın kendisi (127.0.0.1) kodsuz erişir; ağdaki diğer cihazlar bir kez
eşleştirme koduyla bağlanır (okul ağındaki herkes öğrenci fotoğraflarına ve puanlara erişemesin diye).
"""
import hashlib
import io
import json
import os
import queue
import re
import secrets
import socket
import threading
import time
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import config, sorular, store
from .bolme import split_sheets
from .excel import build_workbook
from .matching import annotate as _annotate_raw, parse_roster_file, parse_roster_text, record_warnings, resolve_student
from .recognizer import EngineError, RecognitionError, identify, ollama_status, recognize_auto, recognize_templated

STATIC = Path(__file__).parent / "static"
PORT = 8000
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def annotate(exam: dict, public: bool = True) -> dict:
    """Uyarıları ve eminliği ekle. public: arayüze giden yanıtsa modelin ham çıktısını (büyük) çıkar."""
    exam = _annotate_raw(exam)
    if public:
        for r in exam["kayitlar"]:
            r.pop("okunan", None)
    return exam


app = FastAPI(title="Sınav Okuyucu")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------- cihaz eşleştirme

DEVICES_FILE = config.DATA_DIR / "cihazlar.json"
COOKIE = "so_anahtar"
_pair_code = f"{secrets.randbelow(1_000_000):06d}"  # program her açıldığında yenilenir; eşleşmiş cihazlar bağlı kalır
_pair_fail: dict[str, list[float]] = {}
_dev_lock = threading.Lock()
AUTH_DISABLED = os.environ.get("SINAV_OKUYUCU_KILITSIZ") == "1"  # yalnızca otomatik testler için
# yalnızca test: bilgisayarın kendisini de eşleşmemiş bir telefon gibi say (eşleştirme ekranını denemek için)
LOCAL_TRUSTED = os.environ.get("SINAV_OKUYUCU_YEREL_KILIT") != "1"


def _devices() -> dict:
    try:
        return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_devices(d: dict) -> None:
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    store.write_durable(DEVICES_FILE, json.dumps(d, ensure_ascii=False, indent=1))


def _trusted(request: Request) -> bool:
    if AUTH_DISABLED or (LOCAL_TRUSTED and request.client and request.client.host in LOCAL_HOSTS):
        return True
    token = request.cookies.get(COOKIE) or request.headers.get("X-Sinav-Anahtar") or ""
    if not token:
        return False
    with _dev_lock:
        return token in _devices()


@app.middleware("http")
async def _auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/durum", "/api/eslestir") and not _trusted(request):
        return JSONResponse({"detail": "eslestirme"}, status_code=401)
    return await call_next(request)


class PairIn(BaseModel):
    kod: str
    ad: str = ""


@app.post("/api/eslestir")
def eslestir(body: PairIn, request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    with _dev_lock:
        fails = [t for t in _pair_fail.get(ip, []) if now - t < 300]
        if len(fails) >= 5:
            raise HTTPException(429, "Çok fazla yanlış deneme. 5 dakika sonra tekrar deneyin.")
        if re.sub(r"\D", "", body.kod) != _pair_code:
            _pair_fail[ip] = fails + [now]
            raise HTTPException(400, "Kod yanlış. Bilgisayardaki Sınav Okuyucu'nun ana sayfasındaki kodu girin.")
        _pair_fail.pop(ip, None)
        token = secrets.token_urlsafe(24)
        d = _devices()
        d[token] = {"ad": body.ad[:80] or request.headers.get("user-agent", "")[:80], "olusturma": now}
        _save_devices(d)
    resp = JSONResponse({"ok": True, "anahtar": token})
    resp.set_cookie(COOKIE, token, max_age=400 * 86400, httponly=True, samesite="lax")
    return resp


@app.delete("/api/cihazlar")
def cihazlari_unut(request: Request):
    """Eşleşmiş bütün cihazların bağlantısını kes (yalnızca bilgisayarın kendisinden)."""
    if not (request.client and request.client.host in LOCAL_HOSTS):
        raise HTTPException(403, "Bu işlem yalnızca bilgisayardan yapılabilir")
    with _dev_lock:
        _save_devices({})
    return {"ok": True}


# ---------------------------------------------------------------- arka plan okuyucu

_jobs: "queue.Queue[tuple]" = queue.Queue()
_current: dict = {}
_engine: dict = {"durum": "", "zaman": 0.0}  # okuma motoru beklenirken arayüze bilgi
MAX_ENGINE_RETRIES = 6


class _Skip(Exception):
    """Kayıt bu arada değişti; bu kuyruk girdisi artık geçersiz."""


def _worker() -> None:
    while True:
        job = _jobs.get()
        exam_id, rec_id, *kind = job
        try:
            if kind and kind[0] == "kimlik":
                _identify(exam_id, rec_id)
            else:
                _process(exam_id, rec_id)
        except Exception as e:  # beklenmeyen hata işçiyi asla durdurmasın
            _set_error(exam_id, rec_id, f"Beklenmeyen hata: {e}")
        finally:
            _current.clear()
            _jobs.task_done()


def _set_error(exam_id: str, rec_id: str, msg: str) -> None:
    """Okunan kaydı hataya düşür. Okunurken sayfa eklendi/geri alındıysa kayıt 'bekliyor'a dönmüştür: o zaman eski
    okumanın hatası yazılmaz, yeni kuyruk girdisi kaydı baştan okur."""
    def fn(exam):
        rec = store.find_record(exam, rec_id)
        if rec.get("durum") == "isleniyor":
            rec["durum"] = "hata"
            rec["hata"] = msg
            rec["ilerleme"] = ""
    try:
        store.update_exam(exam_id, fn)
    except Exception:  # disk dolu, dosya kilitli, bozuk JSON… işçi yine de çalışmaya devam etmeli
        pass


def _engine_wait(exam_id: str, rec_id: str, err: EngineError) -> None:
    """Okuma motoru şu an yok: kağıdı hataya düşürme, bekleyip yeniden dene."""
    attempts = 0

    def fn(e):
        nonlocal attempts
        r = store.find_record(e, rec_id)
        if r.get("durum") != "isleniyor":
            raise _Skip
        attempts = int(r.get("motor_deneme", 0)) + 1
        r["motor_deneme"] = attempts
        r["durum"] = "bekliyor"
        r["ilerleme"] = f"Okuma motoru bekleniyor ({err})"
    try:
        store.update_exam(exam_id, fn)
    except (KeyError, _Skip):
        return
    if attempts > MAX_ENGINE_RETRIES:
        def fail(e):
            r = store.find_record(e, rec_id)
            r.update(durum="hata", hata=str(err), ilerleme="", motor_deneme=0)
        try:
            store.update_exam(exam_id, fail)
        except Exception:
            pass
        return
    _engine.update(durum=str(err), zaman=time.time())
    time.sleep(min(120, 15 * attempts))
    _jobs.put((exam_id, rec_id))


def _process(exam_id: str, rec_id: str) -> None:
    def mark_running(e):
        r = store.find_record(e, rec_id)
        if r.get("durum") != "bekliyor":
            raise _Skip  # bu arada geri alınmış, silinmiş, tamamlanmış ya da başka bir kuyruk girdisiyle okunmuş
        r["durum"] = "isleniyor"
        r["ilerleme"] = "Okuma başlıyor…"
    try:
        exam = store.update_exam(exam_id, mark_running)
        rec = store.find_record(exam, rec_id)
    except (KeyError, _Skip):
        return
    _current.update(exam=exam_id, kayit=rec_id, baslangic=time.time())
    pages_at_start = list(rec["resimler"])

    images = [store.image_dir(exam_id) / name for name in pages_at_start]
    images = [p for p in images if p.exists()]
    if not images:
        _set_error(exam_id, rec_id, "Kağıdın fotoğrafı bulunamadı.")
        return
    settings = config.load_settings()
    started = time.time()

    def progress(text: str) -> None:
        def fn(e):
            r = store.find_record(e, rec_id)
            if r.get("durum") == "isleniyor":
                r["ilerleme"] = text
        try:
            store.update_exam(exam_id, fn)
        except Exception:
            pass

    try:
        tpl = exam.get("sablon") or {}
        tpl_pages = [store.image_dir(exam_id) / n for n in tpl.get("sayfalar", [])]
        if tpl.get("kutular") and tpl_pages and all(p.exists() for p in tpl_pages):
            result = recognize_templated(images, exam["tam_puanlar"], settings, tpl_pages, tpl["kutular"],
                                         progress, roster=exam["ogrenci_listesi"])
        else:
            result = recognize_auto(images, exam["tam_puanlar"], settings, progress, layout=exam.get("duzen"),
                                    roster=exam["ogrenci_listesi"], labels=sorular.labels_of(exam))
    except EngineError as e:
        _engine_wait(exam_id, rec_id, e)
        return
    except RecognitionError as e:
        _set_error(exam_id, rec_id, str(e))
        return
    _engine.update(durum="", zaman=time.time())
    # her sorunun puan kutusu küçük resim olarak saklanır: kontrol ekranında okunan değerin yanında gösterilir
    box_images: dict[str, str] = {}
    for i, im in (result.pop("kutular", None) or {}).items():
        try:
            im = im.convert("RGB")
            im.thumbnail((360, 360), Image.LANCZOS)
            name = store.new_id() + ".jpg"
            im.save(store.image_dir(exam_id) / name, "JPEG", quality=85)
            box_images[str(i)] = name
        except Exception:
            continue
    old_boxes: list[str] = []

    def save(e):
        e["duzen"] = {**(e.get("duzen") or {}), **result["duzen"]}
        r = store.find_record(e, rec_id)
        if r.get("durum") != "isleniyor" or r.get("resimler") != pages_at_start:
            raise _Skip  # okunurken sayfa eklendi/geri alındı: yeni kuyruk girdisi yeniden okuyacak
        n = len(e["tam_puanlar"])  # okunurken soru sayısı değişmiş olabilir
        old_boxes.extend((r.get("kutu_resimleri") or {}).values())
        r["kutu_resimleri"] = {k: v for k, v in box_images.items() if int(k) < n}
        conf = result["eminlik"]
        no, ad, warn, info, kimlik = resolve_student(result["ogrenci_no"], result["ad_soyad"], e["ogrenci_listesi"],
                                                     conf["no"], result.get("liste_secimi"))
        r.update(
            durum="tamam",
            hata="",
            okunan=result,
            ogrenci_no=no,
            ad_soyad=ad,
            eslesme_notu=warn,
            eslesme_bilgi=info,
            puanlar=(list(result["puanlar"]) + [None] * n)[:n],
            yazili_toplam=result["yazili_toplam"],
            supheli={k: v for k, v in result["supheli"].items() if int(k) < n},
            eminlik_puan=(list(conf["puan"]) + [0.0] * n)[:n],
            eminlik_alt={k: v for k, v in conf["alternatif"].items() if int(k) < n},
            eminlik_kimlik=round(kimlik, 4),
            kimlik_elle=False,  # yeniden okundu: öğretmenin eski kimlik düzeltmesi geçersiz
            hizalanamayan=result.get("hizalanamayan") or [],
            okuma_uyarilari=list(result.get("uyarilar") or []),
            ilerleme="",
            motor_deneme=0,
            sure=round(time.time() - started, 1),
            onaylandi=False,
            toplam_uyarisi_kapali=False,
        )
    try:
        store.update_exam(exam_id, save)
    except (KeyError, _Skip):
        old_boxes[:] = list(box_images.values())  # kaydedilmedi: yeni kutu resimleri artık
    for name in old_boxes:
        store.unlink(store.image_dir(exam_id) / name)


def _identify(exam_id: str, rec_id: str) -> None:
    """Sınıf listesi sonradan eklendi: kimliği belirsiz kağıdın yalnızca başlığını listeyle karşılaştır."""
    try:
        exam = store.get_exam(exam_id)
        rec = store.find_record(exam, rec_id)
    except KeyError:
        return
    roster = exam["ogrenci_listesi"]
    images = [store.image_dir(exam_id) / n for n in rec.get("resimler") or []]
    if rec.get("durum") != "tamam" or not roster or not images or not images[0].exists() or rec.get("onaylandi"):
        return
    o0 = rec.get("okunan") or {}
    if not o0.get("ogrenci_no") and not str(o0.get("ad_soyad") or "").strip():
        return  # kağıtta ne isim ne numara var: listeden seçmek tahmin olurdu
    _current.update(exam=exam_id, kayit=rec_id, baslangic=time.time())
    tpl = exam.get("sablon") or {}
    tpl_pages = [store.image_dir(exam_id) / n for n in tpl.get("sayfalar", [])]
    try:
        choice = identify(images[0], roster, config.load_settings(),
                          tpl_pages if tpl_pages and all(p.exists() for p in tpl_pages) else None, tpl.get("kutular"))
    except Exception:
        return  # kimlik denetimi isteğe bağlı: başarısız olursa okunmuş kağıda dokunma
    o = rec.get("okunan") or {}

    def fn(e):
        r = store.find_record(e, rec_id)
        if r.get("durum") != "tamam" or _identity_by_teacher(r) or r.get("onaylandi"):
            return  # bu arada öğretmen düzeltti ya da kağıt yeniden okunuyor
        o["liste_secimi"] = choice
        r["okunan"] = o
        p_no = (o.get("eminlik") or {}).get("no", 0.9)
        r["ogrenci_no"], r["ad_soyad"], r["eslesme_notu"], r["eslesme_bilgi"], k = resolve_student(
            o.get("ogrenci_no", ""), o.get("ad_soyad", ""), e["ogrenci_listesi"], p_no, o["liste_secimi"])
        r["eminlik_kimlik"] = round(k, 4)
    try:
        store.update_exam(exam_id, fn)
    except Exception:
        pass


def _requeue_pending() -> None:
    """Sunucu yeniden başlarsa yarım kalan kağıtları tekrar sıraya al."""
    for exam in store.list_exams():
        ids = [r["id"] for r in exam["kayitlar"] if r.get("durum") in ("bekliyor", "isleniyor")]
        if not ids:
            continue

        def fn(e):
            for r in e["kayitlar"]:
                if r.get("durum") == "isleniyor":
                    r["durum"] = "bekliyor"
                    r["ilerleme"] = ""
        store.update_exam(exam["id"], fn)
        for rid in ids:
            _jobs.put((exam["id"], rid))


DISCOVERY_PORT = 47800
DISCOVERY_ASK = b"SINAV_OKUYUCU_NEREDE"


def _discovery_responder() -> None:
    """Android uygulaması ağa 'neredesin' yayını yapar; bu bilgisayar kendi adresiyle yanıt verir."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError:
        return
    reply = json.dumps({"uygulama": "sinav-okuyucu", "ad": socket.gethostname(), "port": PORT}).encode()
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.strip() == DISCOVERY_ASK:
                sock.sendto(reply, addr)
        except OSError:
            time.sleep(1)


@app.on_event("startup")
def _startup() -> None:
    config.EXAMS_DIR.mkdir(parents=True, exist_ok=True)
    store.cleanup_orphans()  # silinirken dosyası açık kalmış sınav klasörleri (öğrenci fotoğrafı diskte unutulmasın)
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_discovery_responder, daemon=True).start()
    _requeue_pending()


# ---------------------------------------------------------------- yardımcılar


def _lan_urls() -> list[str]:
    primary = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # paket gönderilmez; varsayılan ağ arayüzünün adresi bulunur
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    others = sorted(ip for ip in ips if ip != primary and not ip.startswith(("127.", "169.254.")))
    ordered = ([primary] if primary and not primary.startswith(("127.", "169.254.")) else []) + others
    return [f"http://{ip}:{PORT}" for ip in ordered]


def _exam_or_404(exam_id: str) -> dict:
    try:
        return store.get_exam(exam_id)
    except KeyError:
        raise HTTPException(404, "Sınav bulunamadı")


def _summary(exam: dict) -> dict:
    recs = exam["kayitlar"]
    return {
        k: exam[k] for k in ("id", "ad", "sinif", "ders", "olusturma", "tam_puanlar")
    } | {
        "soru_adlari": sorular.labels_of(exam),
        "sayfa_sayisi": exam.get("sayfa_sayisi", 1),
        "kagit_sayisi": len(recs),
        "bekleyen": sum(r["durum"] in ("bekliyor", "isleniyor") for r in recs),
        "hatali": sum(r["durum"] == "hata" for r in recs),
        "ogrenci_sayisi": len(exam["ogrenci_listesi"]),
        "sablonlu": bool((exam.get("sablon") or {}).get("kutular")),
    }


MAX_PDF_PAGES = 400


class _BadFile(Exception):
    pass


class _Shift(Exception):
    """Sıralı sayfa gruplamasını kaydıracak bir durum (yükleme geri alınır)."""


def to_rgb8(im: Image.Image) -> Image.Image:
    """8 bit RGB. 16 bitlik ya da kayan noktalı gri görüntülerde Pillow'un convert('RGB')'si değerleri 255'te keser:
    tarayıcıdan gelen 16 bitlik gri bir PNG/TIFF bembeyaz bir sayfaya dönüşürdü. Önce 8 bite ölçeklenir."""
    if im.mode.startswith("I;16") or im.mode in ("I", "F"):
        import numpy as np
        x = np.asarray(im, dtype=np.float64)
        top = 65535.0 if im.mode.startswith("I;16") or x.max() > 255 else 255.0
        im = Image.fromarray(np.clip(x * (255.0 / top) + 0.5, 0, 255).astype(np.uint8))
    return im.convert("RGB")


def _save_page(exam_id: str, im: Image.Image) -> str:
    im.thumbnail((2400, 2400), Image.LANCZOS)
    name = store.new_id() + ".jpg"
    # renk ayrıntısı tam tutulur (subsampling=0): kurşun kalem süzgeci mürekkebin rengine bakıyor
    im.save(store.image_dir(exam_id) / name, "JPEG", quality=90, subsampling=0)
    return name


def _save_pages(exam_id: str, data: bytes, filename: str, sheets: int = 1, notes: list | None = None) -> list[str]:
    """Bir resmi ya da PDF'i (ör. fotokopi makinesinin tarayıcısından) sayfa sayfa kaydeder.
    PDF sayfaları tek tek işlenir; yüzlerce sayfalık bir tarama belleği doldurmaz.
    sheets > 1: fotoğrafta yan yana o kadar kağıt var; her biri ayrı sayfa olarak (okuma sırasıyla) kaydedilir."""
    if data[:5] == b"%PDF-" or filename.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:
            raise _BadFile(f"“{filename}” açılamadı (bozuk ya da şifreli PDF olabilir)")
        names: list[str] = []
        try:
            if len(pdf) > MAX_PDF_PAGES:
                raise _BadFile(f"“{filename}” {len(pdf)} sayfa; bir PDF en fazla {MAX_PDF_PAGES} sayfa olabilir")
            for i in range(len(pdf)):
                page = pdf[i]
                w, h = page.get_size()
                bitmap = page.render(scale=2400 / max(w, h))
                names.append(_save_page(exam_id, bitmap.to_pil().convert("RGB")))
                bitmap.close()
                page.close()
        except _BadFile:
            raise
        except Exception:
            for n in names:
                store.unlink(store.image_dir(exam_id) / n)
            raise _BadFile(f"“{filename}” sayfaları okunamadı")
        finally:
            pdf.close()
        return names
    try:
        with Image.open(io.BytesIO(data)) as im:
            photo = to_rgb8(ImageOps.exif_transpose(im))
    except Exception:
        raise _BadFile(f"“{filename}” bir resim ya da PDF değil (HEIC ise telefonda JPEG olarak kaydedin)")
    if sheets <= 1:
        return [_save_page(exam_id, photo)]
    parts, how = split_sheets(photo, sheets)
    if how == "esit" and notes is not None:
        notes.append(filename)
    return [_save_page(exam_id, part) for part in parts]


def _forget_images(e: dict, names: list[str]) -> None:
    """Bütün sayfaları silinmiş dosyaların 'aynı dosya' kaydını unut (yeniden yüklenebilsinler). Çok sayfalı bir PDF'in
    ya da çoklu kağıtlı bir fotoğrafın yalnızca bir kaydı silindiyse dosya unutulmaz: yeniden yüklemek öteki bütün
    sayfaları çoğaltırdı."""
    alive = {n for r in e["kayitlar"] for n in (r.get("resimler") or [])}
    alive -= set(names)
    as_list = lambda v: v if isinstance(v, list) else [v]  # noqa: E731  (eski kayıtlarda yalnızca ilk sayfa)
    e["ozetler"] = {h: v for h, v in (e.get("ozetler") or {}).items() if any(n in alive for n in as_list(v))}


def _close_open_sessions(e: dict, queued: list[str]) -> None:
    """Seri çekim oturumlarında sayfa bekleyen kağıtları, eldeki sayfalarla okumaya gönder."""
    for st in (e.get("oturumlar") or {}).values():
        r = next((x for x in e["kayitlar"] if x["id"] == st.get("acik")), None)
        if r is not None and r["durum"] == "eksik":
            r.update(durum="bekliyor", eksik_sayfa=True)
            queued.append(r["id"])
        st["acik"] = None


# ---------------------------------------------------------------- sayfalar


@app.get("/", response_class=HTMLResponse)
def index():
    # Dosya değiştikçe adres de değişsin; telefon/pencere güncellemeden sonra eski arayüzü önbellekten göstermesin
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        version = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


APK_FILE = STATIC / "SinavOkuyucu.apk"


@app.get("/apk")
def apk():
    """Telefona kurulacak Android uygulaması (QR ile indirilir)."""
    if not APK_FILE.exists():
        raise HTTPException(404, "Android uygulaması bu kurulumda yok")
    return FileResponse(APK_FILE, media_type="application/vnd.android.package-archive", filename="SinavOkuyucu.apk")


def _queue_count() -> int:
    return sum(r.get("durum") in ("bekliyor", "isleniyor") for e in store.list_exams() for r in e["kayitlar"])


@app.get("/api/durum")
def durum(request: Request):
    if not _trusted(request):
        # eşleşmemiş cihaz: yalnızca "burada bir Sınav Okuyucu var" bilgisi
        return {"uygulama": "sinav-okuyucu", "eslesme_gerekli": True}
    settings = config.load_settings()
    local = bool(request.client and request.client.host in LOCAL_HOSTS)
    engine_wait = _engine["durum"] if time.time() - _engine["zaman"] < 300 else ""
    return {
        "uygulama": "sinav-okuyucu",
        "apk_var": APK_FILE.exists(),
        "ollama": ollama_status(settings),
        "motor_bekleniyor": engine_wait,
        "ollama_bulunamadi": os.environ.get("SINAV_OKUYUCU_OLLAMA_YOK") == "1",
        "ayarlar": settings,
        "kuyruk": _queue_count(),
        "isleniyor": dict(_current),
        "adresler": _lan_urls(),
        # eşleştirme kodu yalnızca bilgisayarın kendi ekranında gösterilir
        "eslestirme_kodu": _pair_code if local else None,
        "cihaz_sayisi": len(_devices()) if local else None,
    }


@app.get("/api/qr.svg")
def qr(url: str):
    if not re.match(r"^http://[\d.]+:\d+(/[\w#/-]*)?$", url):
        raise HTTPException(400)
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


class SettingsIn(BaseModel):
    model: str | None = None
    max_kenar: int | None = Field(None, ge=512, le=3000)
    zaman_asimi: int | None = Field(None, ge=30, le=3600)
    ollama_gpu: str | None = None
    yalnizca_renkli_kalem: bool | None = None
    ikinci_model: str | None = None
    ogretmen_kalem_rengi: str | None = None


@app.put("/api/ayarlar")
def ayarlar(body: SettingsIn):
    cur = config.load_settings()
    model = body.model or cur.get("model")
    second = body.ikinci_model or cur.get("ikinci_model")
    if second and second != "kapali" and second == model:
        raise HTTPException(400, "Çapraz kontrol modeli okuma modelinden farklı olmalı")
    return config.save_settings(body.model_dump())


# ---------------------------------------------------------------- sınavlar


class ExamIn(BaseModel):
    ad: str = ""
    sinif: str = ""
    ders: str = ""
    tam_puanlar: list[float]
    soru_adlari: list[str] = []  # kağıttaki soru numaraları ("1", "2", "3a", "3b"...); boşsa 1..n
    sayfa_sayisi: int = 1
    sablon_kaynak: str = ""  # başka şubeye kopyalarken şablonu bu sınavdan al


def _check_maxes(body: ExamIn) -> None:
    maxes = body.tam_puanlar
    if not 1 <= len(maxes) <= 50 or any(m <= 0 for m in maxes):
        raise HTTPException(400, "Soru sayısı 1-50 arası, tam puanlar sıfırdan büyük olmalı")
    if not 1 <= body.sayfa_sayisi <= 8:
        raise HTTPException(400, "Kağıt 1-8 sayfa olabilir")
    if body.soru_adlari:
        if len(body.soru_adlari) != len(maxes):
            raise HTTPException(400, "Soru numaralarının sayısı soru sayısıyla aynı olmalı")
        err = sorular.check(body.soru_adlari)
        if err:
            raise HTTPException(400, err)


def _labels_in(body: ExamIn) -> list[str]:
    names = [sorular.normalize(x) for x in body.soru_adlari]
    return [] if not names or names == sorular.defaults(len(names)) else names


@app.get("/api/sinavlar")
def sinavlar():
    return [_summary(e) for e in store.list_exams()]


@app.get("/api/siniflar")
def siniflar():
    """Listesi kaydedilmiş sınıflar: yeni sınavda 'liste otomatik eklenecek' bilgisi için."""
    return store.list_classes()


@app.post("/api/sinavlar")
def sinav_olustur(body: ExamIn):
    _check_maxes(body)
    exam = store.create_exam(body.ad.strip(), body.sinif.strip(), body.ders.strip(), body.tam_puanlar, body.sayfa_sayisi,
                             _labels_in(body))
    if body.sablon_kaynak:
        try:
            src = store.get_exam(body.sablon_kaynak)
        except KeyError:
            src = None
        tpl = (src or {}).get("sablon") or {}
        if tpl.get("kutular") and len(src["tam_puanlar"]) == len(body.tam_puanlar):
            names = []
            for n in tpl["sayfalar"]:
                f = store.image_dir(src["id"]) / n
                if f.exists():
                    new = store.new_id() + ".jpg"
                    (store.image_dir(exam["id"]) / new).write_bytes(f.read_bytes())
                    names.append(new)
            if len(names) == len(tpl["sayfalar"]):
                def fn(e):
                    e["sablon"] = {"sayfalar": names, "kutular": tpl["kutular"]}
                exam = store.update_exam(exam["id"], fn)
    return _summary(exam)


@app.get("/api/sinavlar/{exam_id}")
def sinav(exam_id: str):
    return annotate(_exam_or_404(exam_id))


@app.put("/api/sinavlar/{exam_id}")
def sinav_guncelle(exam_id: str, body: ExamIn):
    _check_maxes(body)
    _exam_or_404(exam_id)
    queued: list[str] = []
    recheck: list[str] = []

    dropped_images: list[str] = []

    def fn(e):
        old_n = len(e["tam_puanlar"])
        old_maxes = list(e["tam_puanlar"])
        old_pages = int(e.get("sayfa_sayisi", 1))
        old_labels = sorular.labels_of(e)
        e.update(ad=body.ad.strip(), sinif=body.sinif.strip(), ders=body.ders.strip(), tam_puanlar=body.tam_puanlar,
                 sayfa_sayisi=body.sayfa_sayisi, soru_adlari=_labels_in(body))
        plan = sorular.remap(old_labels, sorular.labels_of(e))
        moved = plan != [("ayni", [i]) for i in range(old_n)]
        if not e["ogrenci_listesi"]:
            e["ogrenci_listesi"] = store.get_class_roster(e["sinif"])
            if e["ogrenci_listesi"]:
                _rematch_roster(e, recheck)  # liste kendiliğinden geldi: okunmuş kağıtları da ona göre eşleştir
        n = len(body.tam_puanlar)
        if moved:
            _remap_cells(e, plan, old_labels, dropped_images)
        if moved or list(body.tam_puanlar) != old_maxes:
            # onay eski sorular / tam puanlar için verilmişti: yeni boş soru ya da artık tam puanı aşan bir puan
            # "onaylandı" diye uyarısız dışa aktarılmasın
            for r in e["kayitlar"]:
                p = r.get("puanlar") or []
                if r.get("onaylandi") and (moved or any(
                        i < len(p) and p[i] is not None and p[i] > body.tam_puanlar[i] for i in range(n))):
                    r["onaylandi"] = False
        if body.sayfa_sayisi != old_pages:
            # sayfa sayısı değişti: açık seri çekim oturumundaki yarım kağıt, yeni öğrencinin sayfalarıyla birleşmesin
            _close_open_sessions(e, queued)
    exam = store.update_exam(exam_id, fn)
    for name in dropped_images:
        store.unlink(store.image_dir(exam_id) / name)
    for rid in queued:
        _jobs.put((exam_id, rid))
    for rid in recheck:
        _jobs.put((exam_id, rid, "kimlik"))
    return annotate(exam)


def _remap_cells(e: dict, plan: list, old_labels: list[str], dropped_images: list[str]) -> None:
    """Soru hücreleri değişince (bölme, birleştirme, ekleme, silme) okunmuş puanları ve şablon kutularını kağıttaki soru
    numarasına göre taşır (bkz. sorular.remap). Birleşen maddelerin puanları toplanır; bölünen sorunun maddeleri boş kalır
    ve kontrole düşer (eski puan notta yazar); karşılığı değişen hücrenin şablon kutusu atılır ("kutu yok" uyarısı çıkar,
    öğretmen yeniden çizer). Konuma göre taşımak, ortadaki bir bölmeden sonra puanları uyarısız bir sütun kaydırıyordu."""
    def fmt(v):
        return "boş" if v is None else f"{v:g}"

    for r in e["kayitlar"]:
        p_old = list(r.get("puanlar") or [])
        conf_old = list(r.get("eminlik_puan") or [])
        sup_old = r.get("supheli") or {}
        alt_old = r.get("eminlik_alt") or {}
        box_old = r.get("kutu_resimleri") or {}
        p_new, conf_new, sup_new, alt_new, box_new = [], [], {}, {}, {}
        for j, (kind, src) in enumerate(plan):
            vals = [p_old[i] if i < len(p_old) else None for i in src]
            if kind == "ayni":
                i = src[0]
                p_new.append(vals[0])
                conf_new.append(conf_old[i] if i < len(conf_old) else 0.0)
                for old, new in ((sup_old, sup_new), (alt_old, alt_new), (box_old, box_new)):
                    if str(i) in old:
                        new[str(j)] = old[str(i)]
                continue
            conf_new.append(0.0)
            if kind == "toplam":
                p_new.append(None if None in vals else sum(vals))
                notes = [sup_old[str(i)] for i in src if str(i) in sup_old]
                if notes or None in vals:
                    sup_new[str(j)] = "; ".join(notes + [
                        "Maddeler birleştirildi (" + ", ".join(f"{old_labels[i]}: {fmt(v)}" for i, v in zip(src, vals)) + ")"])
            elif kind == "bolundu":
                p_new.append(None)
                if vals[0] is not None:
                    sup_new[str(j)] = (f"Soru maddelere bölündü (eski {old_labels[src[0]]}. soru puanı: {fmt(vals[0])}); "
                                       "bu maddenin puanını girin ya da kağıdı yeniden okutun")
            else:
                p_new.append(None)
        kept = set(box_new.values())
        dropped_images.extend(name for name in box_old.values() if name not in kept)
        r["puanlar"] = p_new
        if r.get("eminlik_puan"):
            r["eminlik_puan"] = conf_new
        r["supheli"], r["eminlik_alt"], r["kutu_resimleri"] = sup_new, alt_new, box_new
    tpl = e.get("sablon") or {}
    if tpl.get("kutular"):
        # aynı kalan hücrelerin kutuları yeni numarasıyla; ad/numara (0) ve toplam (-1) kutuları olduğu gibi
        where = {src[0] + 1: j + 1 for j, (kind, src) in enumerate(plan) if kind == "ayni"}
        boxes = []
        for b in tpl["kutular"]:
            q = int(b["soru"])
            if q <= 0:
                boxes.append(b)
            elif q in where:
                boxes.append({**b, "soru": where[q]})
        tpl["kutular"] = boxes


@app.delete("/api/sinavlar/{exam_id}")
def sinav_sil(exam_id: str):
    _exam_or_404(exam_id)
    try:
        store.delete_exam(exam_id)
    except OSError:
        raise HTTPException(500, "Bazı fotoğraflar şu an kullanımda olduğu için silinemedi. Biraz sonra yeniden deneyin "
                                 "(program yeniden açılınca kalanlar da silinir).")
    return {"ok": True}


def _identity_by_teacher(r: dict) -> bool:
    """Öğrenciyi öğretmen mi girdi? (Eski kayıtlarda bu, eminlik_kimlik == 1.0 ile işaretleniyordu.)"""
    return bool(r["kimlik_elle"]) if "kimlik_elle" in r else r.get("eminlik_kimlik") == 1.0


def _rematch_roster(e: dict, recheck: list[str]) -> None:
    """Onaylanmamış ve öğretmenin elle düzeltmediği okunmuş kağıtları sınavın (yeni) listesine göre yeniden eşleştir.
    Hâlâ belirsiz olanların başlığı (yalnızca başlığı) listeyle karşılaştırılmak üzere recheck'e eklenir."""
    roster = e["ogrenci_listesi"]
    for r in e["kayitlar"]:
        if r.get("durum") != "tamam" or r.get("onaylandi") or not r.get("okunan") or _identity_by_teacher(r):
            continue
        o = r["okunan"]
        p_no = (o.get("eminlik") or {}).get("no", 0.9)
        r["ogrenci_no"], r["ad_soyad"], r["eslesme_notu"], r["eslesme_bilgi"], k = resolve_student(
            o.get("ogrenci_no", ""), o.get("ad_soyad", ""), roster, p_no, o.get("liste_secimi"))
        r["eminlik_kimlik"] = round(k, 4)
        if (k < 0.9 or r["eslesme_notu"]) and r.get("resimler") and roster:
            recheck.append(r["id"])


class SheetsIn(BaseModel):
    deger: int


@app.put("/api/sinavlar/{exam_id}/fotodaki-kagit")
def fotodaki_kagit(exam_id: str, body: SheetsIn):
    """Bir fotoğrafta kaç kağıt var (1, 2 ya da 4). Sonraki fotoğraflar bu sayıya göre bölünür."""
    if body.deger not in (1, 2, 4):
        raise HTTPException(400, "Bir fotoğrafta 1, 2 ya da 4 kağıt olabilir")
    _exam_or_404(exam_id)

    def fn(e):
        e["fotodaki_kagit"] = body.deger
    return annotate(store.update_exam(exam_id, fn))


@app.post("/api/sinavlar/{exam_id}/liste")
async def liste_yukle(exam_id: str, metin: str = Form(""), dosya: UploadFile | None = File(None),
                      temizle: bool = Form(False)):
    """Sınıf listesi: yapıştırılan metin veya .xlsx/.csv/.txt dosyası. temizle=True listeyi kaldırır."""
    _exam_or_404(exam_id)
    if temizle:
        def clear(e):
            e["ogrenci_listesi"] = []
            store.save_class_roster(e.get("sinif", ""), [])
        return annotate(store.update_exam(exam_id, clear))
    try:
        if dosya is not None and dosya.filename:
            roster = parse_roster_file(dosya.filename, await dosya.read())
        else:
            roster = parse_roster_text(metin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not roster:
        raise HTTPException(400, "Listede öğrenci bulunamadı. Her satırda 'numara ad soyad' olmalı.")

    recheck: list[str] = []

    def fn(e):
        e["ogrenci_listesi"] = roster
        # bu sınıfın sonraki sınavlarında liste kendiliğinden gelsin
        store.save_class_roster(e.get("sinif", ""), roster)
        _rematch_roster(e, recheck)

    exam = store.update_exam(exam_id, fn)
    for rid in recheck:
        _jobs.put((exam_id, rid, "kimlik"))
    return annotate(exam)


# ---------------------------------------------------------------- kağıtlar


def _new_record(e: dict, pages: list[str], durum: str) -> dict:
    r = {"id": store.new_id(), "durum": durum, "resimler": list(pages), "eklenme": time.time()}
    e["kayitlar"].append(r)
    return r


def _session_info(e: dict, sid: str) -> dict:
    st = (e.get("oturumlar") or {}).get(sid) or {"acik": None, "kayitlar": []}
    open_rec = next((x for x in e["kayitlar"] if x["id"] == st.get("acik")), None)
    alive = [rid for rid in st.get("kayitlar", []) if any(x["id"] == rid for x in e["kayitlar"])]
    return {
        "ogrenci": len(alive),
        "acik_sayfa": len(open_rec["resimler"]) if open_rec else 0,
        "sayfa_sayisi": max(1, int(e.get("sayfa_sayisi", 1))),
    }


def _ingest(exam_id: str, files: list[tuple[str, bytes]], mode: str, kayit_id: str, oturum: str) -> dict:
    """Yüklenen dosyaları kaydedip kağıtlara ayırır (iş parçacığında çalışır; sunucuyu bekletmez)."""
    exam0 = store.get_exam(exam_id)
    known = (exam0.get("ozetler") or {}).copy()
    # bir fotoğrafta birden çok kağıt (var olan kağıda tek sayfa eklerken bölünmez)
    sheets = 1 if kayit_id else int(exam0.get("fotodaki_kagit", 1) or 1)
    split_equal: list[str] = []
    accepted: list[tuple[str, list[str]]] = []  # (özet, sayfa adları)
    bad: list[str] = []
    skipped = 0
    seen: set[str] = set()
    for filename, data in files:
        digest = hashlib.sha1(data).hexdigest()
        if digest in known or digest in seen:
            skipped += 1
            continue
        seen.add(digest)
        try:
            pages = _save_pages(exam_id, data, filename, sheets, split_equal)
        except _BadFile as e:
            bad.append(str(e))
            continue
        if pages:
            accepted.append((digest, pages))
    if not accepted and not skipped:
        raise HTTPException(400, "; ".join(bad) if bad else "Fotoğraf yok")
    per0 = max(1, int(exam0.get("sayfa_sayisi", 1)))
    if mode == "sayfa" and not kayit_id and not oturum and per0 > 1 and (bad or skipped):
        # Sayfalar sırayla N'er N'er öğrencilere ayrılıyor: aradan bir dosya düşerse sonraki bütün öğrencilerin
        # sayfaları karışır (bir öğrencinin 1. sayfası ile ötekinin 2. sayfası aynı kağıt olur). Hiçbirini ekleme.
        for _d, pages in accepted:
            for name in pages:
                store.unlink(store.image_dir(exam_id) / name)
        raise HTTPException(400, f"{len(bad) + skipped} dosya okunamadı ya da zaten ekliydi. Sayfalar {per0}'er {per0}'er "
                                 "öğrencilere ayrıldığı için öğrenciler karışacaktı; hiçbir sayfa eklenmedi. O dosyaları çıkarıp "
                                 "yeniden gönderin" + ("; " + "; ".join(bad) if bad else ""))

    queued: list[str] = []
    info: dict = {}
    dropped: list[str] = []

    def fn(e):
        # aynı dosya bu arada (ör. telefonun yeniden denemesiyle) eklendiyse kilit altında yeniden kontrol et
        ozet = e.setdefault("ozetler", {})
        names: list[str] = []
        nonlocal skipped
        for digest, pages in accepted:
            if digest in ozet:
                if mode == "sayfa" and not kayit_id and not oturum and per0 > 1:
                    raise _Shift  # bu arada aynı dosya eklenmiş: gruplama kayardı
                dropped.extend(pages)
                skipped += 1
                continue
            ozet[digest] = list(pages)
            names.extend(pages)
        per = max(1, int(e.get("sayfa_sayisi", 1)))
        if oturum:
            st = e.setdefault("oturumlar", {}).setdefault(oturum, {"acik": None, "yigin": [], "kayitlar": []})
            st["son"] = time.time()
        if not names:
            if oturum:
                info.update(_session_info(e, oturum))
            return
        if kayit_id:
            r = store.find_record(e, kayit_id)
            r["resimler"] += names
            if r["durum"] == "eksik" and len(r["resimler"]) < per:
                return
            # okunmakta olan kağıda sayfa eklendiyse: okuma bitince kaydedilmez, yeni girdi baştan okur
            r.update(durum="bekliyor", hata="", eksik_sayfa=len(r["resimler"]) < per)
            queued.append(r["id"])
            return
        if oturum:
            photo_of = {n: d[:12] for d, pages in accepted for n in pages}
            for name in names:
                r = next((x for x in e["kayitlar"] if x["id"] == st["acik"]), None)
                if r is None or r["durum"] != "eksik":
                    r = _new_record(e, [], "eksik")
                    st["kayitlar"].append(r["id"])
                r["resimler"].append(name)
                # üçüncü öğe: sayfanın geldiği fotoğraf (bir fotoğraftaki bütün kağıtlar birlikte geri alınır)
                st["yigin"] = (st["yigin"] + [[r["id"], name, photo_of.get(name, "")]])[-100:]
                if len(r["resimler"]) >= per:
                    r["durum"] = "bekliyor"
                    st["acik"] = None
                    queued.append(r["id"])
                else:
                    st["acik"] = r["id"]
            info.update(_session_info(e, oturum))
            return
        if mode == "tek":
            groups = [names]
        elif mode == "sayfa":
            groups = [names[i:i + per] for i in range(0, len(names), per)]
        else:
            groups = [[n] for n in names]
        for g in groups:
            r = _new_record(e, g, "bekliyor")
            if len(g) < per:
                r["eksik_sayfa"] = True
            queued.append(r["id"])

    try:
        store.update_exam(exam_id, fn)
    except KeyError:
        dropped.extend(p for _, pages in accepted for p in pages)
        raise HTTPException(404, "Kağıt bulunamadı")
    except _Shift:
        dropped.extend(p for _, pages in accepted for p in pages)
        raise HTTPException(409, "Dosyalardan biri bu arada eklenmiş; öğrenciler karışmasın diye hiçbir sayfa eklenmedi. "
                                 "Yeniden gönderin.")
    finally:
        for name in dropped:
            store.unlink(store.image_dir(exam_id) / name)
    for rid in queued:
        _jobs.put((exam_id, rid))
    return {"kayitlar": queued, "sayfa": sum(len(p) for _, p in accepted) - len(dropped), "atlanan": skipped,
            "hatali": bad, "oturum": info, "esit_bolunen": split_equal, "fotodaki_kagit": sheets}


@app.post("/api/sinavlar/{exam_id}/kagit")
async def kagit_yukle(
    exam_id: str,
    dosyalar: list[UploadFile] = File(...),
    gruplama: str = Form(""),
    tek_ogrenci: bool = Form(False),
    kayit_id: str = Form(""),
    oturum: str = Form(""),
):
    """Fotoğraf ya da PDF yükler.

    gruplama: "sayfa" → sınavın sayfa sayısına göre sırayla grupla (2 sayfaysa her 2 sayfa bir öğrenci),
              "ayri" → her sayfa ayrı öğrenci, "tek" → hepsi tek öğrencinin sayfaları.
    kayit_id: var olan kağıda sayfa ekle.
    oturum:   telefonun seri çekim oturumu; ayrı ayrı gelen fotoğraflar sayfa sayısına göre birleştirilir.
    """
    exam = _exam_or_404(exam_id)
    if kayit_id and not any(r["id"] == kayit_id for r in exam["kayitlar"]):
        raise HTTPException(404, "Kağıt bulunamadı")
    if oturum and not re.fullmatch(r"[\w-]{4,64}", oturum):
        raise HTTPException(400, "Geçersiz oturum")
    mode = gruplama or ("tek" if tek_ogrenci else "ayri")
    if mode not in ("sayfa", "ayri", "tek"):
        raise HTTPException(400, "Geçersiz gruplama")
    files = [(f.filename or "", await f.read()) for f in dosyalar]
    return await run_in_threadpool(_ingest, exam_id, files, mode, kayit_id, oturum)


@app.get("/api/sinavlar/{exam_id}/oturum/{sid}")
def oturum_durumu(exam_id: str, sid: str):
    return _session_info(_exam_or_404(exam_id), sid)


@app.post("/api/sinavlar/{exam_id}/oturum/{sid}/geri-al")
def oturum_geri_al(exam_id: str, sid: str):
    """Seri çekimde son çekilen fotoğrafı geri al (okunmaya başlamış olsa bile; okuma sonucu atılır).
    Fotoğrafta birden çok kağıt varsa hepsi birlikte geri alınır."""
    _exam_or_404(exam_id)
    removed: list[str] = []

    def fn(e):
        st = (e.get("oturumlar") or {}).get(sid)
        if not st or not st["yigin"]:
            raise HTTPException(400, "Geri alınacak çekim yok")
        stack = st["yigin"]
        photo = stack[-1][2] if len(stack[-1]) > 2 else ""
        take = 1
        while photo and take < len(stack) and len(stack[-take - 1]) > 2 and stack[-take - 1][2] == photo:
            take += 1
        entries = stack[-take:]
        for rid, _name, *_ in entries:
            r = next((x for x in e["kayitlar"] if x["id"] == rid), None)
            if r is not None and r["durum"] not in ("eksik", "bekliyor", "isleniyor"):
                raise HTTPException(409, "Bu kağıt okundu; gerekirse tablodan silin.")
        del stack[-take:]
        for rid, name, *_ in reversed(entries):
            removed.append(name)
            r = next((x for x in e["kayitlar"] if x["id"] == rid), None)
            if r is None:
                continue
            if name in r["resimler"]:
                r["resimler"].remove(name)
            if not r["resimler"]:
                e["kayitlar"] = [x for x in e["kayitlar"] if x["id"] != rid]
                st["acik"] = None
            else:
                r["durum"] = "eksik"  # kuyruktaki/okunan girdi, durum 'bekliyor/isleniyor' olmadığı için kaydedilmez
                r["ilerleme"] = ""
                st["acik"] = rid
        _forget_images(e, removed)

    exam = store.update_exam(exam_id, fn)
    for name in removed:
        store.unlink(store.image_dir(exam_id) / name)
    return _session_info(exam, sid)


@app.post("/api/sinavlar/{exam_id}/oturum/{sid}/bitir")
def oturum_bitir(exam_id: str, sid: str):
    """Seri çekim bitti: sayfası eksik kalan son kağıdı da okumaya gönder."""
    _exam_or_404(exam_id)
    queued: list[str] = []

    def fn(e):
        st = (e.get("oturumlar") or {}).get(sid)
        if not st:
            return
        r = next((x for x in e["kayitlar"] if x["id"] == st.get("acik")), None)
        if r is not None and r["durum"] == "eksik":
            r.update(durum="bekliyor", eksik_sayfa=True)
            queued.append(r["id"])
        st["acik"] = None

    exam = store.update_exam(exam_id, fn)
    for rid in queued:
        _jobs.put((exam_id, rid))
    return _session_info(exam, sid)


@app.post("/api/sinavlar/{exam_id}/kagit/{rec_id}/tamamla")
def kagit_tamamla(exam_id: str, rec_id: str):
    """Diğer sayfaları beklenen kağıdı, eldeki sayfalarla şimdi oku."""
    _exam_or_404(exam_id)

    def fn(e):
        r = next((x for x in e["kayitlar"] if x["id"] == rec_id), None)
        if r is None:
            raise HTTPException(404, "Kağıt bulunamadı")
        if r["durum"] != "eksik":
            raise HTTPException(400, "Bu kağıt sayfa beklemiyor")
        r.update(durum="bekliyor", eksik_sayfa=True)
        for st in (e.get("oturumlar") or {}).values():
            if st.get("acik") == rec_id:
                st["acik"] = None

    store.update_exam(exam_id, fn)
    _jobs.put((exam_id, rec_id))
    return {"ok": True}


class CellIn(BaseModel):
    soru: int  # 0'dan başlayan soru sırası
    deger: float | None


class RecordIn(BaseModel):
    ogrenci_no: str | None = None
    ad_soyad: str | None = None
    puanlar: list[float | None] | None = None  # (eski istemciler için) bütün satır
    puan: CellIn | None = None                 # yalnızca değişen hücre: eşzamanlı düzeltmeler birbirini ezmesin
    onaylandi: bool | None = None
    toplam_uyarisi_kapali: bool | None = None
    yazili_toplam: float | None = None
    yazili_toplam_sil: bool = False
    yalniz_uyarisiz: bool = False  # toplu onay: kağıt o an hâlâ uyarısızsa onayla


@app.patch("/api/sinavlar/{exam_id}/kagit/{rec_id}")
def kagit_duzenle(exam_id: str, rec_id: str, body: RecordIn):
    _exam_or_404(exam_id)
    edits = body.model_dump(exclude_defaults=True)

    def set_cell(r: dict, n: int, i: int, value: float | None) -> None:
        puanlar = (list(r.get("puanlar") or []) + [None] * n)[:n]
        if puanlar[i] == value:
            return
        puanlar[i] = value
        r["puanlar"] = puanlar
        # öğretmenin elle düzelttiği hücrenin kontrol uyarısı kalksın
        (r.get("supheli") or {}).pop(str(i), None)
        conf = list(r.get("eminlik_puan") or [])
        if i < len(conf):
            conf[i] = 1.0
            r["eminlik_puan"] = conf

    def fn(e):
        try:
            r = store.find_record(e, rec_id)
        except KeyError:
            raise HTTPException(404, "Kağıt bulunamadı")
        n = len(e["tam_puanlar"])
        if r.get("durum") != "tamam" and edits:
            # okunurken/okunmayı beklerken yapılan düzeltme, okuma bitince yeni sonuçla sessizce ezilirdi
            raise HTTPException(409, "Bu kağıt yeniden okunuyor; okuma bitince düzeltin.")
        if body.onaylandi and body.yalniz_uyarisiz:
            counts: dict[str, int] = {}
            for x in e["kayitlar"]:
                if x.get("durum") == "tamam" and x.get("ogrenci_no"):
                    counts[x["ogrenci_no"]] = counts.get(x["ogrenci_no"], 0) + 1
            row, cells = record_warnings(r, e, {k for k, c in counts.items() if c > 1})
            if row or cells:
                raise HTTPException(409, "Kağıt bu arada uyarı aldı; onaylanmadı.")
        if body.ogrenci_no is not None:
            no = re.sub(r"\D", "", body.ogrenci_no)
            r["ogrenci_no"] = no
            r["eslesme_notu"] = r["eslesme_bilgi"] = ""
            r["eminlik_kimlik"] = 1.0
            r["kimlik_elle"] = True  # öğretmen girdi: liste sonradan değişse de dokunulmaz
            match = next((s for s in e["ogrenci_listesi"] if s["no"] == no), None)
            if match and body.ad_soyad is None:
                r["ad_soyad"] = match["ad"]
        if body.ad_soyad is not None:
            r["ad_soyad"] = " ".join(body.ad_soyad.split())
            r["eslesme_notu"] = r["eslesme_bilgi"] = ""
            r["eminlik_kimlik"] = 1.0
            r["kimlik_elle"] = True
        if body.puan is not None:
            if not 0 <= body.puan.soru < n:
                raise HTTPException(400, "Geçersiz soru")
            set_cell(r, n, body.puan.soru, body.puan.deger)
        if body.puanlar is not None:
            new = (list(body.puanlar) + [None] * n)[:n]
            for i in range(n):
                set_cell(r, n, i, new[i])
        if body.onaylandi is not None:
            r["onaylandi"] = body.onaylandi
        if body.toplam_uyarisi_kapali is not None:
            r["toplam_uyarisi_kapali"] = body.toplam_uyarisi_kapali
        if body.yazili_toplam_sil:
            r["yazili_toplam"] = None
        elif body.yazili_toplam is not None:
            r["yazili_toplam"] = body.yazili_toplam
    return annotate(store.update_exam(exam_id, fn))


@app.post("/api/sinavlar/{exam_id}/kagit/{rec_id}/tekrar")
def kagit_tekrar(exam_id: str, rec_id: str):
    _exam_or_404(exam_id)

    def fn(e):
        try:
            r = store.find_record(e, rec_id)
        except KeyError:
            raise HTTPException(404, "Kağıt bulunamadı")
        if not r.get("resimler"):
            raise HTTPException(400, "Bu kağıdın fotoğrafı silinmiş; yeniden okunamaz.")
        if r["durum"] in ("bekliyor", "isleniyor"):
            return  # zaten sırada
        r.update(durum="bekliyor", hata="", motor_deneme=0)
    store.update_exam(exam_id, fn)
    _jobs.put((exam_id, rec_id))
    return {"ok": True}


@app.delete("/api/sinavlar/{exam_id}/kagit/{rec_id}")
def kagit_sil(exam_id: str, rec_id: str):
    _exam_or_404(exam_id)
    removed: list[str] = []

    def fn(e):
        keep = []
        for r in e["kayitlar"]:
            if r["id"] == rec_id:
                removed.extend(r["resimler"])
                removed.extend((r.get("kutu_resimleri") or {}).values())
            else:
                keep.append(r)
        e["kayitlar"] = keep
        _forget_images(e, removed)
    store.update_exam(exam_id, fn)
    for name in removed:
        store.unlink(store.image_dir(exam_id) / name)
    return {"ok": True}


@app.get("/api/sinavlar/{exam_id}/resim/{name}")
def resim(exam_id: str, name: str):
    if not re.fullmatch(r"[0-9a-f]{12}\.jpg", name):
        raise HTTPException(404)
    try:
        path = store.image_dir(exam_id) / name
    except KeyError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    # öğrenci fotoğrafları tarayıcı/WebView önbelleğinde kalmasın (KVKK: silinince gerçekten silinsin)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/sinavlar/{exam_id}/resimleri-sil")
def resimleri_sil(exam_id: str):
    """KVKK: puanlar kaldıktan sonra kağıt fotoğraflarını sil (şablon kalır)."""
    _exam_or_404(exam_id)
    removed: list[str] = []
    keep: set[str] = set()

    def fn(e):
        if any(r["durum"] in ("bekliyor", "isleniyor", "eksik") for r in e["kayitlar"]):
            raise HTTPException(400, "Okunmayı bekleyen kağıtlar var")
        for r in e["kayitlar"]:
            removed.extend(r.get("resimler") or [])
            removed.extend((r.get("kutu_resimleri") or {}).values())
            r["resimler"] = []
            r["kutu_resimleri"] = {}
        e["ozetler"] = {}
        keep.update((e.get("sablon") or {}).get("sayfalar", []))  # şablon, yeni kağıtlar için gerekli
    store.update_exam(exam_id, fn)
    for name in removed:
        store.unlink(store.image_dir(exam_id) / name)
    # kayda bağlanmamış eski artıklar (yarım kalmış yüklemeler); az önce yüklenmiş olabilecekleri koru
    for f in store.image_dir(exam_id).glob("*.jpg"):
        if f.name not in keep and time.time() - f.stat().st_mtime > 600:
            f.unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- şablon


class BoxIn(BaseModel):
    soru: int
    sayfa: int
    x: float
    y: float
    w: float
    h: float


class TemplateIn(BaseModel):
    sayfalar: list[str]  # şablon sayfası olarak kullanılacak fotoğrafların adları (bu sınavın fotoğrafları)
    kutular: list[BoxIn]


@app.post("/api/sinavlar/{exam_id}/sablon/foto")
async def sablon_foto(exam_id: str, dosya: UploadFile = File(...)):
    """Şablon için ayrı bir fotoğraf (tercihen boş, doldurulmamış kağıt) yükle."""
    _exam_or_404(exam_id)
    data = await dosya.read()
    try:
        names = await run_in_threadpool(_save_pages, exam_id, data, dosya.filename or "")
    except _BadFile as e:
        raise HTTPException(400, str(e))
    return {"resimler": names}


@app.put("/api/sinavlar/{exam_id}/sablon")
def sablon_kaydet(exam_id: str, body: TemplateIn):
    exam = _exam_or_404(exam_id)
    n = len(exam["tam_puanlar"])
    if not 1 <= len(body.sayfalar) <= 8:
        raise HTTPException(400, "Şablon 1-8 sayfa olabilir")
    for name in body.sayfalar:
        if not re.fullmatch(r"[0-9a-f]{12}\.jpg", name) or not (store.image_dir(exam_id) / name).exists():
            raise HTTPException(400, "Şablon fotoğrafı bulunamadı")
    boxes = []
    for b in body.kutular:
        if not (-1 <= b.soru <= n) or not (0 <= b.sayfa < len(body.sayfalar)):
            raise HTTPException(400, "Geçersiz kutu")
        if b.w <= 0.005 or b.h <= 0.005:
            continue
        boxes.append({"soru": b.soru, "sayfa": b.sayfa, "x": max(0.0, b.x), "y": max(0.0, b.y),
                      "w": min(1.0, b.w), "h": min(1.0, b.h)})
    by_q = {b["soru"]: b for b in boxes}  # her soru için en fazla bir kutu (sonuncusu geçerli)
    if any(q not in by_q for q in range(1, n + 1)):
        raise HTTPException(400, "Bütün soruların kutusu işaretlenmeli")

    # şablon sayfalarını öğrencinin kağıt fotoğrafından ayrı bir kopya olarak sakla: kağıt silinse de şablon kalsın
    def fn(e):
        old = (e.get("sablon") or {}).get("sayfalar", [])
        names = []
        for name in body.sayfalar:
            if name in old:
                names.append(name)
            else:
                new = store.new_id() + ".jpg"
                (store.image_dir(exam_id) / new).write_bytes((store.image_dir(exam_id) / name).read_bytes())
                names.append(new)
        for name in old:
            if name not in names:
                store.unlink(store.image_dir(exam_id) / name)
        e["sablon"] = {"sayfalar": names, "kutular": sorted(by_q.values(), key=lambda b: (b["soru"] <= 0, b["soru"]))}
    return annotate(store.update_exam(exam_id, fn))


@app.delete("/api/sinavlar/{exam_id}/sablon")
def sablon_sil(exam_id: str):
    exam = _exam_or_404(exam_id)
    names = (exam.get("sablon") or {}).get("sayfalar", [])

    def fn(e):
        e.pop("sablon", None)
    exam = store.update_exam(exam_id, fn)
    for name in names:
        store.unlink(store.image_dir(exam_id) / name)
    return annotate(exam)


@app.post("/api/sinavlar/{exam_id}/yeniden-oku")
def yeniden_oku(exam_id: str, sadece_hatali: bool = False):
    """Onaylanmamış bütün kağıtları (ör. şablon işaretlendikten sonra) ya da yalnızca hatalıları yeniden okut."""
    _exam_or_404(exam_id)
    queued: list[str] = []
    states = ("hata",) if sadece_hatali else ("tamam", "hata")

    def fn(e):
        for r in e["kayitlar"]:
            if r.get("durum") in states and not r.get("onaylandi") and r.get("resimler"):
                r.update(durum="bekliyor", hata="", motor_deneme=0)
                queued.append(r["id"])
    store.update_exam(exam_id, fn)
    for rid in queued:
        _jobs.put((exam_id, rid))
    return {"kayitlar": queued}


@app.get("/api/sinavlar/{exam_id}/excel")
def excel(exam_id: str):
    exam = annotate(_exam_or_404(exam_id), public=False)
    data = build_workbook(exam)
    base = "_".join(x for x in (exam.get("sinif"), exam.get("ders"), exam.get("ad")) if x) or "sinav"
    base = re.sub(r"[^\w\-]+", "_", base).strip("_")
    ascii_name = base.encode("ascii", "ignore").decode() or "sinav"
    from urllib.parse import quote
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}.xlsx\"; filename*=UTF-8''{quote(base)}.xlsx",
            "Cache-Control": "no-store",
        },
    )
