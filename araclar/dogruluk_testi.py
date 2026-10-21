"""Okuma motorunu doğrudan (sunucu olmadan) bilinen kağıtlar üzerinde test eder.

Kullanım: .venv\\Scripts\\python.exe araclar\\dogruluk_testi.py [model]
Gerçek kağıt testi için kagitlar.json dosyasına {"resimler": [...], "tam": [...], "dogru": [...], "no": "..."} ekleyin.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "araclar"))

from app import config  # noqa: E402
from app.recognizer import recognize  # noqa: E402
from ornek_kagit import MAXES, STUDENTS  # noqa: E402

cases = [
    {"ad": f"ornek {no}", "resimler": [str(ROOT / "ornek_kagitlar" / f"kagit_{no}.jpg")], "tam": MAXES, "dogru": scores, "no": no}
    for no, _, scores in STUDENTS
]
extra = ROOT / "araclar" / "kagitlar.json"
if extra.exists():
    cases = json.loads(extra.read_text(encoding="utf-8")) + cases

settings = config.load_settings()
if len(sys.argv) > 1:
    settings["model"] = sys.argv[1]
print("Model:", settings["model"])

ok_total = all_total = 0
for c in cases:
    t = time.time()
    r = recognize([Path(p) for p in c["resimler"]], c["tam"], settings)
    ok = sum(1 for a, b in zip(r["puanlar"], c["dogru"]) if a is not None and abs(a - b) < 0.01)
    ok_total += ok
    all_total += len(c["dogru"])
    no_ok = "✓" if r["ogrenci_no"] == c["no"] else "✗"
    print(f"{c['ad']:<12} puan {ok}/{len(c['dogru'])}  no {r['ogrenci_no']}{no_ok}  ad '{r['ad_soyad']}'  toplam {r['yazili_toplam']}  "
          f"{time.time() - t:.0f}s  supheli={r['supheli']}")
    if ok < len(c["dogru"]):
        print("      okunan :", r["puanlar"])
        print("      doğru  :", c["dogru"])
print(f"\nToplam puan doğruluğu: {ok_total}/{all_total}")
