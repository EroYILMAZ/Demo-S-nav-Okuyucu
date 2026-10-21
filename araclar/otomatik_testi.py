"""Şablonsuz okumanın (puan yerlerini kendiliğinden bulma) doğruluk testi.

Kullanım: .venv\\Scripts\\python.exe araclar\\otomatik_testi.py <test.json> [model]
test.json: {"tam": [...], "kagitlar": [{"ad": "...", "resimler": [...], "dogru": [...]}]}
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.recognizer import recognize_auto  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
settings = config.load_settings()
if len(sys.argv) > 2:
    settings["model"] = sys.argv[2]
print("Model:", settings["model"], flush=True)

ok_all = n_all = flagged_wrong = silent_wrong = 0
for c in spec["kagitlar"]:
    t = time.time()
    r = recognize_auto([Path(p) for p in c["resimler"]], spec["tam"], settings, progress=None)
    n = len(c["dogru"])
    ok = sum(1 for a, b in zip(r["puanlar"], c["dogru"]) if a is not None and abs(a - b) < 0.01)
    ok_all += ok
    n_all += n
    print(f"\n{c['ad']}: {ok}/{n}  no={r['ogrenci_no']} ad='{r['ad_soyad']}'  {time.time() - t:.0f} sn", flush=True)
    for h in r["ham"]:
        if "aday" in h or "uyari" in h or "yontem" in h:
            print("  ", json.dumps(h, ensure_ascii=False))
    for i, (a, b) in enumerate(zip(r["puanlar"], c["dogru"])):
        good = a is not None and abs(a - b) < 0.01
        note = r["supheli"].get(str(i), "")
        if not good:
            if note:
                flagged_wrong += 1
            else:
                silent_wrong += 1
        print(f"  S{i + 1}: {a} {'✓' if good else f'✗ (doğru {b})'}  {('kontrol: ' + note) if note else ''}")
print(f"\nTOPLAM: {ok_all}/{n_all} | yanlış ama kontrole düşen: {flagged_wrong} | sessiz yanlış: {silent_wrong}")
