"""Çalışan sunucuya örnek kağıtları gönderip okunan puanları beklenenle karşılaştırır.

Kullanım: python araclar/uctan_uca_test.py [klasör]   (sunucu http://127.0.0.1:8000'de çalışıyor olmalı)
"""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ornek_kagit import MAXES, STUDENTS  # noqa: E402

BASE = "http://127.0.0.1:8000"
folder = Path(sys.argv[1] if len(sys.argv) > 1 else "ornek_kagitlar")

c = httpx.Client(base_url=BASE, timeout=60)
ex = c.post("/api/sinavlar", json={"ad": "Test Yazılısı", "sinif": "9-A", "ders": "Matematik", "tam_puanlar": MAXES}).json()
eid = ex["id"]
c.post(f"/api/sinavlar/{eid}/liste", data={"metin": "101 Ali Yılmaz\n117 Ayşe Demir\n124 Mehmet Çelik\n130 Zeynep Kaya"}).raise_for_status()
files = [("dosyalar", (f"kagit_{no}.jpg", (folder / f"kagit_{no}.jpg").read_bytes(), "image/jpeg")) for no, _, _ in STUDENTS]
c.post(f"/api/sinavlar/{eid}/kagit", files=files).raise_for_status()
print("Sınav:", eid)

t0 = time.time()
while True:
    exam = c.get(f"/api/sinavlar/{eid}").json()
    pending = [r for r in exam["kayitlar"] if r["durum"] in ("bekliyor", "isleniyor")]
    if not pending:
        break
    time.sleep(3)
print(f"Toplam süre: {time.time() - t0:.0f} sn\n")

expected = {no: (name, scores) for no, name, scores in STUDENTS}
correct = total = 0
for r in exam["kayitlar"]:
    if r["durum"] != "tamam":
        print("HATA:", r.get("hata"))
        continue
    exp = expected.get(r["ogrenci_no"])
    print(f"{r['ogrenci_no']:>4} {r['ad_soyad']:<15} {r['puanlar']}  ({r['sure']} sn)  okunan ham: no={r['okunan']['ogrenci_no']} ad={r['okunan']['ad_soyad']} toplam={r['yazili_toplam']}")
    if r["uyarilar"] or r["hucre_uyarilari"]:
        print("      uyarılar:", r["uyarilar"], r["hucre_uyarilari"])
    if exp:
        good = sum(1 for a, b in zip(r["puanlar"], exp[1]) if a is not None and abs(a - b) < 0.01)
        correct += good
        total += len(exp[1])
        if good < len(exp[1]):
            print("      beklenen:", exp[1])
print(f"\nPuan doğruluğu: {correct}/{total}")
r = c.get(f"/api/sinavlar/{eid}/excel")
Path("test_cikti.xlsx").write_bytes(r.content)
print("Excel: test_cikti.xlsx", len(r.content), "bayt")
