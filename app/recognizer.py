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
import time
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

# Örnek bir isim vermeyin: isim alanı boş bir kağıtta model örnekteki ismi ("Ayşe Yılmaz") yazdı.
HEADER_PROMPT = """This is the top part of a Turkish exam paper. Find what the STUDENT handwrote in the name and number area
(next to printed labels like "Adı Soyadı", "Adı", "Soyadı", "No", "Numara", "Sınıfı"; the labels may also be missing).
name_written: is a student's name really handwritten here? number_written: is a school number really handwritten here?
If a field is left empty, set its "written" value to false and answer an empty string: NEVER guess or invent a name or number.
Printed text (school name, lesson, exam title, teacher name, the labels themselves) is not the student's name.
A circled number in colored pen is the teacher's score for a question, not the school number.
Read the name letter by letter (Turkish letters ç ğ ı İ ö ş ü). student_number: only the digits of the school number
(usually 1-5 digits), never the class (like "8/C") or the date.
total_score: the overall score the teacher wrote for the whole paper (near "Puan"/"Toplam", often circled).
If no total score is written, use -1. Do not compute it yourself."""

# Alanların sırası önemli: model, ismi yazmadan önce kağıtta isim olup olmadığına karar verir.
HEADER_SCHEMA = {
    "type": "object",
    "properties": {
        "name_written": {"type": "boolean"},
        "student_name": {"type": "string"},
        "number_written": {"type": "boolean"},
        "student_number": {"type": "string"},
        "class_name": {"type": "string"},
        "total_score": {"type": "number"},
    },
    "required": ["name_written", "student_name", "number_written", "student_number", "class_name", "total_score"],
}

_LABEL_WORDS = re.compile(r"(?:ad[ıi]?|soyad[ıi]?|isim|no|numara|numaras[ıi]|s[ıi]n[ıi]f[ıi]?|[öo][ğg]renci)", re.IGNORECASE)


def clean_header(h: dict) -> dict:
    """Başlık okumasındaki uydurmaları ayıklar: model "yazılmamış" dediyse alan boş; yalnızca basılı etiketlerden
    ("ADI SOYADI:") oluşan isim boş."""
    h = dict(h)
    name = " ".join(str(h.get("student_name") or "").split())
    name = re.sub(r"^(?:ad[ıi]?\s*/?\s*(?:soyad[ıi]?)?|isim)\s*[:\-]\s*", "", name, flags=re.IGNORECASE)
    if h.get("name_written") is False or not re.sub(r"[\W_]", "", _LABEL_WORDS.sub("", name)):
        name = ""
    h["student_name"] = name
    no = str(h.get("student_number") or "")
    if h.get("number_written") is False:
        no = ""
    # sınıf ("8/C", "10-A") numaraya karışmasın: "8/C 368" → 368
    runs = re.findall(r"\d+", re.sub(r"\d{1,2}\s*[/\-.]\s*[A-Za-zÇĞİÖŞÜçğıöşü](?![A-Za-zÇĞİÖŞÜçğıöşü])", " ", no))
    h["student_number"] = runs[0] if len(runs) == 1 else "".join(re.findall(r"\d", no))
    return h


def number_is_score(no: str, values: list[float]) -> bool:
    """Okunan "öğrenci numarası" başlığın yanındaki bir puanla aynı mı? Numara alanı boş bir kağıtta model, başlığın hemen
    altındaki yuvarlak içindeki 1. soru puanını (13) numara diye okudu."""
    return bool(no) and len(no) <= 3 and any(v is not None and abs(int(no) - v) < 0.01 for v in values)

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
        im = ImageOps.exif_transpose(im)
        if im.mode.startswith("I;16") or im.mode in ("I", "F"):  # convert('RGB') 16 biti 255'te keser (bembeyaz sayfa)
            x = np.asarray(im, dtype=np.float64)
            top = 65535.0 if im.mode.startswith("I;16") or x.max() > 255 else 255.0
            im = Image.fromarray(np.clip(x * (255.0 / top) + 0.5, 0, 255).astype(np.uint8))
        return im.convert("RGB")


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
        # Model çalıştırıcısı çağrı ortasında çökebilir (zayıf ekran kartlarında sürücü sıfırlanması: "device lost").
        # Ollama onu bir sonraki istekte yeniden başlatır: birkaç saniye bekleyip aynı çağrıyı yinele, bütün kağıdı değil.
        for attempt in range(3):
            try:
                resp = httpx.post(self.url, json=payload, timeout=self.settings["zaman_asimi"])
            except httpx.ConnectError as e:
                raise EngineError("Okuma motoruna (Ollama) bağlanılamadı.") from e
            except httpx.TimeoutException as e:
                raise EngineError("Model zamanında yanıt vermedi (zaman aşımı). Ayarlardan süreyi artırabilirsiniz.") from e
            except httpx.HTTPError as e:  # Ollama istek ortasında çöktü vb.
                if attempt < 2:
                    time.sleep(5)
                    continue
                raise EngineError(f"Okuma motoruyla bağlantı koptu: {e}") from e
            if resp.status_code == 500 and attempt < 2 and "running the model" in resp.text:
                time.sleep(5)
                continue
            break
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


def pen_color(settings: dict) -> str | None:
    """Ayarlarda seçilen öğretmen kalemi rengi ("kirmizi", "mavi", "yesil") ya da None (otomatik: bütün renkli kalemler)."""
    c = settings.get("ogretmen_kalem_rengi")
    return c if c in kalem.FAMILIES else None


def teacher_color(page_arrays: list, analyses: list, n: int) -> tuple[str | None, dict]:
    """Öğrenci de renkli kalem kullandıysa öğretmenin kalem rengini seçer (otomatik ayarda). Bütün renkli izlerle
    bulunan puan adayı sayısı soru sayısına zaten yakınsa None (değişiklik yok). Değilse her renk ailesi ayrı denenir:
    aday sayısı soru sayısına en yakın (n/2..2n arası) ve bütün renklerden açıkça daha yakın olan aile seçilir.
    Öğrenci mavi, öğretmen kırmızı kalem kullandığında bütün renklerle sayfada yüzlerce aday çıkıyordu.
    Dönüş: (renk ya da None, {renk: aday sayısı} — tanılama için)."""
    def count(color):
        return sum(len(find_score_marks(a, an, color)[0]) for a, an in zip(page_arrays, analyses))
    base = count(None)
    counts = {"hepsi": base}
    # Renkli basılı şekiller, başlıklar, haritalar da aday üretir; onları model "sayı yok" diye eler. Aday sayısına bakarak
    # renk seçmek, sayfa yalnızca biraz kalabalıkken yanılıyordu (renkli basılı bir fen sayfasında öğretmenin mavi
    # puanları atılıp kırmızı şekiller seçildi; az sorulu sayfada öğrencinin mavisi seçildi). Bu yüzden yalnızca sayfa
    # çok kalabalıksa (soru sayısının üç katından çok aday, en az 3 soru) denenir. Kesin yol: Ayarlar'dan kalem rengi.
    if n < 3 or base <= 3 * n:
        return None, counts
    total = sum(int(an["pen"].sum()) for an in analyses) or 1
    fits = []
    for name, code in kalem.FAMILIES.items():
        share = sum(int((kalem.ink_family(an) == code).sum()) for an in analyses) / total
        if share < 0.03:
            continue
        c = count(name)
        counts[name] = c
        if 0.75 * n <= c <= 1.5 * n:
            fits.append((abs(c - n), name))
    fits.sort()
    # tek bir aile soru sayısına uyuyorsa o; iki aile eşit uyuyorsa hangisinin öğretmen olduğu belli değil
    if not fits or (len(fits) > 1 and fits[0][0] == fits[1][0]):
        return None, counts
    return fits[0][1], counts


def needs_roster_choice(no: str, name: str, p_no: float, roster: list[dict]) -> bool:
    """Numara güvenle okunmuş, listede var ve isim de uyuyorsa listeye sormaya gerek yok. Kağıtta ne isim ne numara
    yazılıysa sorulmaz: listeden seçim tahmin olurdu."""
    from .matching import name_similarity
    if not roster or (not no and not name.strip()):
        return False
    by_no = next((s for s in roster if s["no"] == no), None)
    if not (by_no and p_no >= 0.95 and name_similarity(name, by_no["ad"]) >= 0.6):
        return True
    # isim listedeki başka bir öğrenciyle açıkça daha iyi uyuşuyorsa listeye yine de sor
    sim = name_similarity(name, by_no["ad"])
    return any(s["no"] != no and name_similarity(name, s["ad"]) >= 0.85 and name_similarity(name, s["ad"]) - sim >= 0.05
               for s in roster)


