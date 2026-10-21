"""Şablonlu okumanın doğruluk testi.

Kullanım: .venv\\Scripts\\python.exe araclar\\sablon_testi.py <sablon.json> [model]
sablon.json: {"sablon_sayfalari": [...], "kutular": [...], "tam": [...],
              "kagitlar": [{"ad": "...", "resimler": [...], "dogru": [...], "no": "..."}]}
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.recognizer import recognize_templated  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
settings = config.load_settings()
if len(sys.argv) > 2:
    settings["model"] = sys.argv[2]
print("Model:", settings["model"], flush=True)

ok_all = n_all = 0
for c in spec["kagitlar"]:
    t = time.time()
    r = recognize_templated([Path(p) for p in c["resimler"]], spec["tam"], settings,
                            [Path(p) for p in spec["sablon_sayfalari"]], spec["kutular"], progress=None, roster=c.get("liste"))
    ok = sum(1 for a, b in zip(r["puanlar"], c["dogru"]) if a is not None and abs(a - b) < 0.01)
    ok_all += ok
    n_all += len(c["dogru"])
    print(f"\n{c['ad']}: {ok}/{len(c['dogru'])}  no={r['ogrenci_no']} ad='{r['ad_soyad']}'  {time.time() - t:.0f} sn", flush=True)
    for h in r["ham"]:
        if "eslesme" in h:
            print(f"  foto {h['foto']} → şablon sayfası {h['sablon_sayfasi']} ({h['eslesme']} eşleşme)")
        if "soru" in h:
            q = h["soru"]
            got = r["puanlar"][q - 1]
            mark = "✓" if got is not None and abs(got - c["dogru"][q - 1]) < 0.01 else f"✗ (doğru {c['dogru'][q - 1]})"
            note = r["supheli"].get(str(q - 1), "")
            print(f"  S{q}: {got} {mark}  oylar {[(v[0], v[1]) for v in h['oylar']]}{'  KONTROL: ' + note if note else ''}")
    if r["supheli"]:
        print("  kontrol notları:", r["supheli"])
print(f"\nTOPLAM: {ok_all}/{n_all}")
