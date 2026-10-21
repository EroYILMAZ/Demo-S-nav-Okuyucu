"""Sınav kağıdı fotoğrafından öğrenci bilgisi ve soru puanlarını yerel model (Ollama) ile okur.

Hiçbir veri bilgisayardan dışarı gönderilmez: istekler yalnızca yerel Ollama sunucusuna gider.

Okuma adımları (gerçek kağıtlarla yapılan denemelere göre seçildi):
1. Her sayfa ayrı okunur. Tüm sayfaları tek seferde küçültüp göndermek, öğrenci karalamaları arasında
   puanların kaybolmasına ve modelin "hepsi 10" diye tahmin etmesine yol açıyordu.
2. İki sütunlu sayfalar (sol/sağ sütunda ayrı sorular) sütunlara bölünüp yakın planda okunur.
   Sütun düzeni, basılı metin satırlarının konumundan (yerel OCR) anlaşılır.
3. Model her sütunda gördüğü "soru numarası → öğretmenin puanı" çiftlerini listeler; sabit uzunlukta
   bir liste istemek, görmediği soruları uydurmasına neden oluyordu.
4. Ad, numara ve kağıdın toplamı yalnızca sayfanın üst kısmından ayrıca okunur; toplam yazılmamışsa
   -1 istenir (aksi hâlde model toplamı kendisi hesaplayıp "kağıtta yazıyor" gibi döndürüyordu).
5. "Yalnızca renkli kalem" ayarı açıkken (varsayılan) puanlar ve toplam yalnızca renkli tükenmez kalem izlerinden
   okunur (bkz. kalem.py); kurşun kalemle sonradan yazılan bir rakam okunmaz. Ad ve numara her kalemle okunur:
   öğrenciler adını çoğu zaman kurşun kalemle yazar ve ad bir puanı yükseltemez.
"""
import base64
import io
import json
import math
import re
import threading
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageOps

from . import kalem

MARKS_PROMPT = """This image is {what} of a graded Turkish exam paper.
Each question starts with a printed question number like "1-)", "5-)", "7)", "3.".
The TEACHER graded each question by writing the points earned as a handwritten number in pen
(often blue, red or green, often CIRCLED), next to / above / below the question number, or in the margin
on the same line, or in a score table ("Soru 1 2 3 ... / Puan ...").
List every question you can see here together with the teacher's score for it.
The printed maximum like "(10 puan)" is NOT the score. Ignore the student's calculations, numbers inside
figures and answer choices (A) B) C)...). If you see no question, return an empty list."""

MARKS_SCHEMA = {
    "type": "object",
    "properties": {
        "marks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "integer"}, "score": {"type": "number"}},
                "required": ["question", "score"],
            },
        }
    },
    "required": ["marks"],
}

PEN_NOTE = """
In this image only the COLORED pen marks (blue, red, green, purple) are shown in full strength; everything grey
(printed text, pencil writing, black marks) was deliberately faded. The teacher's scores are colored pen marks:
never report a faded grey or pencil number as a score."""

INK_LIST_PROMPT = """This image shows only the colored pen marks from {what} of a graded exam; everything else was erased.
List every handwritten number you can see in it (each occurrence once, in any order).
If there is no number, return an empty list."""

INK_LIST_SCHEMA = {
    "type": "object",
    "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
    "required": ["numbers"],
}

TOTAL_INK_PROMPT = """This image shows only the colored pen marks from the top part of a graded exam paper; everything else was erased.
The teacher may have written the total score of the paper here (often circled, near where "Puan" was printed).
Answer the total score. If no total score is visible, answer -1."""

RECHECK_NOTE = """
Read every score digit very carefully: a teacher's score is never larger than the question's maximum,
and in a score table each column belongs to the question number printed above it."""

HEADER_PROMPT ="""This is the top part of a Turkish exam paper. The student handwrote their name and surname
(a Turkish name, e.g. "Ayşe Yılmaz"), class (e.g. "10/C") and school number (usually 1-5 digits) here,
possibly without labels like "Adı Soyadı" / "No". Read them carefully letter by letter.
total_score: the overall score the teacher wrote for the whole paper (e.g. near "Puan"/"Toplam", often circled).
If no total score is written, use -1. Do not compute it yourself."""

HEADER_SCHEMA = {
    "type": "object",
    "properties": {
        "student_name": {"type": "string"},
        "student_number": {"type": "string"},
        "class_name": {"type": "string"},
        "total_score": {"type": "number"},
    },
    "required": ["student_name", "student_number", "class_name", "total_score"],
}

HEADER_FRACTION = 0.18  # sayfanın üstten bu kadarı başlık sayılır
COLUMN_OVERLAP = 0.06  # sütunlar ortada bu kadar örtüşür (ortadaki puan kesilmesin)


class RecognitionError(Exception):
    """Kağıt okunamadı (kalıcı): kağıt "hata" durumuna geçer."""


class EngineError(RecognitionError):
    """Okuma motoru şu an kullanılamıyor (Ollama kapalı, yeniden başlıyor, çöktü, zaman aşımı).
    Kağıdın kendisinde sorun yok: biraz sonra yeniden denenir."""