# ------------------------------------------------------------------ okuma


def recognize(images: list[Path], maxes: list[float], settings: dict, progress=None, layout: dict | None = None,
              roster: list[dict] | None = None, pages: list | None = None, labels: list[str] | None = None) -> dict:
    """Bir öğrencinin kağıdını (bir veya birkaç sayfa) okur.

    layout: {"sayfa_indeksi": iki_sutunlu_mu}. Aynı sınavın bütün kağıtları aynı düzende olduğu için
    sınav başına bir kez algılanıp saklanır; her kağıtta yeniden OCR yapılmaz (sayfa başına ~10 sn kazanç).
    roster: sınıf listesi; numara/isim belirsizse model başlığı listeyle karşılaştırıp öğrenciyi seçer.

    Dönüş: {"ogrenci_no", "ad_soyad", "puanlar": [float|None]*n, "yazili_toplam": float|None,
            "supheli": {soru_indeksi: açıklama}, "eminlik": {...}, "liste_secimi": {...}|None,
            "duzen": layout, "ham": [...]}
    """
    from . import sorular
    layout = dict(layout or {})
    n = len(maxes)
    llm = _Ollama(settings)
    pen = pen_settings_on(settings)
    # model soru numarasını yazar ("question": 3); maddeli sınavda 3. sorunun okumaları sırayla 3a, 3b hücrelerine gider
    slot_q = [sorular.parse(x)[0] for x in (labels or [])]
    if len(slot_q) != n or None in slot_q:
        slot_q = list(range(1, n + 1))

    def cells_of(marks_list) -> list[tuple[int, float, float, str | None]]:
        used: dict[int, int] = {}
        out = []
        for q, score, conf, alt in marks_list:
            idx = [i for i, sq in enumerate(slot_q) if sq == q]
            k = used.get(q, 0)
            used[q] = k + 1
            if k < len(idx):
                out.append((idx[k], score, conf, alt))
        return out
    ink_seen: list[float] = []  # renkli kalemle yazılmış bütün sayılar (puanların sağlaması için)
    found: dict[int, list[tuple[float, float, str | None]]] = {}
    raw: list = []
    header: dict = {}
    hconf = {"p_no": 0.0, "p_ad": 0.0, "p_toplam": 0.0}
    choice = None

    pages = list(pages) if pages is not None else [load_image(p) for p in images]  # verilmişse yönü düzeltilmiş sayfalar
    # puanlar, renksiz izleri soluklaştırılmış görüntüden okunur (basılı soru numaraları görünür kalır)
    pen_pages: list[Image.Image] = []
    views = pages
    if pen:  # süzgeç bütün sayfaya uygulanır (basılı çizgileri sayfa ölçeğinde tanır), sonra kırpılır
        both = [kalem.pen_and_faded(np.array(im), color=pen_color(settings)) for im in pages]
        pen_pages = [Image.fromarray(x) for x, _ in both]
        views = [Image.fromarray(y) for _, y in both]
    marks_prompt = MARKS_PROMPT + (PEN_NOTE if pen else "")
    for pi, im in enumerate(pages):
        if pi == 0:
            if progress:
                progress("Ad ve numara okunuyor…")
            top = header_crop(im)
            ans = llm.ask(HEADER_PROMPT, top, HEADER_SCHEMA)
            header, hconf = clean_header(ans.data), _header_values(ans)
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
            for i, score, conf, alt in cells_of(marks):
                found.setdefault(i, []).append((score, conf, alt))

    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    alts: dict[str, str] = {}
    suspicious: dict[str, str] = {}
    for i, reads in found.items():
        values = [r[0] for r in reads]
        scores[i] = values[0]
        if len(set(values)) > 1:
            suspicious[str(i)] = "Farklı okumalar: " + " / ".join(f"{v:g}" for v in values)
            confs[i] = min(0.5, reads[0][1])
        else:
            # aynı soru iki parçada da (sütun örtüşmesi) aynı okunduysa iki bağımsız kanıt
            miss = 1.0
            for r in reads:
                miss *= 1.0 - r[1]
            confs[i] = 1.0 - miss
        if reads[0][2]:
            alts[str(i)] = reads[0][2]

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
            for i, score, conf, _alt in cells_of(marks):
                if second[i] is None:
                    second[i], second_conf[i] = score, conf
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

    # Bütün sayfa okumasında hiçbir şey puanın hangi soruya ait olduğunu doğrulamıyor (model komşu sorunun puanını
    # yazabilir; yer değiştirme toplamı da bozmaz). Bu yüzden okunan her hücre kontrole düşer.
    for i, v in enumerate(scores):
        if v is not None:
            note = "Bütün sayfa okundu; puanın hangi soruya ait olduğu doğrulanamadı"
            suspicious[str(i)] = (suspicious[str(i)] + "; " + note) if suspicious.get(str(i)) else note
        else:
            suspicious.setdefault(str(i), "Bu sorunun puanı kağıtta bulunamadı")

    return {
        "uyarilar": ["Puanlar bütün sayfadan okundu; her puanın sorusunu kontrol edin (şablon işaretlerseniz her puan "
                     "kendi kutusundan, doğrulanarak okunur)."],
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


# ------------------------------------------------------------------ toplu okuma (bir kağıdın bütün kutuları tek görüntüde)
#
# Her kutuyu ayrı ayrı okumak kağıt başına ~50 model çağrısı demekti (bu bilgisayarda ~30 dakika). Kutular harflerle
# tek görüntüye dizilip bir çağrıda okunur. Her bakışta kutuların yeri ve harfi değişir (konumdan kaynaklanan yanılgı
# tekrarlanmasın). Bakışlar tutmazsa yalnızca o kutular tek tek, yakından yeniden okunur.

GRID_PEN_PROMPT = """This image shows {n} separate boxes, each marked with a black letter label ({labels}).
Each box is a close-up of one score that a teacher handwrote on a graded exam; only the colored pen marks are kept,
everything else was erased. The score is usually circled. For every label, read the teacher's number in that box.
Read each box on its own. If a box shows no number, answer -1 for it.
Turkish handwriting: the digit 4 is often written open at the top, looking like "u" or "ч"; 7 may have a crossbar;
1 may be a single stroke; a score can also be a decimal like 7,5 (write it as 7.5). A "+" sign before a number is not part of it."""

GRID_ORIG_PROMPT = """This image shows {n} separate boxes, each marked with a black letter label ({labels}).
Each box is a close-up crop of a graded exam where the teacher handwrote a score (usually circled, in colored pen).
For every label, read the teacher's number in that box. A small printed question label like "2-)" is NOT the score;
ignore printed text and the student's calculations. If a box shows no teacher score, answer -1 for it.
Turkish handwriting: the digit 4 is often written open at the top, looking like "u" or "ч"; 7 may have a crossbar;
1 may be a single stroke. A "+" sign before a number is not part of it."""

GRID_CELL = 384  # 320 pikselde "+10" gibi küçük ayrıntılı puanlar "4" okunabiliyordu
GRID_COLS = 3
GRID_MAX = 6    # bir görüntüde en fazla bu kadar kutu (~1 megapiksel). Daha büyük görüntü, 3 GB'lık ekran kartında
                # görüntü kodlayıcısının belleğini aşıp sürücüyü çökertti ("device lost"); süre piksel sayısıyla
                # orantılı olduğu için bölmek okumayı uzatmaz.
GRID_VIEWS = [("kalem", 0.10, 1), ("kalem_genis", 0.35, 2), ("kalem_siki", 0.0, 3), ("kalem_orta", 0.22, 4)]
CLOSE_WEIGHT = 1.5  # tek tek yakından okuma (768 piksel) toplu bakıştan (kutu başına ~380 piksel) güvenilir

_font_cache: dict = {}


def _label_font(size: int):
    from PIL import ImageFont
    if size not in _font_cache:
        f = None
        for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        _font_cache[size] = f or ImageFont.load_default()
    return _font_cache[size]


def grid_image(crops: list, labels: list[str], cell: int = GRID_CELL, cols: int = GRID_COLS) -> Image.Image:
    """Kırpımları harf etiketli hücrelere dizer (siyah harf; renkli mürekkeple karışmaz)."""
    from PIL import ImageDraw
    cols = min(cols, len(crops))
    rows = (len(crops) + cols - 1) // cols
    head = cell // 7
    g = Image.new("RGB", (cols * cell, rows * (cell + head)), "white")
    d = ImageDraw.Draw(g)
    f = _label_font(int(head * 0.8))
    for i, (c, lab) in enumerate(zip(crops, labels)):
        x, y = (i % cols) * cell, (i // cols) * (cell + head)
        im = Image.fromarray(c) if isinstance(c, np.ndarray) else c.copy()
        if im.width and im.height:
            k = (cell - 16) / max(im.size)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
            g.paste(im, (x + (cell - im.width) // 2, y + head + (cell - im.height) // 2))
        d.rectangle([x + 2, y + 2, x + cell - 3, y + head + cell - 3], outline=(0, 0, 0), width=3)
        d.text((x + 10, y + 3), lab, fill=(0, 0, 0), font=f)
    return g


def ask_grid(llm: "_Ollama", crops: list, prompt: str, cell: int = GRID_CELL, cols: int = GRID_COLS) -> list:
    """Kırpımları (en fazla GRID_MAX'lık görüntülerle) toplu okur. Dönüş: her kırpım için sayı ya da None."""
    out: list = []
    for start in range(0, len(crops), GRID_MAX):
        out.extend(_ask_grid_part(llm, crops[start:start + GRID_MAX], prompt, cell, cols))
    return out


def _ask_grid_part(llm: "_Ollama", part: list, prompt: str, cell: int, cols: int) -> list:
    labels = [chr(ord("A") + i) for i in range(len(part))]
    schema = {"type": "object", "properties": {lab: {"type": "number"} for lab in labels}, "required": labels}
    img = grid_image(part, labels, cell, cols)
    try:
        ans = llm.ask(prompt.format(n=len(part), labels=", ".join(labels)), img, schema, side=max(img.size),
                      logprobs=False)
    except EngineError:
        if len(part) == 1:
            raise
        # motor bu görüntüde çöktüyse (zayıf ekran kartı) daha küçük görüntülerle dene
        half = (len(part) + 1) // 2
        return _ask_grid_part(llm, part[:half], prompt, cell, cols) + _ask_grid_part(llm, part[half:], prompt, cell, cols)
    vals = []
    for lab in labels:
        v = parse_number(ans.data.get(lab))
        vals.append(None if v is None or v < 0 else v)
    return vals


def crop_alone(img: np.ndarray, box, pad: float, others: list) -> np.ndarray:
    """Kutuyu kenar payıyla kırpar; komşu puan kutularının (others) bu kırpıma taşan kısmı beyaza boyanır. Geniş bir
    kırpım komşu sorunun puanını da içine alıyordu (gerçek kağıtta S2'nin bir bakışı üstteki S1'in 15'ini okudu)."""
    from . import sablon
    part = sablon.crop(img, box, pad).copy()
    if not others or part.size == 0:
        return part
    H, W = img.shape[:2]
    bx1, by1 = box.x * W, box.y * H
    bx2, by2 = bx1 + box.w * W, by1 + box.h * H
    px, py = (bx2 - bx1) * pad, (by2 - by1) * pad
    cx1, cy1 = int(max(0, bx1 - px)), int(max(0, by1 - py))
    for o in others:
        ox1, oy1 = o.x * W, o.y * H
        ox2, oy2 = ox1 + o.w * W, oy1 + o.h * H
        x1, y1 = int(max(ox1, cx1)), int(max(oy1, cy1))
        x2, y2 = int(min(ox2, cx1 + part.shape[1])), int(min(oy2, cy1 + part.shape[0]))
        if x2 <= x1 or y2 <= y1:
            continue
        region = np.zeros(part.shape[:2], bool)
        region[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = True
        # bu kutunun kendisiyle örtüşen kısım korunur
        mx1, my1 = int(max(0, bx1 - cx1)), int(max(0, by1 - cy1))
        mx2, my2 = int(min(part.shape[1], bx2 - cx1)), int(min(part.shape[0], by2 - cy1))
        region[my1:my2, mx1:mx2] = False
        part[region] = 255
    return part


def read_boxes(llm: "_Ollama", items: list[tuple], progress=None) -> list[dict]:
    """Bir kağıdın bütün puan kutularını toplu okur. items: [(asıl sayfa, renkli kalem sayfası, Box)].
    Dönüş, her kutu için _read_box ile aynı biçimde: {"deger", "eminlik", "oylar", "not", "kontrol"}."""
    import random

    from . import sablon

    n = len(items)
    votes: list[list] = [[] for _ in range(n)]
    results: list[dict | None] = [None] * n
    # aynı sayfadaki öteki kutular: kırpımlarda beyaza boyanır (komşunun puanı okunmasın)
    others = [[b2 for (p2, _q2, b2) in items if b2 is not box and b2.sayfa == box.sayfa and p2 is page]
              for (page, _pen, box) in items]

    def crop_of(i: int, which: int, pad: float) -> np.ndarray:
        return crop_alone(items[i][which], items[i][2], pad, others[i])

    active = []
    for i, (_page, pen, box) in enumerate(items):
        near = sablon.crop(pen, box, 0.35)
        if near.size == 0 or int((near < 245).any(axis=2).sum()) < 40:
            results[i] = {"deger": None, "eminlik": 0.0, "oylar": [], "not": NO_PEN_NOTE, "kontrol": None}
        else:
            active.append(i)
    if not active:
        return results

    for vi, (vname, pad, seed) in enumerate(GRID_VIEWS):
        if progress:
            progress(f"Puanlar okunuyor (bakış {vi + 1}/{len(GRID_VIEWS) + 1})…")
        order = list(active)
        random.Random(seed).shuffle(order)
        vals = ask_grid(llm, [crop_of(i, 1, pad) for i in order], GRID_PEN_PROMPT)
        for i, v in zip(order, vals):
            votes[i].append((vname, v, 0.0, 1.0))

    # bakışlar tutmayan kutular: tek tek, yakından ek okumalar (toplu görüntünün bağlamından bağımsız)
    disputed = [i for i in active if len({v for _n, v, _c, _w in votes[i]}) > 1]
    for k, i in enumerate(disputed):
        if progress:
            progress(f"Tutmayan puan yakından okunuyor ({k + 1}/{len(disputed)})…")
        for name, pad, _ink, _w in _PEN_EXTRA:
            part = crop_of(i, 1, pad)
            if part.size == 0:
                continue
            ans = llm.ask(INK_PROMPT, Image.fromarray(part), SCORE_SCHEMA, side=CROP_SIDE, logprobs=False)
            v = parse_number(ans.data.get("score"))
            votes[i].append((name, None if v is None or v < 0 else v, 0.0, CLOSE_WEIGHT))

    # çapraz kontrol: süzgeçsiz görüntüden bir toplu bakış (değer yine renkli kalemden alınır)
    if progress:
        progress(f"Puanlar okunuyor (bakış {len(GRID_VIEWS) + 1}/{len(GRID_VIEWS) + 1}, süzgeçsiz)…")
    plain = ask_grid(llm, [crop_of(i, 0, 0.25) for i in active], GRID_ORIG_PROMPT)

    for i, v_plain in zip(active, plain):
        value, top, total = sablon.weighted_vote([(v, w) for _n, v, _c, w in votes[i]])
        notes = []
        numeric = [(v, w) for _n, v, _c, w in votes[i] if v is not None]
        grid_numeric = sum(1 for n_, v, _c, _w in votes[i] if v is not None and n_ in {g[0] for g in GRID_VIEWS})
        if value is None and grid_numeric >= 2 and sum(w for _v, w in numeric) >= 0.5 * (total - sum(w for _v, w in numeric)):
            # Toplu bakışların çoğu bir sayı gördü, yakından okumalar görmedi (gerçek kağıtta "·3" puanı: 13/13/3/3, yakından
            # üç kez "sayı yok"). Puan atılırsa şablonsuz okumada sonraki puanlar bir soru kayabiliyordu: en çok okunan
            # sayıyla tutulur, hücre kontrole düşer.
            value, top, _t = sablon.weighted_vote(numeric)
            notes.append("Bakışların bir kısmı burada puan görmedi; puan olmayabilir")
        if top < total:
            notes.append("Okumalar farklı: " + " / ".join("—" if v is None else f"{v:g}" for _n, v, _c, _w in votes[i]))
        oylar = votes[i] + [("suzgecsiz", v_plain, 0.0, 0)]
        if value is None:
            # renkli kalemde sayı okunamadı; süzgeçsiz bakış bir şey okuduysa öğretmene ipucu olarak yaz (değeri yazma:
            # süzgeçsiz görüntü kurşun kalemi de görür, öğretmen bakıp karar vermeli)
            hint = f" (süzgeçsiz görüntüde {v_plain:g} okundu, kağıda bakın)" if v_plain is not None else ""
            seen = sorted({v for _n, v, _c, _w in votes[i] if v is not None})
            if seen:  # bakışların azı bir sayı gördü (silik ya da yarım kalmış puan olabilir)
                hint += f" (bazı bakışlarda {' / '.join(f'{v:g}' for v in seen)} okundu)"
            results[i] = {"deger": None, "eminlik": 0.0, "oylar": oylar, "not": "Bu kutuda öğretmen puanı bulunamadı" + hint,
                          "kontrol": None}
            continue
        if v_plain != value:
            notes.append(f"Süzgeçsiz görüntüde {'puan yok' if v_plain is None else f'{v_plain:g}'} okundu "
                         "(kurşun kalem eki ya da silik mürekkep olabilir)")
        results[i] = {"deger": value, "eminlik": top / total if total else 0.0, "oylar": oylar, "not": "; ".join(notes),
                      "kontrol": Image.fromarray(crop_of(i, 1, 0.22))}
    return results


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
    if progress:
        progress("İkinci model kontrol ediyor…")
    vals = ask_grid(llm2, [im for _k, im in items], GRID_PEN_PROMPT)
    return {key: v for (key, _im), v in zip(items, vals)}


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


def _box_cut_off(cover, pen_page, page, box) -> bool:
    """Puan kutusu fotoğrafın kenarında kesiliyor mu? Kutunun bir kısmı kadraj dışındaysa VE mürekkep kadraj kenarına
    değiyorsa evet: kesik bir rakam bütün bakışlarda aynı (yanlış) okunurdu. Kadraj dışına taşan kısım boş kenar
    boşluğuysa (rakam içeride) uyarı gerekmez."""
    import cv2

    from . import sablon
    if cover is None:
        return False
    c = sablon.crop(cover, box, 0.10)
    if c.size == 0 or c.mean() >= 0.97:
        return False
    src = pen_page if pen_page is not None else page
    ink = (sablon.crop(src, box, 0.10) < (245 if pen_page is not None else 110)).any(axis=2)
    edge = c & ~(cv2.erode(c.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0)
    # görüntünün kendi kenarı kadraj kenarı değildir (kutu kenarı); yalnızca kapsama sınırına bak
    edge[[0, -1], :] = False
    edge[:, [0, -1]] = False
    touch = edge & (cv2.dilate(ink.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    return int(touch.sum()) >= 6


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
            warped[pi] = (w, inl, aligner.last_cover)

    pen = pen_settings_on(settings)
    pen_pages = {pi: kalem.pen_only(w[0], pen_color(settings))[0] for pi, w in warped.items()} if pen else {}

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
    header, hconf = clean_header(ans.data), _header_values(ans)
    raw.append({"baslik": header, "eminlik": hconf})
    no0 = re.sub(r"\D", "", str(header.get("student_number") or ""))

    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    suspicious: dict[str, str] = {}
    to_check: list[tuple] = []
    readable = []
    for q in range(1, n + 1):
        box = next((b for b in bx if b.soru == q), None)
        if box is None:
            suspicious[str(q - 1)] = "Şablonda bu sorunun kutusu yok"
        elif box.sayfa not in warped:
            suspicious[str(q - 1)] = f"Şablonun {box.sayfa + 1}. sayfası bu kağıtta bulunamadı"
        else:
            readable.append((q, box))
    tot_box = next((b for b in bx if b.soru == -1), None)
    if tot_box is not None and tot_box.sayfa in warped:
        readable.append((-1, tot_box))
    batch = {}
    if pen and readable:
        rs = read_boxes(llm, [(warped[b.sayfa][0], pen_pages[b.sayfa], b) for _q, b in readable], progress)
        batch = {q: r for (q, _b), r in zip(readable, rs)}
    for q, box in readable:
        if q == -1:
            continue
        if q in batch:
            r = batch[q]
        else:
            if progress:
                progress(f"{q}. soru okunuyor ({q}/{n})…")
            r = _read_box(llm, warped[box.sayfa][0], box, maxes, pen_pages.get(box.sayfa))
        raw.append({"soru": q, "oylar": [(name, v, round(c, 3), w) for name, v, c, w in r["oylar"]]})
        scores[q - 1], confs[q - 1] = r["deger"], r["eminlik"]
        if r["not"]:
            suspicious[str(q - 1)] = r["not"]
        if _box_cut_off(warped[box.sayfa][2], pen_pages.get(box.sayfa), warped[box.sayfa][0], box):
            note = "Puan kutusu fotoğrafın kenarında kesiliyor (kağıt kırpılmış çekilmiş), kağıda bakın"
            suspicious[str(q - 1)] = (suspicious[str(q - 1)] + "; " + note) if suspicious.get(str(q - 1)) else note
        if r["deger"] is not None and r.get("kontrol") is not None:
            to_check.append((q - 1, r["kontrol"]))
    checks = second_opinion(settings, to_check, progress)
    if checks:
        raw.append({"ikinci_model": settings.get("ikinci_model"), "okumalar": {str(k + 1): v for k, v in checks.items()}})
        apply_second_opinion(scores, suspicious, checks, settings.get("ikinci_model", ""))

    # kağıdın toplamı: şablonda kutusu varsa oradan, yoksa başlıktan
    total, p_total = None, 0.0
    if tot_box is not None and tot_box.sayfa in warped:
        r = batch.get(-1) or _read_box(llm, warped[tot_box.sayfa][0], tot_box, maxes, pen_pages.get(tot_box.sayfa))
        total, p_total = r["deger"], r["eminlik"]
    elif pen:
        # toplam yalnızca renkli kalemden (başlık okuması kurşun kalemle yazılmış bir sayıyı da görebilir)
        if id_box is not None and id_box.sayfa in pen_pages:
            top_pen = Image.fromarray(sablon.crop(pen_pages[id_box.sayfa], id_box, 0.08))
        else:
            top_pen = header_crop(Image.fromarray(kalem.pen_only(np.array(load_image(images[0])), pen_color(settings))[0]))
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

    # öğrenci numarası diye okunan sayı, kimlik kırpımına giren bir puan kutusunun değeri mi?
    def near_id(b) -> bool:
        if id_box is not None and id_box.sayfa in warped:
            return (b.sayfa == id_box.sayfa and b.x < id_box.x + id_box.w and id_box.x < b.x + b.w
                    and b.y < id_box.y + id_box.h and id_box.y < b.y + b.h)
        return b.sayfa == 0 and b.y < HEADER_FRACTION
    near_vals = [scores[q - 1] for q, b in readable if q > 0 and near_id(b)]
    if tot_box is not None and near_id(tot_box):
        near_vals.append(total)
    if number_is_score(no0, near_vals):
        raw.append({"uyari": "numara bir puanla aynı", "numara": no0})
        no0 = ""
    choice = None
    if roster and needs_roster_choice(no0, str(header.get("student_name") or ""), hconf["p_no"], roster):
        if progress:
            progress("İsim sınıf listesiyle karşılaştırılıyor…")
        choice = choose_from_roster(top, roster, settings)
        raw.append({"liste_secimi": choice})

    if unaligned:
        raw.append({"uyari": f"{', '.join(map(str, unaligned))}. fotoğraf şablona oturtulamadı"})
    # kontrol ekranı için her sorunun kutusu (süzgeçsiz: öğretmen kağıtta ne yazdığını görsün)
    crops = {q - 1: Image.fromarray(sablon.crop(warped[b.sayfa][0], b, 0.25)) for q, b in readable if q > 0}
    return {
        "kutular": crops,
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
#   3. Adayın yanındaki basılı soru numarası yerel OCR ile aranır (printed_label). Modelden numara okutmak denendi:
#      10'da ~6'sı doğruydu ve sistematik olarak komşu sorunun numarasını okuyordu; OCR'ın bulduğu numaraların ise
#      hepsi doğruydu (yaklaşık yarısı bulunuyor).
#   4. Adaylar okuma sırasına (sayfa, sütun, satır, soldan sağa) dizilip sorulara bağlanır: basılı numaralarla en çok
#      uyuşan, sırayı bozmayan eşleştirme seçilir. Sıra her sayfada en az bir basılı numarayla doğrulanmazsa, ya da bir
#      numara sırayla çelişirse, numarasıyla doğrulanmamış her hücre kontrole düşer.

CAND_MIN_SIDE = 0.035   # aday kümenin uzun kenarı sayfa genişliğinin en az bu kadarı (puan dairesi ~%8-15)
CAND_MAX_SIDE = 0.30    # bundan büyük küme puan değil (öğrencinin renkli kalemle yazdığı uzun satırlar vb.)
CAND_MIN_INK = 0.0004   # kümedeki mürekkep pikseli sayfanın en az bu oranı


def find_score_marks(page: np.ndarray, an: dict | None = None, color: str | None = None) -> tuple[list, np.ndarray]:
    """Sayfadaki öğretmen puanı adayları: [(x, y, w, h)] piksel ve yalnızca renkli mürekkep kalmış sayfa.
    an: kalem.analyze sonucu (verilmezse hesaplanır); color: yalnızca bu renk ailesi ("kirmizi", "mavi", "yesil")."""
    import cv2

    from .bolme import paper_region
    an = an if an is not None else kalem.analyze(page)
    pen = kalem.family(an, color) & paper_region(page)
    only = np.full(page.shape, 255, np.uint8)
    only[pen] = (an["norm"][pen] * 255).astype(np.uint8)
    H, W = pen.shape
    # Renkli BASILI uzun düz çizgiler (pembe/mavi soru çerçevesi, tablo, ızgara) kümelemeden çıkarılır: öğretmenin dairesi
    # çerçeveye değince ikisi tek dev kümede birleşip "puan olamayacak kadar büyük" diye atılıyordu (renkli çerçeveli
    # yayınevi kağıdında 5 puanın hiçbiri bulunamadı). Siyah çizgileri kalem süzgeci zaten siler. Hafif eğik çizgi (fotoğraf
    # perspektifi) de yakalansın diye maske önce biraz kalınlaştırılır. El yazısı rakamın bu kadar uzun düz parçası olmaz.
    length = max(15, int(kalem.LINE_LENGTH * max(H, W)))
    thick = cv2.dilate(pen.astype(np.uint8), np.ones((5, 5), np.uint8))
    straight = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8)) | \
        cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    marks_mask = pen & ~(cv2.dilate(straight, np.ones((3, 3), np.uint8)) > 0)
    k = max(3, int(0.012 * max(H, W)))  # rakamlar ve dairesi tek kümede birleşsin
    merged = cv2.dilate(marks_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    num, lab, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    ink = np.bincount(lab.ravel(), weights=marks_mask.ravel().astype(np.float64), minlength=num)
    marks = []
    for i in range(1, num):
        x, y, w, h, _a = stats[i]
        side = max(w, h) / W
        if CAND_MIN_SIDE <= side <= CAND_MAX_SIDE and ink[i] >= CAND_MIN_INK * H * W:
            marks.append((int(x), int(y), int(w), int(h)))
    return marks, only


def _mark_at_edge(only: np.ndarray, box) -> bool:
    """Puan kümesinin mürekkebi fotoğrafın kenarına değiyor mu? Kağıt kırpılarak çekilmişse kenarda kesilen bir rakam
    ("10"un yalnızca "1"i) bütün bakışlarda aynı, yanlış okunurdu. (Şablonlu okumadaki _box_cut_off'un karşılığı.)"""
    H, W = only.shape[:2]
    x1, y1 = int(box.x * W), int(box.y * H)
    x2, y2 = int(round((box.x + box.w) * W)), int(round((box.y + box.h) * H))
    ink = (only[y1:y2, x1:x2] < 245).any(axis=2)
    m = 3  # kenara bu kadar piksel yakın mürekkep kenara değiyor sayılır
    touch = 0
    if y1 < m:
        touch += int(ink[:m - y1].sum())
    if y2 > H - m:
        touch += int(ink[max(0, H - m - y1):].sum())
    if x1 < m:
        touch += int(ink[:, :m - x1].sum())
    if x2 > W - m:
        touch += int(ink[:, max(0, W - m - x1):].sum())
    return touch >= 4


def page_rotation(im: Image.Image) -> int:
    """Sayfayı düz okunur yapan döndürme (derece, PIL rotate; 0/90/180/270). Basılı metin satırlarının yerel OCR'la
    okunabilirliğinden bulunur (açı sınıflandırıcısı kapalı: ters satır düz okunmasın). Kararsızsa 0.
    EXIF yönü yanlış kaydedilmiş fotoğrafta ya da tarayıcıya ters beslenmiş kağıtta puanlar ters sırayla sorulara
    bağlanır ve ters "6" bütün bakışlarda "9" okunurdu."""
    global _ocr
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return 0
    small = im.copy()
    small.thumbnail((1200, 1200))

    def score(deg: int) -> float:
        buf = io.BytesIO()
        small.rotate(deg, expand=True).save(buf, "JPEG", quality=90)
        with _ocr_lock:
            res, _ = _ocr(buf.getvalue(), use_cls=False)
        return sum(len(t) * c for _b, t, c in res or [] if c >= 0.85 and len(t.strip()) >= 3)

    with _ocr_lock:
        if _ocr is None:
            _ocr = RapidOCR()
    s0 = score(0)
    if s0 >= 120:  # bol, güvenle okunan düz metin: döndürmeye gerek yok
        return 0
    scores = {0: s0, **{d: score(d) for d in (180, 90, 270)}}
    best = max(scores, key=scores.get)
    return best if best != 0 and scores[best] >= max(60.0, 2 * s0) else 0


# "5-)", "5)", "5.", "5-", "Soru 5". Ardından rakam gelen nokta/tire numara değildir: soru metnindeki "1.5", "2-3"
# yanlış numara verip eşleştirmeyi kaydırabiliyordu.
_LABEL_RE = re.compile(r"(?:soru\s*(\d{1,2}))|(?:(?<![\d,.])(\d{1,2})\s*(?:\)|[-–.](?![\d,])))", re.IGNORECASE)
# madde harfi: "A.", "B-", "a)", "b)" (yalnızca sınavda maddeli soru varsa kullanılır: test şıkları da "A)" gibidir)
_PART_RE = re.compile(r"(?<![0-9A-Za-zÇĞİÖŞÜçğıöşü])([A-Ha-h])\s*[-.)]")
_ALNUM = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]")


def printed_label(im: Image.Image, pen_img: np.ndarray, box, n: int) -> tuple[int | None, str | None]:
    """Puan dairesinin yakınındaki BASILI soru numarası ("5-)", "5)", "5.", "Soru 5") ve madde harfi ("A.", "b)") —
    yerel OCR ile, büyütülmüş çevre kırpımında. Dönüş: (numara, küçük harf) — bulunamayan None.
    Renkli mürekkebin üstüne düşen metin (öğretmenin el yazısı "10" dairesiyle "10)" okunabilir) sayılmaz. Gerçek
    kağıtlarda bulunan numaraların hepsi doğruydu (yaklaşık yarısı bulunuyor; daire altında kalanlar bulunamıyor).
    Numara sınavın soru sayısından büyük de olabilir: kağıttaki numaralandırma sınavdakinden farklı demektir."""
    global _ocr
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None, None
    W, H = im.size
    x, y, w, h = box.x * W, box.y * H, box.w * W, box.h * H
    ctx = (int(max(0, x - 1.2 * w)), int(max(0, y - 0.8 * h)), int(min(W, x + 1.25 * w)), int(min(H, y + 1.25 * h)))
    if ctx[2] - ctx[0] < 8 or ctx[3] - ctx[1] < 8:
        return None, None
    crop = im.crop(ctx)
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=92)
    with _ocr_lock:
        if _ocr is None:
            _ocr = RapidOCR()
        res, _ = _ocr(buf.getvalue(), use_cls=False)
    pen = (pen_img < 245).any(axis=2)
    gray = np.asarray(im.convert("L"), dtype=np.int16)
    best = part = None
    for pts, text, conf in res or []:
        if conf < 0.6:
            continue
        xs = [q[0] / 2 + ctx[0] for q in pts]
        ys = [q[1] / 2 + ctx[1] for q in pts]
        x1, x2, y1, y2 = int(max(0, min(xs))), int(min(W, max(xs))), int(max(0, min(ys))), int(min(H, max(ys)))
        if x2 <= x1 or y2 <= y1:
            continue
        # Basılı mı, el yazısı mı? Kutudaki koyu izlerin çoğu renksizse basılı (üstünden öğretmenin dairesi geçebilir);
        # çoğu renkli mürekkepse el yazısıdır (öğretmenin dairesiyle "10)" okunabilen puanı): sayılmaz.
        g = gray[y1:y2, x1:x2]
        p = pen[y1:y2, x1:x2]
        dark = g < np.median(g) - 45
        printed = dark & ~p
        if printed.sum() < 0.5 * max(1, (dark | p).sum()):
            continue
        # Yalnızca metin parçasının BAŞINDAKİ numara ve madde harfi: soru numarası satır başında durur; satır içindeki
        # sayılar ("8. sınıf", "x = 19.") numara değildir (sentetik matematik sayfasında 8, 12, 19 numara sanıldı).
        # Kırpımın sol kenarında kesilmiş satır parçasının başı gerçek satır başı değildir.
        if ctx[0] > 0 and min(q[0] for q in pts) <= 3:
            continue
        d = abs(x1 - x) + abs(y1 - y)
        rest = text
        m = _LABEL_RE.search(text)
        if m and not _ALNUM.search(text[:m.start()]):
            rest = text[m.end():]
            k = int(m.group(1) or m.group(2))
            if 1 <= k <= 99 and (best is None or d < best[0]):
                best = (d, k)
        m = _PART_RE.search(rest)
        if m and not _ALNUM.search(rest[:m.start()]) and (part is None or d < part[0]):
            part = (d, m.group(1).lower())
    return (best[1] if best else None), (part[1] if part else None)


def _assign_questions(labels: list, n: int, strong: list[int | None] | None = None,
                      slots: list[tuple[int, str | None]] | None = None) -> list[int | None]:
    """Okuma sırasındaki adayları 1..n puan hücrelerine bağlar (sıra korunur). Dönüş: her aday için hücre (1..n) ya da
    None. slots: her hücrenin kağıttaki (soru numarası, madde harfi) — verilmezse (1, None)..(n, None).
    strong: adayın yanında yerel OCR ile bulunan basılı soru numarası (güçlü kanıt); labels: madde harfi ("a", "b";
    zayıf kanıt, yalnızca maddeli hücrelerde sayılır). Okuma sırası da kanıttır (adaylar doğrulanmış öğretmen
    puanları, soruların çoğunun puanı vardır).
    Puanlama: güçlü numara tutarsa +4, tutmazsa -4; madde harfi +1 / -0.5; atlanan hücre -2; bağlanamayan aday -4."""
    from functools import lru_cache
    m = len(labels)
    strong = list(strong) if strong is not None else [None] * m
    slots = list(slots) if slots is not None else [(i + 1, None) for i in range(n)]

    @lru_cache(maxsize=None)
    def best(i: int, q: int) -> tuple[float, tuple]:
        if i == m:
            return 0.0, ()
        skip_s, skip_rest = best(i + 1, q)
        top = (skip_s - 4.0, (None,) + skip_rest)
        for r in range(q + 1, n + 1):
            gain = 0.0
            sq, part = slots[r - 1]  # (q'yu ezmesin: q, önceki adayın bağlandığı hücre)
            if strong[i] is not None:
                gain += 4.0 if strong[i] == sq else -4.0
            if labels[i] is not None and part is not None:
                gain += 1.0 if labels[i] == part else -0.5
            rest_s, rest = best(i + 1, r)
            cand = (gain - 2.0 * (r - q - 1) + rest_s, (r,) + rest)
            if cand[0] > top[0]:
                top = cand
        return top

    return list(best(0, 0)[1])


def _infer_labels(found: list[tuple]) -> list[str] | None:
    """Puanların yanında okunan basılı (numara, madde harfi) çiftlerinden kağıdın soru numaralarını çıkarır: öğretmene
    "numaraları 1, 2, 3a, 3b … diye yazın" önerisi için (okumayı değiştirmez). Numarası bulunamayan bir puan yalnızca
    iki komşusu arasında tek bir boşluk kalıyorsa doldurulur (2 ile 4 arası 3; 3a ile 3c arası 3b); yoksa öneri yok."""
    labs: list[tuple[int, str | None] | None] = []
    cur = None
    for num, part in found:
        if num is not None:
            cur = num
            labs.append((num, part))
        elif part is not None and cur is not None:
            labs.append((cur, part))
        else:
            labs.append(None)
    for i, lab in enumerate(labs):
        if lab is not None:
            continue
        prev = labs[i - 1] if i > 0 else (0, None)
        nxt = labs[i + 1] if i + 1 < len(labs) else None
        if prev is None or nxt is None:
            return None
        if prev[1] is None and nxt[1] is None and nxt[0] == prev[0] + 2:
            labs[i] = (prev[0] + 1, None)
        elif prev[1] and nxt[1] and prev[0] == nxt[0] and ord(nxt[1]) == ord(prev[1]) + 2:
            labs[i] = (prev[0], chr(ord(prev[1]) + 1))
        elif prev[1] is None and nxt[1] == "b" and prev[0] + 1 == nxt[0]:
            labs[i] = (nxt[0], "a")
        else:
            return None
    # tek maddeli soru ("3a" var, "3b" yok) madde sayılmaz: test şıkkı ya da yanlış okuma olabilir
    out = []
    for q, part in labs:
        alone = part is not None and sum(1 for q2, p2 in labs if q2 == q and p2) < 2
        out.append(f"{q}{'' if alone or part is None else part}")
    return out


def _reading_order(found: list[tuple]) -> list[tuple]:
    """Adayları okuma sırasına dizer: sayfa, sütun, sonra SATIR (dikeyde örtüşenler aynı satır), satır içinde soldan
    sağa. found: (sayfa, sütun, y1, y2, x, Box). Ham y-merkezine göre sıralamak, aynı satırdaki puanları (puan tablosu,
    yan yana sorular) el yazısının birkaç piksellik oynamasına göre karıştırıyordu."""
    found = sorted(found, key=lambda f: (f[0], f[1], (f[2] + f[3]) / 2, f[4]))
    rows: list[list] = []
    for f in found:
        r = rows[-1] if rows else None
        if r and r[0][:2] == f[:2] and any(min(g[3], f[3]) - max(g[2], f[2]) > 0.5 * min(g[3] - g[2], f[3] - f[2]) for g in r):
            r.append(f)
        else:
            rows.append([f])
    out = []
    for r in rows:
        out += sorted(r, key=lambda f: f[4])
    return out


def recognize_auto(images: list[Path], maxes: list[float], settings: dict, progress=None, layout: dict | None = None,
                   roster: list[dict] | None = None, labels: list[str] | None = None) -> dict:
    """Şablonsuz okuma: puanların yerini renkli mürekkepten bulup her birini yakından okur.
    Renkli kalem süzgeci kapalıysa ya da puan bulunamazsa eski yönteme (bütün sayfayı okuma) döner; o yolda her
    hücre kontrole düşer. labels: puan hücrelerinin kağıttaki numaraları ("1", "2", "3a", "3b"...; bkz. sorular.py).
    Dönüş, recognize() ile aynı biçimdedir."""
    from . import sablon, sorular
    from .bolme import paper_region

    n = len(maxes)
    layout = dict(layout or {})
    row_notes: list[str] = []
    if progress:
        progress("Sayfaların yönü ve puanların yeri bulunuyor…")
    pages = []
    for p in images:
        im = load_image(p)
        deg = page_rotation(im)
        if deg:
            im = im.rotate(deg, expand=True)
            row_notes.append(f"Bir sayfa {deg}° döndürülmüş çekilmişti; düzeltilip okundu.")
        pages.append(im)
    if not pen_settings_on(settings):
        r = recognize(images, maxes, settings, progress, layout=layout, roster=roster, pages=pages, labels=labels)
        r["uyarilar"] = row_notes + r.get("uyarilar", [])
        return r
    llm = _Ollama(settings)
    raw: list = [{"yontem": "puan yerleri kendiliğinden bulundu"}]

    found = []  # (sayfa, sütun, y1, y2, x, Box)
    pen_pages = []
    page_arrays = [np.array(im) for im in pages]
    analyses = [kalem.analyze(a) for a in page_arrays]
    color = pen_color(settings)
    if color is None:
        color, counts = teacher_color(page_arrays, analyses, n)
        if color:
            others = [kalem.FAMILY_NAMES[kalem.FAMILIES[c]] for c in counts if c not in ("hepsi", color)]
            row_notes.append(f"Kağıtta öğretmenin kaleminden başka renkli kalem de var ({', '.join(others) or 'başka renk'}); "
                             f"puanlar yalnızca {kalem.FAMILY_NAMES[kalem.FAMILIES[color]]} kalemden okundu. Öğretmenin kalem "
                             "rengini Ayarlar'dan seçerseniz bu uyarı çıkmaz.")
            raw.append({"renk_secimi": color, "aday_sayilari": counts})
    for pi, im in enumerate(pages):
        a = page_arrays[pi]
        marks, only = find_score_marks(a, analyses[pi], color)
        pen_pages.append(only)
        key = str(pi)
        if key not in layout:
            layout[key] = is_two_column(im)
        H, W = a.shape[:2]
        # sütun sınırı fotoğrafın değil kağıdın ortası (tek kağıtlık fotoğraf kağıda kırpılmaz, kağıt kayık olabilir)
        cols_with_paper = np.flatnonzero(paper_region(a).any(axis=0))
        mid = (cols_with_paper[0] + cols_with_paper[-1]) / 2 if cols_with_paper.size else W / 2
        for x, y, w, h in marks:
            col = int(layout[key] and x + w / 2 > mid)
            found.append((pi, col, y, y + h, x, sablon.Box(0, pi, x / W, y / H, w / W, h / H)))
    found = [(f[0], f[5]) for f in _reading_order(found)]  # (sayfa, Box)
    raw.append({"adaylar": [[pi + 1, round(b.x, 3), round(b.y, 3)] for pi, b in found]})
    if len(found) < max(1, n // 2) or len(found) > 4 * n:
        # renkli puan bulunamadı (siyah kalem, puan tablosu) ya da öğrenci de renkli kalem kullanmış: eski yöntem
        r = recognize(images, maxes, settings, progress, layout=layout, roster=roster, pages=pages, labels=labels)
        r["uyarilar"] = row_notes + [f"Puanların yeri bulunamadı ({len(found)} renkli aday); bütün sayfa okundu. "
                                     "Bu sınav için şablon işaretlerseniz her puan ayrı ve doğrulanarak okunur."] + r.get("uyarilar", [])
        return r

    # öğrenci kimliği: ilk sayfanın üst kısmı (ad ve numara her kalemle okunur)
    if progress:
        progress("Ad ve numara okunuyor…")
    top = header_crop(pages[0])
    ans = llm.ask(HEADER_PROMPT, top, HEADER_SCHEMA)
    header, hconf = clean_header(ans.data), _header_values(ans)
    raw.append({"baslik": header, "eminlik": hconf})
    no0 = re.sub(r"\D", "", str(header.get("student_number") or ""))

    # bütün adayları toplu oku; sayı olanların yanındaki basılı soru numarasını yerel OCR ile bul
    rs = read_boxes(llm, [(page_arrays[pi], pen_pages[pi], box) for pi, box in found], progress)
    if progress:
        progress("Puanların hangi soruya ait olduğu bulunuyor…")
    reads = []  # (Box, okuma sonucu, basılı soru numarası)
    for ci, ((pi, box), r) in enumerate(zip(found, rs)):
        if r["deger"] is None:
            raw.append({"aday": ci + 1, "oylar": [(nm, v) for nm, v, _c, _w in r["oylar"]], "sonuc": "puan değil"})
            continue
        lab = printed_label(pages[pi], pen_pages[pi], box, n)
        reads.append((box, r, lab))
        raw.append({"aday": ci + 1, "sayfa": pi + 1, "oylar": [(nm, v) for nm, v, _c, _w in r["oylar"]], "soru_no": lab})

    # öğrenci numarası diye okunan sayı, başlık kırpımına giren bir puan mı?
    if number_is_score(no0, [t[1]["deger"] for t in reads if t[0].sayfa == 0 and t[0].y < HEADER_FRACTION]):
        row_notes.append(f"Numara diye {no0} okundu ama bu, başlığın yanındaki bir puan; numara boş bırakıldı.")
        raw.append({"uyari": "numara bir puanla aynı", "numara": no0})
        no0 = ""
    choice = None
    if roster and needs_roster_choice(no0, str(header.get("student_name") or ""), hconf["p_no"], roster):
        if progress:
            progress("İsim sınıf listesiyle karşılaştırılıyor…")
        choice = choose_from_roster(top, roster, settings)
        raw.append({"liste_secimi": choice})

    # Kağıdın toplamı. Soru puanı sanılırsa (bir soru boş kalmışsa aday sayısı yine n olur) bütün puanlar bir soru
    # kayardı: tek bir sorunun tam puanından büyük okunan aday; ya da başlıkta okunan toplamla aynı değerdeki,
    # 1. sayfanın üst kısmındaki aday; ya da aday fazlaysa, değeri ötekilerin toplamına eşit olan aday.
    total, p_total = None, 0.0
    head_total = parse_number(header.get("total_score"))
    pool = [t for t in reads if t[1]["deger"] > max(maxes)]
    if not pool and head_total is not None and head_total > 0:
        # Başlık okuması, başlığın hemen altındaki 1. sorunun puanını da "toplam" sanabiliyor (gerçek kağıtta S1=15
        # toplam sanıldı, bütün puanlar bir soru kaydı). Bu yüzden başlıktaki sayıyla eşleşen aday ancak toplam gibi
        # davranıyorsa toplam sayılır: soru sayısından fazla aday var ya da değeri öteki puanların toplamına eşit.
        def others(t):
            return sum(x[1]["deger"] for x in reads if x is not t)
        pool = [t for t in reads if t[0].sayfa == 0 and t[0].y + t[0].h / 2 < 0.3 and abs(t[1]["deger"] - head_total) < 0.01
                and (len(reads) > n or abs(t[1]["deger"] - others(t)) < 0.01)]
    if not pool and len(reads) > n:
        all_sum = sum(t[1]["deger"] for t in reads)
        pool = [t for t in reads if t[1]["deger"] > 0 and abs(2 * t[1]["deger"] - all_sum) < 0.01]
    if pool:
        tb = max(pool, key=lambda t: t[1]["deger"])
        total, p_total = tb[1]["deger"], tb[1]["eminlik"]
        reads = [t for t in reads if t is not tb]

    # Puan hücreleri kağıttaki numaralarıyla: (soru numarası, madde harfi) — "3b" → (3, "b").
    slots = [sorular.parse(x) for x in (labels or [])]
    if len(slots) != n or any(q is None for q, _p in slots):
        slots = [(i + 1, None) for i in range(n)]
    names = [f"{q}{p or ''}" for q, p in slots]
    parted = any(p for _q, p in slots)
    nums = [t[2][0] for t in reads]
    parts = [t[2][1] if parted else None for t in reads]
    # Kağıtta sınavda olmayan bir soru numarası okunduysa (6 hücreli sınavda "7)") kağıdın numaralandırması sınavdakinden
    # farklıdır: numara atlanmış ya da sorular A/B maddelerine ayrılmış. O zaman basılı "6)" 6. hücreyi göstermez (gerçek
    # kağıtta 5 atlanmıştı: "6)" yanındaki puan 6. hücreye yazıldı, "7)" yanındaki kayboldu). Numaralar kullanılmaz,
    # yalnızca sıra kullanılır ve her hücre kontrole düşer.
    known = {q for q, _p in slots}
    # sınavın en büyük numarasından çok büyük sayı ("35", şekildeki bir uzunluk) numaralandırma farkı değil, gürültüdür
    nums = [k if k is None or k <= max(known) + 3 else None for k in nums]
    foreign = sorted({k for k in nums if k is not None and k not in known})
    if foreign:
        nums = [None] * len(reads)
        row_notes.append(f"Kağıtta sınavda olmayan soru numarası okundu ({', '.join(map(str, foreign))}). Kağıttaki "
                         "numaralar farklıysa (atlanan numara, A/B maddeleri) sınav bilgilerinde soru numaralarını kağıttaki "
                         "gibi yazın (ör. 1, 2, 3a, 3b); o zaman puanlar numaralarıyla doğrulanır.")
        raw.append({"uyari": "kağıtta sınavda olmayan soru numarası", "numaralar": foreign})
    hint = _infer_labels([t[2] for t in reads])
    if hint and len(hint) == n and hint != names and not sorular.check(hint):
        row_notes.append("Kağıttaki soru numaraları " + ", ".join(hint) + " gibi görünüyor. Sınav bilgilerinde soru "
                         "numaralarını böyle yazarsanız puanlar numaralarıyla doğrulanarak okunur.")
    assign = _assign_questions(parts, n, nums, slots)

    def num_ok(k: int, q: int | None) -> bool:
        return q is not None and nums[k] is not None and nums[k] == slots[q - 1][0]

    def confirmed(k: int, q: int | None) -> bool:
        # hücre kendi basılı numarasıyla doğrulandı; maddeli hücrede madde harfiyle de (numara yalnızca soruyu gösterir:
        # 3a boşken 3b'nin puanı "3)" yüzünden 3a'ya yazılmasın)
        return num_ok(k, q) and (slots[q - 1][1] is None or parts[k] == slots[q - 1][1])

    def clashes(k: int, q: int | None) -> str:
        if q is None:
            return ""
        q0, p0 = slots[q - 1]
        if nums[k] is not None and nums[k] != q0:
            return f"Puanın yanında {nums[k]}. sorunun numarası basılı; başka bir soruya ait olabilir"
        if p0 is not None and parts[k] is not None and parts[k] != p0:
            return f"Puanın yanında {parts[k].upper()} maddesi basılı; başka bir maddeye ait olabilir"
        return ""

    # Sıra ancak şunlar varsa güvenilir: puan sayısı hücre sayısına eşit, eşleştirme okuma sırasıyla birebir aynı, hiçbir
    # basılı numara sırayla çelişmiyor ve puanı olan her sayfada en az bir basılı numara sırayı doğruluyor. Değilse
    # numarasıyla doğrulanmamış her hücre kontrole düşer: kaymış ya da yer değiştirmiş bir eşleştirme sessiz kalmasın.
    clash = any(clashes(k, q) for k, q in enumerate(assign))
    pages_with = {t[0].sayfa for t, q in zip(reads, assign) if q is not None}
    pages_confirmed = {t[0].sayfa for k, (t, q) in enumerate(zip(reads, assign)) if num_ok(k, q)}
    # Puan sayısı soru sayısına eşit olsa da bu bir rastlantı olabilir: puan olmayan renkli bir işaret (öğrencinin mavi
    # kalemle yazdığı bir rakam, tik) okunmuş ve aynı anda bir soru puansız kalmışsa ikisinin arasındaki puanlar bir
    # soru kayar. Böyle bir işaretin izi: bakışlardan biri (çoğunlukla süzgeçsiz bakış) orada puan görmemiştir.
    doubtful = any(q is not None and not confirmed(k, q) and any(v is None for _n, v, _c, _w in t[1]["oylar"])
                   for k, (t, q) in enumerate(zip(reads, assign)))
    order_ok = (len(reads) == n and assign == list(range(1, n + 1)) and not clash and pages_with <= pages_confirmed
                and not doubtful and not foreign)
    # Bir puan hiçbir soruya bağlanamadı VE bir soru boş kaldı: eşleştirme basılı numaralara uyup sırayı bozmuş
    # demektir. Basılı numara okuması da yanılabilir; numarasıyla doğrulanan hücreler de kontrole düşer.
    shaky = None in assign and len(set(assign) - {None}) < n
    if not order_ok:
        raw.append({"uyari": "okuma sırası doğrulanamadı", "basili_numaralar": [t[2] for t in reads], "eslesme": assign,
                    "suphe": doubtful, "kayma": shaky})

    scores: list[float | None] = [None] * n
    confs: list[float] = [0.0] * n
    suspicious: dict[str, str] = {}
    to_check: list[tuple] = []
    crops = {}

    def add_note(i: int, note: str) -> None:
        suspicious[str(i)] = (suspicious[str(i)] + "; " + note) if suspicious.get(str(i)) else note

    # her puandan önce (okuma sırasında) bağlanan son hücre: arada boş hücre kaldıysa puan, basılı numarası yüzünden ileri atlamıştır
    before: dict[int, int] = {}
    last = 0
    for k, q in enumerate(assign):
        if q is not None:
            before[k], last = last, q

    for k, ((box, r, _lab), q) in enumerate(zip(reads, assign)):
        if q is None:
            continue
        i = q - 1
        crops[i] = Image.fromarray(sablon.crop(page_arrays[box.sayfa], box, 0.25))
        scores[i], confs[i] = r["deger"], r["eminlik"]
        if r.get("kontrol") is not None:
            to_check.append((i, r["kontrol"]))
        if r["not"]:
            add_note(i, r["not"])
        if clashes(k, q):
            add_note(i, clashes(k, q))
        elif not confirmed(k, q) and not order_ok:
            add_note(i, "Hangi soruya ait olduğu doğrulanamadı (kağıtta puan olmayabilecek bir renkli işaret var; "
                        "puanlar bir soru kaymış olabilir)" if doubtful else
                        "Hangi soruya ait olduğu doğrulanamadı (soru numarası okunamadı, sıra kesin değil)")
        elif shaky:
            add_note(i, "Bir puan hiçbir soruya bağlanamadı, bir soru da puansız kaldı; puanlar kaymış olabilir, "
                        "kağıda bakın")
        elif q - before[k] > 1:
            # Önündeki hücre(ler) boş kaldı ve bu puan yalnızca basılı numarasıyla ileri bağlandı. Kağıttaki numaralar
            # sınavdakinden farklıysa yanlış hücredir (gerçek kağıtta 5 atlanmıştı: "6)" yanındaki puan 6. hücreye yazıldı).
            add_note(i, f"Önceki {', '.join('S' + x for x in names[before[k]:q - 1])} puansız kaldı; bu puan yanındaki basılı "
                        f"numaraya göre S{names[i]} hücresine yazıldı. Kağıttaki soru numaraları sınavdakiyle aynı mı, bakın")
        if _mark_at_edge(pen_pages[box.sayfa], box):
            add_note(i, "Puan fotoğrafın kenarında kesiliyor (kağıt kırpılmış çekilmiş), kağıda bakın")
        if r["deger"] > maxes[i]:
            add_note(i, f"Okunan puan ({r['deger']:g}) bu sorunun tam puanından büyük")
    # soruya bağlanamayan puanlar: yanında okunan sorunun ve okuma sırasında komşu hücrelerin notuna yaz
    for k, ((box, r, _lab), q) in enumerate(zip(reads, assign)):
        if q is not None:
            continue
        targets = {j for j, (q0, _p) in enumerate(slots) if nums[k] is not None and q0 == nums[k]}
        prev = next((assign[j] for j in range(k - 1, -1, -1) if assign[j] is not None), None)
        nxt = next((assign[j] for j in range(k + 1, len(assign)) if assign[j] is not None), None)
        targets |= {t - 1 for t in (prev, nxt) if t is not None}
        for t in sorted(targets):
            add_note(t, f"Yakında hangi soruya ait olduğu anlaşılamayan bir puan daha okundu ({r['deger']:g}), kağıda bakın")
        row_notes.append(f"Hangi soruya ait olduğu anlaşılamayan bir puan okundu: {r['deger']:g}")
    checks = second_opinion(settings, to_check, progress)
    if checks:
        raw.append({"ikinci_model": settings.get("ikinci_model"), "okumalar": {str(k + 1): v for k, v in checks.items()}})
        apply_second_opinion(scores, suspicious, checks, settings.get("ikinci_model", ""))
    for i in range(n):
        if scores[i] is None:
            add_note(i, "Bu sorunun puanı kağıtta bulunamadı")

    total_ok = total is not None and None not in scores and abs(sum(scores) - total) <= 0.01
    if total_ok and p_total >= 0.9:
        confs = [max(c, min(0.99, p_total)) for c in confs]
    return {
        "kutular": crops,
        "uyarilar": row_notes,
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
