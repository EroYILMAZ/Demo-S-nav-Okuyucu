"""Şablonsuz okumanın eşleştirme kurallarının sahte okumalarla birim testi (model ve OCR çağrısı yok).

Kullanım: .venv\Scripts\python.exe araclar\eslestirme_testi.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app import bolme  # noqa: E402
from app import recognizer as R  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok  " if cond else "  HATA") + " " + msg)
    if not cond:
        fails += 1


# --- basılı numara deseni
def lab(text):
    m = next(R._LABEL_RE.finditer(text), None)
    return int(m.group(1) or m.group(2)) if m else None


for text, want in [("5-)", 5), ("5)", 5), ("5.", 5), ("5- Aşağıdaki", 5), ("Soru 5", 5), ("10-", 10),
                   ("1.5 kg", None), ("2-3 arası", None), ("x=1,5.", None), ("7.Mekkeli", 7), ("3 -)", 3)]:
    ok(lab(text) == want, f"etiket {text!r} -> {lab(text)} (beklenen {want})")

# --- eşleştirme: numara yokken sırayla; atlanan hücre cezası (hücre değişkeni ezilince her aday atlanıyordu)
from app import sorular  # noqa: E402
S11 = [sorular.parse(x) for x in ["1a", "1b", "1c", "1d", "1e", "2a", "2b", "3", "4", "5", "6"]]
ok(R._assign_questions([None, "b", "b", None, None, "b", None, None, None, None, None], 11, [None] * 11, S11)
   == list(range(1, 12)), "yalnızca madde harfleri varken adaylar sırayla bağlanır")
ok(R._assign_questions([None] * 4, 4, [None] * 4) == [1, 2, 3, 4], "numarasız adaylar sırayla bağlanır")
ok(R._assign_questions([None] * 4, 5, [1, 2, 4, 5]) == [1, 2, 4, 5], "numaralar boş hücreyi atlatır")

# --- kenarda kesilen puan
W, H = 1000, 1400
only = np.full((H, W, 3), 255, np.uint8)
only[0:40, 100:140] = 30          # üst kenara değen mürekkep
only[500:540, 100:140] = 30       # içeride
only[0:2, 300:340] = 30           # başka bir kümenin üst kenardaki mürekkebi (aşağıdaki kutuyla aynı sütunda)
from app.sablon import Box  # noqa: E402
ok(R._mark_at_edge(only, Box(0, 0, 100 / W, 0, 40 / W, 40 / H)), "kenara değen küme kesik sayılır")
ok(not R._mark_at_edge(only, Box(0, 0, 100 / W, 500 / H, 40 / W, 40 / H)), "içerideki küme kesik sayılmaz")
ok(not R._mark_at_edge(only, Box(0, 0, 300 / W, 600 / H, 40 / W, 40 / H)), "aynı sütunda kenardaki başka küme karışmaz")
only2 = np.full((H, W, 3), 255, np.uint8)
only2[1360:H, 900:W] = 30
ok(R._mark_at_edge(only2, Box(0, 0, 900 / W, 1360 / H, 100 / W, 40 / H)), "sağ alt köşedeki küme kesik sayılır")


# --- recognize_auto eşleştirme senaryoları
class FakeAnswer:
    def __init__(self, data):
        self.data, self.content, self.toks = data, "", []

    def number_conf(self, s, e):
        return 0.97, None


class FakeLLM:
    def __init__(self, settings):
        pass

    def ask(self, prompt, image, schema, side=None, logprobs=True):
        return FakeAnswer({"name_written": True, "student_name": "Ali Veli", "number_written": True,
                           "student_number": "12", "class_name": "", "total_score": -1})


def run(cands, n, labels, edge=None, names=None, maxes=None):
    """cands: [(y, değer, oylar)] — tek sayfa, tek sütun. labels: {y: basılı numara ya da (numara, madde harfi)}.
    names: sınavın soru numaraları ("1", "3a"...)."""
    marks = [(100, int(y * H), 80, 60) for y, _v, _o in cands]
    only = np.full((H, W, 3), 255, np.uint8)
    if edge is not None:
        marks[edge] = (100, 0, 80, 60)
        only[0:30, 120:160] = 30
    R.load_image = lambda p: Image.new("RGB", (W, H), "white")
    R.page_rotation = lambda im: 0
    R.is_two_column = lambda im: False
    R.find_score_marks = lambda a, an=None, color=None: (marks, only)
    bolme.paper_region = lambda a: np.ones(a.shape[:2], bool)
    R._Ollama = FakeLLM
    R.second_opinion = lambda *a, **k: {}

    def fake_read(llm, items, progress=None, auto=False):
        out = []
        for (_p, _q, box), (_y, v, oylar) in zip(items, cands):
            out.append({"deger": v, "eminlik": 1.0, "oylar": oylar, "not": "", "kontrol": None})
        return out
    R.read_boxes = fake_read
    def fake_label(im, pen, box, n, printed_out=None):
        lab = labels.get(round(box.y, 2))
        return lab if isinstance(lab, tuple) else (lab, None)
    R.printed_label = fake_label
    return R.recognize_auto([Path("x.jpg")], maxes or [10] * n, {"yalnizca_renkli_kalem": True}, roster=None, labels=names)


def votes(v, plain=None):
    return [("kalem", v, 0, 1)] * 4 + [("suzgecsiz", v if plain == "same" else plain, 0, 0)]


S = "same"
# 1) olağan: 5 puan, 5 soru, 1. ve 4. sorunun numarası basılı bulunmuş -> kontrol yok
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.4, 8, votes(8, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.1: 1, 0.55: 4})
ok(r["puanlar"] == [7, 5, 8, 6, 9] and not r["supheli"], f"olağan kağıt sessiz geçer: {r['puanlar']} {r['supheli']}")

# 2) puan olmayan renkli işaret (süzgeçsiz bakış puan görmedi) + puansız soru: sayı tutuyor ama sıra şüpheli
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, None)), (0.4, 8, votes(8, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.1: 1, 0.7: 5})
flag = set(r["supheli"])
ok({"1", "2", "3"} <= flag and "0" not in flag and "4" not in flag,
   f"şüpheli işaret: numarasız hücreler kontrole düşer: {sorted(flag)}")

# 3) yanlış basılı numara (3. puanın yanında '4') eşleştirmeyi kaydırır -> numarasıyla 'doğrulanan' hücre de kontrole
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.4, 8, votes(8, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.1: 1, 0.4: 4})
silent = [i for i in range(5) if r["puanlar"][i] is not None and str(i) not in r["supheli"]]
ok(all(r["puanlar"][i] == [7, 5, 8, 6, 9][i] for i in silent),
   f"yanlış numarada sessiz yanlış yok: puanlar={r['puanlar']} sessiz={silent}")

# 4) boş soru (puan yok), sıra numaralarla doğrulanmış: yalnızca boş soru kontrole düşer
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.1: 1, 0.25: 2, 0.55: 4, 0.7: 5})
ok(r["puanlar"] == [7, 5, None, 6, 9] and set(r["supheli"]) == {"2", "3"},
   f"boş soru: boş hücre ve ardından numarasıyla atlayan hücre kontrole düşer: {r['puanlar']} {sorted(r['supheli'])}")

# 5) kenarda kesilen puan kontrole düşer
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.4, 8, votes(8, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.25: 2, 0.55: 4}, edge=0)
ok("0" in r["supheli"] and "kenar" in r["supheli"]["0"], f"kenardaki puan: {r['supheli'].get('0')}")

# 6) A/B maddeli sınav (1, 2, 3a, 3b, 4): numaralar ve madde harfleri doğruluyor -> sessiz ve doğru
five = [(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.4, 4, votes(4, S)), (0.55, 3, votes(3, S)), (0.7, 9, votes(9, S))]
r = run(five, 5, {0.1: 1, 0.25: 2, 0.4: (3, "a"), 0.55: (None, "b"), 0.7: 4}, names=["1", "2", "3a", "3b", "4"])
ok(r["puanlar"] == [7, 5, 4, 3, 9] and not r["supheli"], f"maddeli sınav: {r['puanlar']} {r['supheli']}")

# 7) aynı kağıt, sınavda maddeler tanımlanmamış (1..5): "4)" 5. puanın yanında -> kayma sessiz kalmaz + numara önerisi
r = run(five, 5, {0.1: 1, 0.25: 2, 0.4: (3, "a"), 0.55: (None, "b"), 0.7: 4})
silent = [i for i in range(5) if r["puanlar"][i] is not None and str(i) not in r["supheli"]]
ok(all(r["puanlar"][i] == [7, 5, 4, 3, 9][i] for i in silent), f"maddeler tanımsız: sessiz yanlış yok {r['puanlar']} sessiz={silent}")
ok(any("1, 2, 3a, 3b, 4" in w for w in r["uyarilar"]), f"numara önerisi: {r['uyarilar']}")

# 8) 3a boş (puan yok), 3b'nin yanında yalnızca "3)" okunmuş: 3b'nin puanı 3a'ya sessizce yazılmasın
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.55, 3, votes(3, S)), (0.7, 9, votes(9, S))], 5,
        {0.1: 1, 0.25: 2, 0.55: 3, 0.7: 4}, names=["1", "2", "3a", "3b", "4"])
silent = [i for i in range(5) if r["puanlar"][i] is not None and str(i) not in r["supheli"]]
ok(all(r["puanlar"][i] == [7, 5, None, 3, 9][i] for i in silent), f"boş 3a: sessiz yanlış yok {r['puanlar']} sessiz={silent}")

# 9) kağıtta 5 atlanmış (1,2,3,4,6,7), sınav 1..6: "6)" 5. puanın yanında, "7)" 6.'nın -> sıra kullanılır, hepsi kontrole
MX6 = [15, 15, 15, 20, 20, 15]
six = [(0.1, 13, votes(13, S)), (0.25, 7, votes(7, S)), (0.4, 9, votes(9, S)), (0.55, 16, votes(16, S)),
       (0.7, 11, votes(11, S)), (0.85, 3, votes(3, S))]
r = run(six, 6, {0.25: 2, 0.7: 6, 0.85: 7}, maxes=MX6)
ok(r["puanlar"] == [13, 7, 9, 16, 11, 3] and len(r["supheli"]) == 6 and any("(7)" in w for w in r["uyarilar"]),
   f"atlanan numara: {r['puanlar']} kontrol={sorted(r['supheli'])} {r['uyarilar']}")
# ... sınavda numaralar kağıttaki gibi yazılınca sessiz ve doğru
r = run(six, 6, {0.25: 2, 0.7: 6, 0.85: 7}, names=["1", "2", "3", "4", "6", "7"], maxes=MX6)
ok(r["puanlar"] == [13, 7, 9, 16, 11, 3] and not r["supheli"], f"numaralar kağıttaki gibi: {r['puanlar']} {r['supheli']}")

# 9b) gerçek kağıt: 5 atlanmış, "·3" puanı okunamamış (aday atıldı), sınav 1..6: "6)" yanındaki 11 sessizce S6'ya yazılmasın
r = run(six[:5], 6, {0.25: 2, 0.7: 6}, maxes=MX6)
silent = [i for i in range(6) if r["puanlar"][i] is not None and str(i) not in r["supheli"]]
ok(all(r["puanlar"][i] == [13, 7, 9, 16, 11, 3][i] for i in silent), f"atlanan numara + kayıp puan: {r['puanlar']} sessiz={silent}")

# 10) başlık: isim/numara yazılmamış -> boş; sınıf numaraya karışmaz; numara alanına okunan puan yakalanır
h = R.clean_header({"name_written": False, "student_name": "Ayşe Yılmaz", "number_written": True, "student_number": "8/C 368"})
ok(h["student_name"] == "" and h["student_number"] == "368", f"başlık temizliği: {h}")
ok(R.number_is_score("13", [13.0, 7.0]) and not R.number_is_score("368", [13.0]), "numara bir puanla aynıysa yakalanır")

# 10b) gerçek kağıt: 7 puan, 7 soru; son iki dairenin yanında da basılı "6" okundu -> sıra korunur, S7 boş kalmaz
a = R._assign_questions([None] * 7, 7, [None] * 5 + [6, 6])
ok(a == [1, 2, 3, 4, 5, 6, 7], f"sonda boş hücre de atlanmış sayılır: {a}")

# 11) ikinci model tutmazsa: değer aynı kalır, hücre kontrole düşer, ikinci modelin okuması hızlı kontrolde 2. seçenek olur
sc, sus, opts = [15.0, 4.0, 8.0], {}, {"0": [15.0, 13.0], "1": [4.0]}
R.apply_second_opinion(sc, sus, {0: 5.0, 1: 4.0, 2: None}, "minicpm-v:8b", opts)
ok(sc == [15.0, 4.0, 8.0] and sorted(sus) == ["0", "2"] and opts["0"] == [15.0, 5.0, 13.0] and opts["1"] == [4.0]
   and "2" not in opts, f"ikinci model: {sus} {opts}")

print("\nBAŞARISIZ:", fails)
sys.exit(1 if fails else 0)