# ------------------------------------------------------------------ yardımcılar


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def encode(im: Image.Image, max_side: int, upscale: bool = False) -> str:
    im = im.copy()
    if upscale and max(im.size) < max_side:
        # küçük kırpımları büyüt: model rakamı daha çok görüntü parçasıyla görür
        k = max_side / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parse_number(value) -> float | None:
    """'7,5', '7.5', '7', '-3' gibi metinleri sayıya çevirir; okunamazsa None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


_ocr = None
_ocr_lock = threading.Lock()


def is_two_column(im: Image.Image) -> bool:
    """Sayfa iki sütunlu mu? Basılı metin satırları sağ yarıda da başlıyorsa evet."""
    global _ocr
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return False
    small = im.copy()
    small.thumbnail((1600, 1600))
    W, H = small.size
    with _ocr_lock:
        if _ocr is None:
            _ocr = RapidOCR()
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=90)
        result, _ = _ocr(buf.getvalue())
    left = right = 0
    for box, _text, conf in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        # başlık dışındaki, yeterince uzun ve güvenle okunan (basılı) satırlar
        if conf < 0.8 or min(ys) < 0.12 * H or max(xs) - min(xs) < 0.12 * W:
            continue
        if min(xs) > 0.5 * W:
            right += 1
        elif max(xs) < 0.5 * W:
            left += 1
    return right >= 3 and right >= 0.25 * (left + right)


# ------------------------------------------------------------------ eminlik (modelin kendi olasılıkları)
#
# Modelden "ne kadar eminsin" diye bir sayı istemek güvenilmez. Bunun yerine Ollama'dan, model her
# parçayı (Qwen'de her rakam ayrı bir parçadır) yazarken verdiği olasılığı ve en güçlü alternatifleri
# istiyoruz. Bir puanın eminliği = rakamlarının olasılıklarının çarpımı × "sayı burada bitiyor" olasılığı.
# Biçim parçaları (boşluk, tırnak) olasılığı bozmasın diye her rakam yalnızca rakam alternatiflerine
# göre normalleştirilir.

_NUMLIKE = re.compile(r"^\s*-?[\d.,]+\s*$")


def _p(lp) -> float:
    try:
        return math.exp(float(lp))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _tokens_with_offsets(content: str, logprobs: list | None) -> list[tuple[int, int, dict]]:
    """Her parçayı yanıt metnindeki konumuyla eşle."""
    out, pos = [], 0
    for t in logprobs or []:
        tok = t.get("token") or ""
        if not tok:
            continue
        at = content.find(tok, pos) if content[pos:pos + len(tok)] != tok else pos
        if at < 0 or at - pos > 8:
            at = pos  # hizalama kaydıysa sırayla devam et
        out.append((at, at + len(tok), t))
        pos = at + len(tok)
    return out


class Answer:
    """Model yanıtı + değerlerin eminliğini metindeki konumlarından hesaplama."""

    def __init__(self, data: dict, content: str, logprobs: list | None):
        self.data = data
        self.content = content
        self.toks = _tokens_with_offsets(content, logprobs)

    def _span_tokens(self, start: int, end: int):
        inside = [x for x in self.toks if x[0] < end and x[1] > start]
        after = next((x for x in self.toks if x[0] >= end), None)
        return inside, after

    def number_conf(self, start: int, end: int) -> tuple[float, str | None]:
        """[start, end) aralığındaki sayının eminliği (0-1) ve en güçlü alternatif okuma."""
        if not self.toks:
            return 0.97, None  # olasılık bilgisi yoksa (eski Ollama): her kağıdı gereksiz yere kontrole düşürmesin
        inside, after = self._span_tokens(start, end)
        if not inside:
            return 0.97, None
        conf, worst, alt = 1.0, 1.0, None
        text = self.content[start:end]
        for s, e, t in inside:
            chosen = t.get("token", "")
            p = _p(t.get("logprob"))
            alts = [a for a in t.get("top_logprobs") or [] if _NUMLIKE.match(a.get("token") or "")]
            mass = sum(_p(a.get("logprob")) for a in alts if a.get("token") != chosen) + p
            c = p / mass if mass > 0 else p
            conf *= c
            if c < worst:
                rivals = [a for a in alts if a.get("token") != chosen and (a.get("token") or "").strip()]
                if rivals:
                    best = max(rivals, key=lambda a: a.get("logprob", -99))
                    if _p(best.get("logprob")) > 0.02:
                        worst = c
                        rel_s, rel_e = max(0, s - start), min(len(text), e - start)
                        alt = text[:rel_s] + best["token"].strip() + text[rel_e:]
        if after is not None:
            # "7" mi "75" mi? sayıdan sonra bir rakam daha gelme olasılığı
            more = sum(_p(a.get("logprob")) for a in after[2].get("top_logprobs") or []
                       if re.match(r"^\s*\d", a.get("token") or "") and a.get("token") != after[2].get("token"))
            conf *= max(0.0, 1.0 - more)
        return max(0.0, min(1.0, conf)), alt

    def digit_options(self, start: int, end: int) -> list[dict[str, float]] | None:
        """Sayının her rakamı için {rakam: olasılık} (yalnızca rakam alternatiflerine göre normalleştirilmiş)."""
        inside, _ = self._span_tokens(start, end)
        if not inside or any(not re.fullmatch(r"\d", t.get("token", "")) for _s, _e, t in inside):
            return None  # rakam rakam parçalanmamış
        out = []
        for _s, _e, t in inside:
            opts = {t["token"]: _p(t.get("logprob"))}
            for a in t.get("top_logprobs") or []:
                tok = (a.get("token") or "").strip()
                if re.fullmatch(r"\d", tok) and tok not in opts:
                    opts[tok] = _p(a.get("logprob"))
            total = sum(opts.values()) or 1.0
            out.append({k: v / total for k, v in opts.items()})
        return out

    def text_conf(self, start: int, end: int) -> float:
        """Serbest metnin (isim) eminliği: parça olasılıklarının çarpımı."""
        if not self.toks:
            return 0.5
        inside, _ = self._span_tokens(start, end)
        conf = 1.0
        for _s, _e, t in inside:
            conf *= _p(t.get("logprob"))
        return conf if inside else 0.0


class _Ollama:
    def __init__(self, settings: dict):
        self.settings = settings
        self.url = settings["ollama_url"].rstrip("/") + "/api/chat"

    def ask(self, prompt: str, image: Image.Image, schema: dict, side: int | None = None, logprobs: bool = True) -> Answer:
        img = encode(image, side, upscale=True) if side else encode(image, self.settings["max_kenar"])
        payload = {
            "model": self.settings["model"],
            "messages": [{"role": "user", "content": prompt, "images": [img]}],
            "format": schema,
            "stream": False,
            "keep_alive": "30m",
            # num_ctx her çağrıda aynı olmalı; değişirse Ollama modeli her seferinde yeniden yükler
            "options": {"temperature": 0, "num_ctx": 8192},
        }
        if logprobs:
            payload.update(logprobs=True, top_logprobs=5)
        try:
            resp = httpx.post(self.url, json=payload, timeout=self.settings["zaman_asimi"])
        except httpx.ConnectError as e:
            raise EngineError("Okuma motoruna (Ollama) bağlanılamadı.") from e
        except httpx.TimeoutException as e:
            raise EngineError("Model zamanında yanıt vermedi (zaman aşımı). Ayarlardan süreyi artırabilirsiniz.") from e
        except httpx.HTTPError as e:  # Ollama istek ortasında çöktü vb.
            raise EngineError(f"Okuma motoruyla bağlantı koptu: {e}") from e
        if resp.status_code == 404:
            raise EngineError(f"'{self.settings['model']}' modeli yüklü değil. Komut: ollama pull {self.settings['model']}")
        if resp.status_code != 200:
            raise EngineError(f"Ollama hatası ({resp.status_code}): {resp.text[:200]}")
        try:
            body = resp.json()
            content = body["message"]["content"]
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("JSON nesnesi değil")
            return Answer(data, content, body.get("logprobs"))
        except (ValueError, KeyError, TypeError) as e:
            raise RecognitionError("Model geçerli bir yanıt üretmedi.") from e


_MARK_RE = re.compile(r'\{\s*"question"\s*:\s*(-?\d+)\s*,\s*"score"\s*:\s*(-?\d+(?:\.\d+)?)\s*\}')


def _marks_with_conf(ans: Answer) -> list[tuple[int, float, float, str | None]]:
    """[(soru, puan, eminlik, alternatif)] — soru numarasının eminliği de hesaba katılır (yanlış soruya yazma)."""
    out = []
    for m in _MARK_RE.finditer(ans.content):
        q, score = int(m.group(1)), float(m.group(2))
        qc, _ = ans.number_conf(m.start(1), m.end(1))
        sc, alt = ans.number_conf(m.start(2), m.end(2))
        out.append((q, score, qc * sc, alt))
    if not out:  # biçim beklenenden farklıysa ayrıştırılmış veriye dön (eminlik ölçülemez)
        for m in ans.data.get("marks") or []:
            try:
                q, score = int(m["question"]), parse_number(m["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if score is not None:
                out.append((q, score, 0.9, None))
    return out


def _header_values(ans: Answer) -> dict:
    """Başlıktaki numara, isim ve toplamın değerleri ve eminlikleri."""
    c = ans.content
    res = {"p_no": 0.0, "p_ad": 0.0, "p_toplam": 0.0}
    m = re.search(r'"student_number"\s*:\s*"([^"]*)"', c)
    if m and re.search(r"\d", m.group(1)):
        digits = [x for x in re.finditer(r"\d+", m.group(1))]
        s0, e0 = m.start(1) + digits[0].start(), m.start(1) + digits[-1].end()
        res["p_no"], _ = ans.number_conf(s0, e0)
    m = re.search(r'"student_name"\s*:\s*"([^"]*)"', c)
    if m and m.group(1).strip():
        res["p_ad"] = ans.text_conf(m.start(1), m.end(1))
    m = re.search(r'"total_score"\s*:\s*(-?\d+(?:\.\d+)?)', c)
    if m:
        res["p_toplam"], _ = ans.number_conf(m.start(1), m.end(1))
    return res


ROSTER_PROMPT = """This is the top part of a Turkish exam paper. The student handwrote their name and school number on it.
The student is most likely one of the students in this class list (number - name):
{liste}
Compare the handwriting carefully with the list (the handwriting may be messy, letters may look different).
Answer with the school number of the matching student from the list. If no student in the list fits, answer "yok"."""


def choose_from_roster(top: Image.Image, roster: list[dict], settings: dict) -> dict:
    """Belirsiz isim/numarayı sınıf listesinden seçtir. Dönüş: {"no": numara|None, "eminlik", "alternatif": numara|None}."""
    numbers = [s["no"] for s in roster][:300]
    schema = {"type": "object", "properties": {"student_number": {"type": "string", "enum": numbers + ["yok"]}},
              "required": ["student_number"]}
    liste = "\n".join(f"{s['no']} - {s['ad']}" for s in roster[:300])
    ans = _Ollama(settings).ask(ROSTER_PROMPT.format(liste=liste), top, schema)
    choice = str(ans.data.get("student_number") or "")
    if choice not in numbers:
        return {"no": None, "eminlik": 0.0, "alternatif": None}
    m = re.search(r'"student_number"\s*:\s*"(\d+)"', ans.content)
    conf, _ = ans.number_conf(m.start(1), m.end(1)) if m else (0.5, None)
    return {"no": choice, "eminlik": conf, "alternatif": _runner_up(ans, m, choice, numbers) if m else None}


def _runner_up(ans: "Answer", m, choice: str, numbers: list[str]) -> str | None:
    """Listedeki numaralardan, modelin seçtiğinden sonra en olası olanı (rakam rakam olasılıklardan)."""
    opts = ans.digit_options(m.start(1), m.end(1))
    if not opts or len(opts) != len(choice):
        return None
    best, best_p = None, 0.05
    for cand in numbers:
        if cand == choice or len(cand) != len(choice):
            continue
        k = next(i for i in range(len(cand)) if cand[i] != choice[i])
        p = 1.0
        for i in range(k):
            p *= opts[i].get(choice[i], 0.0)
        p *= opts[k].get(cand[k], 0.0)
        # ayrıldıktan sonraki rakamlar bilinmiyor: aynı öneki paylaşan adaylar arasında paylaştır
        p /= sum(1 for x in numbers if x != choice and len(x) == len(cand) and x[:k + 1] == cand[:k + 1])
        if p > best_p:
            best, best_p = cand, p
    return best


def header_crop(im: Image.Image) -> Image.Image:
    return im.crop((0, 0, im.width, int(im.height * HEADER_FRACTION)))


def pen_settings_on(settings: dict) -> bool:
    return bool(settings.get("yalnizca_renkli_kalem", True))


def needs_roster_choice(no: str, name: str, p_no: float, roster: list[dict]) -> bool:
    """Numara güvenle okunmuş, listede var ve isim de uyuyorsa listeye sormaya gerek yok."""
    from .matching import name_similarity
    if not roster:
        return False
    by_no = next((s for s in roster if s["no"] == no), None)
    return not (by_no and p_no >= 0.95 and name_similarity(name, by_no["ad"]) >= 0.6)


# ------------------------------------------------------------------ okuma


def recognize(images: list[Path], maxes: list[float], settings: dict, progress=None, layout: dict | None = None,
              roster: list[dict] | None = None) -> dict:
    """Bir öğrencinin kağıdını (bir veya birkaç sayfa) okur.

    layout: {"sayfa_indeksi": iki_sutunlu_mu}. Aynı sınavın bütün kağıtları aynı düzende olduğu için
    sınav başına bir kez algılanıp saklanır; her kağıtta yeniden OCR yapılmaz (sayfa başına ~10 sn kazanç).
    roster: sınıf listesi; numara/isim belirsizse model başlığı listeyle karşılaştırıp öğrenciyi seçer.

    Dönüş: {"ogrenci_no", "ad_soyad", "puanlar": [float|None]*n, "yazili_toplam": float|None,
            "supheli": {soru_indeksi: açıklama}, "eminlik": {...}, "liste_secimi": {...}|None,
            "duzen": layout, "ham": [...]}
    """
    layout = dict(layout or {})
    n = len(maxes)
    llm = _Ollama(settings)
    pen = pen_settings_on(settings)
    ink_seen: list[float] = []  # renkli kalemle yazılmış bütün sayılar (puanların sağlaması için)
    found: dict[int, list[tuple[float, float, str | None]]] = {}
    raw: list = []
    header: dict = {}
    hconf = {"p_no": 0.0, "p_ad": 0.0, "p_toplam": 0.0}
    choice = None

    pages = [load_image(p) for p in images]
    # puanlar, renksiz izleri soluklaştırılmış görüntüden okunur (basılı soru numaraları görünür kalır)
    pen_pages: list[Image.Image] = []
    views = pages
    if pen:  # süzgeç bütün sayfaya uygulanır (basılı çizgileri sayfa ölçeğinde tanır), sonra kırpılır
        both = [kalem.pen_and_faded(np.array(im)) for im in pages]
        pen_pages = [Image.fromarray(x) for x, _ in both]
        views = [Image.fromarray(y) for _, y in both]
    marks_prompt = MARKS_PROMPT + (PEN_NOTE if pen else "")
    for pi, im in enumerate(pages):
        if pi == 0:
            if progress:
                progress("Ad ve numara okunuyor…")
            top = header_crop(im)
            ans = llm.ask(HEADER_PROMPT, top, HEADER_SCHEMA)
            header, hconf = ans.data, _header_values(ans)
            raw.append({"baslik": header, "eminlik": hconf})
            if pen:
                # toplam yalnızca renkli kalemden: başlıktaki okuma kurşun kalemle yazılmış bir sayıyı da görebilir
                tans = llm.ask(TOTAL_INK_PROMPT, header_crop(pen_pages[0]), SCORE_SCHEMA)
                m = re.search(r'"score"\s*:\s*(-?\d+(?:\.\d+)?)', tans.content)
                header = dict(header, total_score=tans.data.get("score", -1))
                hconf["p_toplam"] = tans.number_conf(m.start(1), m.end(1))[0] if m else 0.0
                raw.append({"toplam_renkli": header["total_score"]})
            no0 = re.sub(r"\D", "", str(header.get("student_number") or ""))
            if roster and needs_roster_choice(no0, str(header.get("student_name") or ""), hconf["p_no"], roster):
                if progress:
                    progress("İsim sınıf listesiyle karşılaştırılıyor…")
                choice = choose_from_roster(top, roster, settings)
                raw.append({"liste_secimi": choice})

        key = str(pi)
        if key not in layout:
            layout[key] = is_two_column(im)
        view = views[pi]
        if layout[key]:
            w = im.width
            boxes = [("one column (the left half) of a page", (0, 0, int(w * (0.5 + COLUMN_OVERLAP)), im.height)),
                     ("one column (the right half) of a page", (int(w * (0.5 - COLUMN_OVERLAP)), 0, w, im.height))]
        else:
            boxes = [("one page", (0, 0, im.width, im.height))]

        for ci, (what, crop_box) in enumerate(boxes):
            part = view.crop(crop_box)
            if progress:
                where = f"sayfa {pi + 1}/{len(pages)}" + (f", sütun {ci + 1}/{len(boxes)}" if len(boxes) > 1 else "")
                progress(f"Puanlar okunuyor ({where})…")
            ans = llm.ask(marks_prompt.format(what=what), part, MARKS_SCHEMA)
            marks = _marks_with_conf(ans)
            raw.append({"sayfa": pi + 1, "parca": ci + 1, "puanlar": [m[:3] for m in marks]})
            if pen:
                # sağlama: yalnızca renkli kalem izlerinde hangi sayılar var?
                lans = llm.ask(INK_LIST_PROMPT.format(what=what), pen_pages[pi].crop(crop_box), INK_LIST_SCHEMA)
                nums = [v for v in (parse_number(x) for x in lans.data.get("numbers") or []) if v is not None]
                ink_seen.extend(nums)
                raw.append({"sayfa": pi + 1, "parca": ci + 1, "renkli_sayilar": nums})
            for q, score, conf, alt in marks:
                if 1 <= q <= n:
                    found.setdefault(q, []).append((score, conf, alt))

    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    alts: dict[str, str] = {}
    suspicious: dict[str, str] = {}
    for q, reads in found.items():
        values = [r[0] for r in reads]
        scores[q - 1] = values[0]
        if len(set(values)) > 1:
            suspicious[str(q - 1)] = "Farklı okumalar: " + " / ".join(f"{v:g}" for v in values)
            confs[q - 1] = min(0.5, reads[0][1])
        else:
            # aynı soru iki parçada da (sütun örtüşmesi) aynı okunduysa iki bağımsız kanıt
            miss = 1.0
            for r in reads:
                miss *= 1.0 - r[1]
            confs[q - 1] = 1.0 - miss
        if reads[0][2]:
            alts[str(q - 1)] = reads[0][2]

    total = parse_number(header.get("total_score"))
    # tek bir sorunun tam puanından küçük "toplam", büyük ihtimalle bir soru puanıdır
    if total is not None and (total < 0 or total <= max(maxes)):
        total = None

    # Kağıttaki toplam tutmuyorsa sayfaları bir kez daha, daha yüksek çözünürlükte ve dikkat uyarısıyla oku.
    # İkinci okumanın toplamı kağıttakiyle birebir tutarsa onu kullan; değişen hücreleri yine de işaretle.
    if total is not None and abs(sum(s or 0 for s in scores) - total) > 0.01:
        if progress:
            progress("Toplam tutmadı, kağıt yeniden kontrol ediliyor…")
        second: list[float | None] = [None] * n
        second_conf: list[float] = [0.0] * n
        recheck = dict(settings, max_kenar=int(settings["max_kenar"] * 1.3))
        llm2 = _Ollama(recheck)
        for pi, im in enumerate(views):
            ans = llm2.ask(marks_prompt.format(what="one page") + RECHECK_NOTE, im, MARKS_SCHEMA)
            marks = _marks_with_conf(ans)
            raw.append({"sayfa": pi + 1, "kontrol": [m[:3] for m in marks]})
            for q, score, conf, _alt in marks:
                if 1 <= q <= n and second[q - 1] is None:
                    second[q - 1], second_conf[q - 1] = score, conf
        if None not in second and abs(sum(second) - total) <= 0.01:
            for i in range(n):
                if second[i] != scores[i]:
                    first = "?" if scores[i] is None else f"{scores[i]:g}"
                    suspicious[str(i)] = f"İkinci okumada düzeltildi ({first} → {second[i]:g}), kontrol edin"
                    alts.pop(str(i), None)
            scores, confs = second, second_conf

    # Kağıttaki toplamla birebir tutuyorsa (ve toplam güvenle okunduysa) bütün puanlar birbirini doğrular:
    # tek bir yanlış puan toplamı bozardı.
    total_ok = total is not None and None not in scores and abs(sum(scores) - total) <= 0.01
    if total_ok and hconf["p_toplam"] >= 0.9:
        confs = [max(c, min(0.99, hconf["p_toplam"])) for c in confs]

    if pen:
        # renkli kalem izlerinde görülmeyen bir puan, kurşun kalemle yazılmış olabilir: kontrole düşür
        for i, v in enumerate(scores):
            if v is not None and not any(abs(v - x) < 0.01 for x in ink_seen):
                suspicious[str(i)] = f"{v:g} renkli kalem izlerinde görülmedi (kurşun kalemle yazılmış olabilir), kontrol edin"
                confs[i] = min(confs[i], 0.4)

    return {
        "ogrenci_no": re.sub(r"\D", "", str(header.get("student_number") or "")),
        "ad_soyad": " ".join(str(header.get("student_name") or "").split()),
        "puanlar": scores,
        "yazili_toplam": total,
        "supheli": suspicious,
        "eminlik": {
            "puan": [round(c, 4) for c in confs],
            "alternatif": alts,
            "no": round(hconf["p_no"], 4),
            "ad": round(hconf["p_ad"], 4),
            "toplam": round(hconf["p_toplam"], 4) if total is not None else None,
            "toplam_tutuyor": total_ok,
        },
        "liste_secimi": choice,
        "duzen": layout,
        "ham": raw,
    }


def identify(image: Path, roster: list[dict], settings: dict, template_pages: list[Path] | None = None,
             boxes: list[dict] | None = None) -> dict:
    """Sınıf listesi sonradan eklendiğinde: yalnızca başlığı listeyle karşılaştır (puanları yeniden okumadan).
    Şablonda Ad/No kutusu varsa, fotoğraf şablona oturtulup o kutu kullanılır."""
    im = load_image(image)
    id_box = next((b for b in boxes or [] if int(b["soru"]) == 0), None)
    if id_box and template_pages:
        from . import sablon
        box = sablon.Box.from_dict(id_box)
        aligner = sablon.Aligner([sablon.to_array(load_image(p)) for p in template_pages])
        pi, warped, _ = aligner.align(sablon.to_array(im), page_hint=box.sayfa)
        if pi == box.sayfa and warped is not None:
            return choose_from_roster(Image.fromarray(sablon.crop(warped, box, 0.08)), roster, settings)
    return choose_from_roster(header_crop(im), roster, settings)


# ------------------------------------------------------------------ şablonlu okuma (en doğru yöntem)

CROP_PROMPT = """This is a close-up crop of a graded exam paper.
The teacher wrote the points the student earned as a handwritten number, usually circled and usually in colored pen.
Read the teacher's number. If the teacher drew a circle, the score is the number inside the circle.
A small printed question label (like "2-)", "7)" or "Örnek 3") may also be visible: it is NOT the score.
Do not answer printed text or the student's calculations. If no teacher score is visible, answer -1.
Turkish handwriting: the digit 4 is often written open at the top, looking like "u" or "ч"; 7 may have a crossbar;
1 may be a single stroke; a score can also be a decimal like 7,5 (write it as 7.5)."""

INK_PROMPT = """This image shows only the colored pen marks from a crop of a graded exam; everything else was erased.
The teacher's handwritten score is among these marks (usually circled). Read the teacher's number.
If no number is visible, answer -1.
Turkish handwriting: the digit 4 is often written open at the top, looking like "u" or "ч"; 7 may have a crossbar;
1 may be a single stroke; a score can also be a decimal like 7,5 (write it as 7.5)."""

SCORE_SCHEMA = {"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"]}
CROP_SIDE = 768  # puan kutusu kırpımları bu boyuta büyütülüp gönderilir

# Okuma biçimleri (ad, kenar payı, yalnızca renkli mürekkep mi, oy ağırlığı).
# Normal kırpımda model bazen basılı soru etiketini ("8-)") puan sanıyor; renkli mürekkep görüntüsünde basılı yazı
# silindiği için bu hata olmuyor. Ama öğrenci de mavi kalem kullanmışsa mürekkep görüntüsü onun yazısını da tutar.
# Gerçek kağıtlarla ölçüldü: iki tür eşit ağırlıkla oylanınca 20 kutudan 19'u doğru (mürekkep 2 kat sayılınca 18).
_INK_FIRST = [("murekkep", 0.10, True, 1), ("murekkep_genis", 0.35, True, 1), ("genis", 0.25, False, 1), ("siki", 0.0, False, 1)]
_PLAIN_FIRST = [("genis", 0.25, False, 1), ("siki", 0.0, False, 1), ("orta", 0.12, False, 1)]
_EXTRA = [("daha_genis", 0.5, False, 1), ("murekkep_cok_genis", 0.6, True, 1), ("orta_2", 0.18, False, 1)]
# "Yalnızca renkli kalem" açıkken bütün bakışlar süzülmüş (yalnızca renkli mürekkep kalmış) sayfadan alınır
_PEN_FIRST = [("kalem", 0.10, True, 1), ("kalem_genis", 0.35, True, 1), ("kalem_siki", 0.0, True, 1), ("kalem_orta", 0.22, True, 1)]
_PEN_EXTRA = [("kalem_daha_genis", 0.5, True, 1), ("kalem_cok_genis", 0.65, True, 1), ("kalem_orta_2", 0.16, True, 1)]
NO_PEN_NOTE = ("Bu kutuda renkli kalemle yazılmış puan yok (puanlar siyah kalemle verildiyse Ayarlar'dan "
               "“yalnızca renkli tükenmez kalem” seçeneğini kapatın)")


def second_opinion(settings: dict, items: list[tuple], progress=None) -> dict:
    """Okunan puanları farklı bir modelle (ayarlardaki ikinci model) bir kez daha okur. items: [(anahtar, görüntü)].
    Dönüş: {anahtar: değer|None}. İkinci model yüklü değilse boş döner. Bütün kutular arka arkaya okunur: iki model
    her kutuda yer değiştirip belleğe yeniden yüklenmesin."""
    model = str(settings.get("ikinci_model") or "").strip()
    if not model or model == "kapali" or not items or model == settings.get("model"):
        return {}
    st = ollama_status(dict(settings, model=model))
    if not st.get("model_yuklu"):
        return {}
    llm2 = _Ollama(dict(settings, model=model))
    out = {}
    for i, (key, im) in enumerate(items):
        if progress:
            progress(f"İkinci model kontrol ediyor ({i + 1}/{len(items)})…")
        ans = llm2.ask(INK_PROMPT, im, SCORE_SCHEMA, side=CROP_SIDE, logprobs=False)
        v = parse_number(ans.data.get("score"))
        out[key] = None if v is None or v < 0 else v
    return out


def apply_second_opinion(scores: list, suspicious: dict, checks: dict, model: str) -> None:
    """İkinci modelin okuması birinciyle tutmayan hücreleri kontrole düşürür (değeri değiştirmez)."""
    for i, v2 in checks.items():
        v = scores[i]
        if v is not None and v2 != v:
            note = f"İkinci model ({model.split(':')[0]}) {'puan bulamadı' if v2 is None else f'{v2:g} okudu'}"
            suspicious[str(i)] = (suspicious[str(i)] + "; " + note) if suspicious.get(str(i)) else note


def _read_box(llm: "_Ollama", page, box, maxes, pen_page=None) -> dict:
    """Bir puan kutusunu birkaç biçimde okuyup ağırlıklı oylar. Dönüş: {"deger", "eminlik", "oylar", "not"}.
    pen_page: yalnızca renkli kalem izleri bırakılmış sayfa; verilirse bütün okumalar ondan yapılır."""
    from . import sablon

    reads: list[tuple[str, float | None, float, int]] = []
    if pen_page is not None:
        near = sablon.crop(pen_page, box, 0.35)
        if near.size == 0 or int((near < 245).any(axis=2).sum()) < 40:
            return {"deger": None, "eminlik": 0.0, "oylar": reads, "not": NO_PEN_NOTE}
        has_ink = True
    else:
        _ink, frac = sablon.teacher_ink(sablon.crop(page, box, 0.10))
        has_ink = frac >= 0.01  # renkli mürekkep var mı (öğretmen siyah kalem kullandıysa yok)

    def run(variants):
        for name, pad, ink, weight in variants:
            if ink and not has_ink:
                continue
            part = sablon.crop(pen_page if pen_page is not None else page, box, pad)
            if part.size == 0:
                continue
            if ink and pen_page is None:
                part, _ = sablon.teacher_ink(part)
            ans = llm.ask(INK_PROMPT if ink else CROP_PROMPT, Image.fromarray(part), SCORE_SCHEMA, side=CROP_SIDE)
            m = re.search(r'"score"\s*:\s*(-?\d+(?:\.\d+)?)', ans.content)
            val = parse_number(ans.data.get("score"))
            conf = ans.number_conf(m.start(1), m.end(1))[0] if m else 0.5
            reads.append((name, None if val is None or val < 0 else val, conf, weight))

    def tally():
        return sablon.weighted_vote([(r[1], r[3]) for r in reads], [r[2] for r in reads])

    if pen_page is not None:
        first, extra = _PEN_FIRST, _PEN_EXTRA
    else:
        first, extra = (_INK_FIRST if has_ink else _PLAIN_FIRST), _EXTRA
    run(first)
    value, top, total = tally()
    if total == 0 or top < total:
        run(extra)  # okumalar tam aynı değil: daha çok bakış
        value, top, total = tally()
    if value is None:
        return {"deger": None, "eminlik": 0.0, "oylar": reads, "not": "Bu kutuda öğretmen puanı bulunamadı"}
    probs = [r[2] for r in reads if r[1] == value]
    conf = (top / total) * (sum(probs) / len(probs))
    notes = []
    if top < total:  # bakışlardan biri bile farklı okuduysa öğretmen baksın
        notes.append("Okumalar farklı: " + " / ".join("—" if r[1] is None else f"{r[1]:g}" for r in reads))
    check = None
    if pen_page is not None:
        check = Image.fromarray(sablon.crop(pen_page, box, 0.22))
        if page is not None:
            # Çapraz kontrol: süzgeçsiz görüntüden bir okuma. Değer yine renkli kalemden alınır; tutmazsa öğretmen baksın:
            # ya kağıda kurşun kalemle bir şey eklenmiş ya da süzgeç silik/koyu mürekkebin bir kısmını silmiştir.
            part = sablon.crop(page, box, 0.25)
            if part.size:
                ans = llm.ask(CROP_PROMPT, Image.fromarray(part), SCORE_SCHEMA, side=CROP_SIDE)
                v = parse_number(ans.data.get("score"))
                v = None if v is None or v < 0 else v
                reads.append(("suzgecsiz", v, 0.0, 0))  # ağırlığı yok: yalnızca kontrol
                if v != value:
                    notes.append(f"Süzgeçsiz görüntüde {'puan yok' if v is None else f'{v:g}'} okundu "
                                 "(kurşun kalem eki ya da silik mürekkep olabilir)")
    return {"deger": value, "eminlik": conf, "oylar": reads, "not": "; ".join(notes), "kontrol": check}


def recognize_templated(images: list[Path], maxes: list[float], settings: dict, template_pages: list[Path],
                        boxes: list[dict], progress=None, roster: list[dict] | None = None) -> dict:
    """Şablonla okuma: fotoğraflar şablona hizalanır, her sorunun kutusu ayrı ayrı okunup oylanır.
    Dönüş, recognize() ile aynı biçimdedir."""
    from . import sablon

    n = len(maxes)
    llm = _Ollama(settings)
    raw: list = []
    bx = [sablon.Box.from_dict(b) for b in boxes]

    if progress:
        progress("Kağıt şablona hizalanıyor…")
    aligner = sablon.Aligner([sablon.to_array(load_image(p)) for p in template_pages])
    warped: dict[int, tuple] = {}
    unaligned: list[int] = []
    for i, path in enumerate(images):
        pi, w, inl = aligner.align(sablon.to_array(load_image(path)), page_hint=i)
        raw.append({"foto": i + 1, "sablon_sayfasi": None if pi is None else pi + 1, "eslesme": inl})
        if pi is None:
            unaligned.append(i + 1)
        elif pi not in warped or inl > warped[pi][1]:
            warped[pi] = (w, inl)

    pen = pen_settings_on(settings)
    pen_pages = {pi: kalem.pen_only(w)[0] for pi, (w, _inl) in warped.items()} if pen else {}

    if not warped:
        # hiçbir sayfa şablona oturmadı: başka bir baskı ya da grup olabilir → şablonsuz oku ve bunu belirt
        r = recognize_auto(images, maxes, settings, progress, roster=roster)
        r["hizalanamayan"] = list(range(1, len(images) + 1))
        r["ham"].insert(0, {"uyari": "Kağıt şablona oturtulamadı; şablonsuz okundu"})
        return r

    # öğrenci kimliği
    if progress:
        progress("Ad ve numara okunuyor…")
    id_box = next((b for b in bx if b.soru == 0), None)
    if id_box is not None and id_box.sayfa in warped:
        top = Image.fromarray(sablon.crop(warped[id_box.sayfa][0], id_box, 0.08))
    else:
        top = header_crop(load_image(images[0]))
    ans = llm.ask(HEADER_PROMPT, top, HEADER_SCHEMA)
    header, hconf = ans.data, _header_values(ans)
    raw.append({"baslik": header, "eminlik": hconf})
    choice = None
    no0 = re.sub(r"\D", "", str(header.get("student_number") or ""))
    if roster and needs_roster_choice(no0, str(header.get("student_name") or ""), hconf["p_no"], roster):
        if progress:
            progress("İsim sınıf listesiyle karşılaştırılıyor…")
        choice = choose_from_roster(top, roster, settings)
        raw.append({"liste_secimi": choice})

    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    suspicious: dict[str, str] = {}
    to_check: list[tuple] = []
    for q in range(1, n + 1):
        box = next((b for b in bx if b.soru == q), None)
        if box is None:
            suspicious[str(q - 1)] = "Şablonda bu sorunun kutusu yok"
            continue
        if box.sayfa not in warped:
            suspicious[str(q - 1)] = f"Şablonun {box.sayfa + 1}. sayfası bu kağıtta bulunamadı"
            continue
        if progress:
            progress(f"{q}. soru okunuyor ({q}/{n})…")
        r = _read_box(llm, warped[box.sayfa][0], box, maxes, pen_pages.get(box.sayfa))
        raw.append({"soru": q, "oylar": [(name, v, round(c, 3), w) for name, v, c, w in r["oylar"]]})
        scores[q - 1], confs[q - 1] = r["deger"], r["eminlik"]
        if r["not"]:
            suspicious[str(q - 1)] = r["not"]
        if r["deger"] is not None and r.get("kontrol") is not None:
            to_check.append((q - 1, r["kontrol"]))
    checks = second_opinion(settings, to_check, progress)
    if checks:
        raw.append({"ikinci_model": settings.get("ikinci_model"), "okumalar": {str(k + 1): v for k, v in checks.items()}})
        apply_second_opinion(scores, suspicious, checks, settings.get("ikinci_model", ""))

    # kağıdın toplamı: şablonda kutusu varsa oradan, yoksa başlıktan
    total, p_total = None, 0.0
    tot_box = next((b for b in bx if b.soru == -1), None)
    if tot_box is not None and tot_box.sayfa in warped:
        r = _read_box(llm, warped[tot_box.sayfa][0], tot_box, maxes, pen_pages.get(tot_box.sayfa))
        total, p_total = r["deger"], r["eminlik"]
    elif pen:
        # toplam yalnızca renkli kalemden (başlık okuması kurşun kalemle yazılmış bir sayıyı da görebilir)
        if id_box is not None and id_box.sayfa in pen_pages:
            top_pen = Image.fromarray(sablon.crop(pen_pages[id_box.sayfa], id_box, 0.08))
        else:
            top_pen = header_crop(Image.fromarray(kalem.pen_only(np.array(load_image(images[0])))[0]))
        tans = llm.ask(TOTAL_INK_PROMPT, top_pen, SCORE_SCHEMA)
        m = re.search(r'"score"\s*:\s*(-?\d+(?:\.\d+)?)', tans.content)
        total, p_total = parse_number(tans.data.get("score")), (tans.number_conf(m.start(1), m.end(1))[0] if m else 0.0)
        if total is not None and (total < 0 or total <= max(maxes)):
            total = None
    else:
        total, p_total = parse_number(header.get("total_score")), hconf["p_toplam"]
        if total is not None and (total < 0 or total <= max(maxes)):
            total = None
    total_ok = total is not None and None not in scores and abs(sum(scores) - total) <= 0.01
    if total_ok and p_total >= 0.9:
        confs = [max(c, min(0.99, p_total)) for c in confs]

    if unaligned:
        raw.append({"uyari": f"{', '.join(map(str, unaligned))}. fotoğraf şablona oturtulamadı"})
    return {
        "ogrenci_no": no0,
        "ad_soyad": " ".join(str(header.get("student_name") or "").split()),
        "puanlar": scores,
        "yazili_toplam": total,
        "supheli": suspicious,
        "eminlik": {"puan": [round(c, 4) for c in confs], "alternatif": {}, "no": round(hconf["p_no"], 4),
                    "ad": round(hconf["p_ad"], 4), "toplam": round(p_total, 4) if total is not None else None,
                    "toplam_tutuyor": total_ok},
        "liste_secimi": choice,
        "duzen": {},
        "hizalanamayan": unaligned,
        "ham": raw,
    }


# ------------------------------------------------------------------ şablonsuz: puan yerlerini kendiliğinden bulma
#
# Bütün sayfayı küçültüp tek seferde okumak gerçek kağıtta 10 puandan yalnızca 5'ini buldu. Şablonlu okuma ise 20/20.
# Fark: şablonda her puan yakından ve ayrı ayrı okunuyor. Şablon yokken puanların yeri renkli mürekkepten bulunur:
# kurşun kalem ve baskı silinince sayfada çoğunlukla yalnızca öğretmenin (yuvarlak içindeki) puanları kalır.
#   1. Kağıdın içindeki renkli mürekkep kümeleri (rakam + çevresindeki daire tek küme) aday olur.
#   2. Her aday şablondaki gibi birkaç biçimde yakından okunup oylanır; sayı olmayanlar (tik, çizgi) atılır.
#   3. Adayın yanındaki basılı soru numarası ayrıca okunur.
#   4. Adaylar okuma sırasına (sayfa, sütun, yukarıdan aşağı) dizilip sorulara bağlanır: soru numarası okumalarıyla en
#      çok uyuşan, sırayı bozmayan eşleştirme seçilir. Okunan numara ile sıra çelişirse hücre kontrole düşer.

LABEL_PROMPT = """This is a part of a graded Turkish exam paper. A red rectangle marks a score the teacher wrote (often circled).
Which question does this score belong to? Each question starts with a printed question number such as "5-)", "5)",
"5." or "Soru 5", usually just left of or above the score, at the start of the question text.
Answer that question number. If no question number is visible, answer -1."""

LABEL_SCHEMA = {"type": "object", "properties": {"question": {"type": "integer"}}, "required": ["question"]}

CAND_MIN_SIDE = 0.035   # aday kümenin uzun kenarı sayfa genişliğinin en az bu kadarı (puan dairesi ~%8-15)
CAND_MAX_SIDE = 0.30    # bundan büyük küme puan değil (öğrencinin renkli kalemle yazdığı uzun satırlar vb.)
CAND_MIN_INK = 0.0004   # kümedeki mürekkep pikseli sayfanın en az bu oranı


def find_score_marks(page: np.ndarray) -> tuple[list, np.ndarray]:
    """Sayfadaki öğretmen puanı adayları: [(x, y, w, h)] piksel ve yalnızca renkli mürekkep kalmış sayfa."""
    import cv2

    from .bolme import paper_region
    an = kalem.analyze(page)
    pen = an["pen"] & paper_region(page)
    only = np.full(page.shape, 255, np.uint8)
    only[pen] = (an["norm"][pen] * 255).astype(np.uint8)
    H, W = pen.shape
    k = max(3, int(0.012 * max(H, W)))  # rakamlar ve dairesi tek kümede birleşsin
    merged = cv2.dilate(pen.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    num, lab, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    ink = np.bincount(lab.ravel(), weights=pen.ravel().astype(np.float64), minlength=num)
    marks = []
    for i in range(1, num):
        x, y, w, h, _a = stats[i]
        side = max(w, h) / W
        if CAND_MIN_SIDE <= side <= CAND_MAX_SIDE and ink[i] >= CAND_MIN_INK * H * W:
            marks.append((int(x), int(y), int(w), int(h)))
    return marks, only


def _assign_questions(labels: list[int | None], n: int) -> list[int | None]:
    """Okuma sırasındaki adayları 1..n sorularına bağlar (sıra korunur). labels: her adayın yanında okunan soru
    numarası (yoksa None). Puanlama: okunan numarayla aynıysa +3, farklıysa -1; atlanan soru -0.5, bağlanamayan aday -2."""
    from functools import lru_cache
    m = len(labels)

    @lru_cache(maxsize=None)
    def best(i: int, q: int) -> tuple[float, tuple]:
        if i == m:
            return 0.0, ()
        skip_s, skip_rest = best(i + 1, q)
        top = (skip_s - 2.0, (None,) + skip_rest)
        for r in range(q + 1, n + 1):
            lab = labels[i]
            gain = 0.0 if lab is None else (3.0 if lab == r else -1.0)
            rest_s, rest = best(i + 1, r)
            cand = (gain - 0.5 * (r - q - 1) + rest_s, (r,) + rest)
            if cand[0] > top[0]:
                top = cand
        return top

    return list(best(0, 0)[1])


def recognize_auto(images: list[Path], maxes: list[float], settings: dict, progress=None, layout: dict | None = None,
                   roster: list[dict] | None = None) -> dict:
    """Şablonsuz okuma: puanların yerini renkli mürekkepten bulup her birini yakından okur.
    Renkli kalem süzgeci kapalıysa ya da puan bulunamazsa eski yönteme (bütün sayfayı okuma) döner.
    Dönüş, recognize() ile aynı biçimdedir."""
    from PIL import ImageDraw

    from . import sablon

    n = len(maxes)
    if not pen_settings_on(settings):
        return recognize(images, maxes, settings, progress, layout=layout, roster=roster)
    layout = dict(layout or {})
    llm = _Ollama(settings)
    raw: list = [{"yontem": "puan yerleri kendiliğinden bulundu"}]

    if progress:
        progress("Puanların yeri bulunuyor…")
    pages = [load_image(p) for p in images]
    found = []  # (sayfa, sütun, y, x, Box)
    pen_pages = []
    page_arrays = []
    for pi, im in enumerate(pages):
        a = np.array(im)
        page_arrays.append(a)
        marks, only = find_score_marks(a)
        pen_pages.append(only)
        key = str(pi)
        if key not in layout:
            layout[key] = is_two_column(im)
        H, W = a.shape[:2]
        for x, y, w, h in marks:
            col = int(layout[key] and x + w / 2 > W / 2)
            box = sablon.Box(0, pi, x / W, y / H, w / W, h / H)
            found.append((pi, col, y + h / 2, x, box))
    found.sort(key=lambda f: f[:4])
    raw.append({"adaylar": [[f[0] + 1, round(f[4].x, 3), round(f[4].y, 3)] for f in found]})
    if len(found) < max(1, n // 2) or len(found) > 4 * n:
        # renkli puan bulunamadı (siyah kalem, puan tablosu) ya da öğrenci de renkli kalem kullanmış: eski yöntem
        r = recognize(images, maxes, settings, progress, layout=layout, roster=roster)
        r["ham"].insert(0, {"uyari": f"Puan yerleri bulunamadı ({len(found)} aday); bütün sayfa okundu"})
        return r

    # öğrenci kimliği: ilk sayfanın üst kısmı (ad ve numara her kalemle okunur)
    if progress:
        progress("Ad ve numara okunuyor…")
    top = header_crop(pages[0])
    ans = llm.ask(HEADER_PROMPT, top, HEADER_SCHEMA)
    header, hconf = ans.data, _header_values(ans)
    raw.append({"baslik": header, "eminlik": hconf})
    no0 = re.sub(r"\D", "", str(header.get("student_number") or ""))
    choice = None
    if roster and needs_roster_choice(no0, str(header.get("student_name") or ""), hconf["p_no"], roster):
        if progress:
            progress("İsim sınıf listesiyle karşılaştırılıyor…")
        choice = choose_from_roster(top, roster, settings)
        raw.append({"liste_secimi": choice})

    # her adayı yakından oku; sayı olanların soru numarasını da oku
    reads = []  # (aday, okuma sonucu, okunan soru numarası)
    for ci, (pi, _col, _y, _x, box) in enumerate(found):
        if progress:
            progress(f"Puanlar okunuyor ({ci + 1}/{len(found)})…")
        r = _read_box(llm, page_arrays[pi], box, maxes, pen_pages[pi])
        if r["deger"] is None:
            raw.append({"aday": ci + 1, "oylar": [(nm, v) for nm, v, _c, _w in r["oylar"]], "sonuc": "puan değil"})
            continue
        im = pages[pi]
        W, H = im.size
        bx, by, bw, bh = box.x * W, box.y * H, box.w * W, box.h * H
        ctx = (int(max(0, bx - 1.3 * bw)), int(max(0, by - 0.9 * bh)), int(min(W, bx + 1.8 * bw)), int(min(H, by + 1.9 * bh)))
        crop = im.crop(ctx).copy()
        ImageDraw.Draw(crop).rectangle([bx - ctx[0], by - ctx[1], bx + bw - ctx[0], by + bh - ctx[1]],
                                       outline=(230, 0, 0), width=max(3, int(0.004 * W)))
        lans = llm.ask(LABEL_PROMPT, crop, LABEL_SCHEMA, side=1024)
        lab = lans.data.get("question")
        lab = int(lab) if isinstance(lab, (int, float)) and 1 <= int(lab) <= n else None
        reads.append((box, r, lab))
        raw.append({"aday": ci + 1, "sayfa": pi + 1, "oylar": [(nm, v) for nm, v, _c, _w in r["oylar"]], "soru_no": lab})

    # kağıdın toplamı: tek bir sorunun tam puanından büyük okunan aday (en büyüğü)
    total, p_total = None, 0.0
    big = [t for t in reads if t[1]["deger"] > max(maxes)]
    if big:
        tb = max(big, key=lambda t: t[1]["deger"])
        total, p_total = tb[1]["deger"], tb[1]["eminlik"]
        reads = [t for t in reads if t is not tb]

    assign = _assign_questions([t[2] for t in reads], n)
    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    suspicious: dict[str, str] = {}
    extra = []
    to_check: list[tuple] = []
    for (box, r, lab), q in zip(reads, assign):
        if q is None:
            extra.append(f"{r['deger']:g}")
            continue
        i = q - 1
        scores[i], confs[i] = r["deger"], r["eminlik"]
        if r.get("kontrol") is not None:
            to_check.append((i, r["kontrol"]))
        notes = []
        if r["not"]:
            notes.append(r["not"])
        if lab is not None and lab != q:
            notes.append(f"Hangi soruya ait olduğu belirsiz (yanında {lab} numarası okundu)")
        if r["deger"] > maxes[i]:
            notes.append(f"Okunan puan ({r['deger']:g}) bu sorunun tam puanından büyük")
        if notes:
            suspicious[str(i)] = "; ".join(notes)
    checks = second_opinion(settings, to_check, progress)
    if checks:
        raw.append({"ikinci_model": settings.get("ikinci_model"), "okumalar": {str(k + 1): v for k, v in checks.items()}})
        apply_second_opinion(scores, suspicious, checks, settings.get("ikinci_model", ""))
    for i in range(n):
        if scores[i] is None:
            suspicious[str(i)] = "Bu sorunun puanı kağıtta bulunamadı"
    if extra:
        raw.append({"uyari": "Hangi soruya ait olduğu anlaşılamayan puanlar: " + ", ".join(extra)})
        first_missing = next((i for i in range(n) if scores[i] is None), None)
        if first_missing is not None:
            suspicious[str(first_missing)] += f" (kağıtta sorusu belirlenemeyen puan var: {', '.join(extra)})"

    total_ok = total is not None and None not in scores and abs(sum(scores) - total) <= 0.01
    if total_ok and p_total >= 0.9:
        confs = [max(c, min(0.99, p_total)) for c in confs]
    return {
        "ogrenci_no": no0,
        "ad_soyad": " ".join(str(header.get("student_name") or "").split()),
        "puanlar": scores,
        "yazili_toplam": total,
        "supheli": suspicious,
        "eminlik": {"puan": [round(c, 4) for c in confs], "alternatif": {}, "no": round(hconf["p_no"], 4),
                    "ad": round(hconf["p_ad"], 4), "toplam": round(p_total, 4) if total is not None else None,
                    "toplam_tutuyor": total_ok},
        "liste_secimi": choice,
        "duzen": layout,
        "ham": raw,
    }


_status_cache: dict = {"t": 0.0, "key": None, "val": None}


def ollama_status(settings: dict) -> dict:
    """Ollama çalışıyor mu, seçili model yüklü mü? (Birkaç saniye önbelleklenir: her telefonun her yoklaması
    Ollama'ya gitmesin; Ollama kapalıyken Windows'un bağlantı denemesi saniyeler sürüp arayüzü bekletmesin.)"""
    import time as _t
    base = settings["ollama_url"].rstrip("/")
    key = (base, settings["model"])
    if _status_cache["key"] == key and _t.time() - _status_cache["t"] < 4:
        return _status_cache["val"]
    try:
        resp = httpx.get(base + "/api/tags", timeout=httpx.Timeout(2.0, connect=1.0))
        models = [m["name"] for m in resp.json().get("models", [])]
        wanted = settings["model"]
        val = {"calisiyor": True, "modeller": models,
               "model_yuklu": any(m == wanted or m == wanted + ":latest" for m in models)}
    except (httpx.HTTPError, ValueError, KeyError):
        val = {"calisiyor": False, "modeller": [], "model_yuklu": False}
    _status_cache.update(t=_t.time(), key=key, val=val)
    return val
