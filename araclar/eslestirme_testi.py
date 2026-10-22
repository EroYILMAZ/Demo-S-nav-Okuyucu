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
        return FakeAnswer({"student_name": "Ali Veli", "student_number": "12", "class_name": "", "total_score": -1})


def run(cands, n, labels, edge=None):
    """cands: [(y, değer, oylar)] — tek sayfa, tek sütun. labels: {y: basılı numara}."""
    marks = [(100, int(y * H), 80, 60) for y, _v, _o in cands]
    only = np.full((H, W, 3), 255, np.uint8)
    if edge is not None:
        marks[edge] = (100, 0, 80, 60)
        only[0:30, 120:160] = 30
    R.load_image = lambda p: Image.new("RGB", (W, H), "white")
    R.page_rotation = lambda im: 0
    R.is_two_column = lambda im: False
    R.find_score_marks = lambda a: (marks, only)
    bolme.paper_region = lambda a: np.ones(a.shape[:2], bool)
    R._Ollama = FakeLLM
    R.second_opinion = lambda *a, **k: {}

    def fake_read(llm, items, progress=None):
        out = []
        for (_p, _q, box), (_y, v, oylar) in zip(items, cands):
            out.append({"deger": v, "eminlik": 1.0, "oylar": oylar, "not": "", "kontrol": None})
        return out
    R.read_boxes = fake_read
    R.printed_label = lambda im, pen, box, n: labels.get(round(box.y * H / H, 2))
    return R.recognize_auto([Path("x.jpg")], [10] * n, {"yalnizca_renkli_kalem": True}, roster=None)


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
ok(r["puanlar"] == [7, 5, None, 6, 9] and set(r["supheli"]) == {"2"}, f"boş soru: {r['puanlar']} {sorted(r['supheli'])}")

# 5) kenarda kesilen puan kontrole düşer
r = run([(0.1, 7, votes(7, S)), (0.25, 5, votes(5, S)), (0.4, 8, votes(8, S)), (0.55, 6, votes(6, S)), (0.7, 9, votes(9, S))],
        5, {0.25: 2, 0.55: 4}, edge=0)
ok("0" in r["supheli"] and "kenar" in r["supheli"]["0"], f"kenardaki puan: {r['supheli'].get('0')}")

print("\nBAŞARISIZ:", fails)
sys.exit(1 if fails else 0)
