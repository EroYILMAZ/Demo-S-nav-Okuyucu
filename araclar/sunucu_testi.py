"""Sunucu mantığının hızlı testi (okuma motoru çalıştırılmaz, geçici veri klasörü kullanılır).

Kullanım: .venv\\Scripts\\python.exe araclar\\sunucu_testi.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="sinav-test-")
os.environ["SINAV_OKUYUCU_VERI"] = tmp
os.environ["SINAV_OKUYUCU_KILITSIZ"] = "1"  # eşleştirme ayrıca, aşağıda test ediliyor
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app import main  # noqa: E402

c = TestClient(main.app)  # 'with' kullanılmadığı için arka plan okuyucu başlamaz


def jpg(text: str) -> bytes:
    im = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(im).text((50, 50), text, fill="black")
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


def pdf(n: int) -> bytes:
    pages = [Image.new("RGB", (600, 800), (255, 255, 255 - i * 10)) for i in range(n)]
    b = io.BytesIO()
    pages[0].save(b, "PDF", save_all=True, append_images=pages[1:])
    return b.getvalue()


def ok(cond, msg):
    print(("  OK  " if cond else "  HATA") + " " + msg)
    if not cond:
        global failed
        failed = True


failed = False
ex = c.post("/api/sinavlar", json={"sinif": "10-C", "ders": "Mat", "ad": "T", "tam_puanlar": [10] * 5, "sayfa_sayisi": 2}).json()
eid = ex["id"]
ok(ex["sayfa_sayisi"] == 2, "sayfa sayısı kaydedildi")

print("Soru numaraları (A/B maddeleri, atlanan numara)")
exm = c.post("/api/sinavlar", json={"sinif": "M", "tam_puanlar": [10, 20, 8, 7, 15],
                                    "soru_adlari": ["1", "2", "3-A", "3 b)", "4"]}).json()
ok(exm["soru_adlari"] == ["1", "2", "3a", "3b", "4"], f"maddeli numaralar kaydedildi: {exm.get('soru_adlari')}")
for bad, why in ((["1", "2", "3", "3a", "4"], "bölünmemiş + maddeli"), (["1", "3", "2", "4", "5"], "sıra bozuk"),
                 (["1", "2", "x", "4", "5"], "geçersiz"), (["1", "2"], "sayı tutmuyor")):
    ok(c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [10] * 5, "soru_adlari": bad}).status_code == 400,
       f"geçersiz numaralar reddedildi ({why})")
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [10] * 5, "soru_adlari": ["1", "2", "3", "4", "6"]}).raise_for_status()
ok(c.get(f"/api/sinavlar/{exm['id']}").json()["soru_adlari"] == ["1", "2", "3", "4", "6"], "atlanan numara kaydedildi")
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [10] * 5, "soru_adlari": ["1", "2", "3", "4", "5"]}).raise_for_status()
ok(c.get(f"/api/sinavlar/{exm['id']}").json()["soru_adlari"] == [], "varsayılan numaralar ayrıca saklanmaz")
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [10] * 5, "soru_adlari": ["1", "2", "3a", "3b", "4"]}).raise_for_status()
import openpyxl  # noqa: E402
wb = openpyxl.load_workbook(io.BytesIO(c.get(f"/api/sinavlar/{exm['id']}/excel").content))
heads = [cell.value for cell in wb.active[4]]
ok("S3a (10)" in heads and "S3b (10)" in heads and "S4 (10)" in heads, f"Excel başlıkları maddeli: {heads[3:8]}")

print("Soru bölününce/birleşince okunmuş puanlar ve şablon kutuları numarasına göre taşınıyor")


def put_record(eid_, puanlar, kutular=None):
    def fn(e):
        e["kayitlar"].append({"id": "kayit-madde", "durum": "tamam", "puanlar": list(puanlar), "supheli": {},
                              "onaylandi": True, "ogrenci_no": "5", "ad_soyad": "Ali Veli", "resimler": [],
                              "kutu_resimleri": {}})
        if kutular:
            e["sablon"] = {"sayfalar": ["yok.jpg"], "kutular": kutular}
    main.store.update_exam(eid_, fn)


def rec(eid_):
    e = c.get(f"/api/sinavlar/{eid_}").json()
    return next(x for x in e["kayitlar"] if x["id"] == "kayit-madde"), e


box = lambda q, y: {"soru": q, "sayfa": 0, "x": 0.1, "y": y, "w": 0.1, "h": 0.05}  # noqa: E731
# birleştirme: 1, 2, 3a, 3b, 4 → 1, 2, 3, 4 (3b'de ×)
put_record(exm["id"], [11, 12, 7, 8, 15], [box(0, 0.02), box(1, 0.1), box(2, 0.2), box(3, 0.3), box(4, 0.4), box(5, 0.5)])
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [20, 20, 20, 40], "soru_adlari": ["1", "2", "3", "4"]}).raise_for_status()
r, e = rec(exm["id"])
ok(r["puanlar"] == [11, 12, 15, 15], f"birleştirmede maddeler toplanır, 4. soru yerinde kalır: {r['puanlar']}")
ok(not r["onaylandi"], "sorular değişince onay kalkar")
ks = {b["soru"]: b["y"] for b in e["sablon"]["kutular"]}
ok(ks == {0: 0.02, 1: 0.1, 2: 0.2, 4: 0.5}, f"birleşen sorunun kutusu atıldı, 4. sorunun kutusu taşındı: {ks}")
# bölme: 1, 2, 3, 4 → 1, 2, 3a, 3b, 4
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [20, 20, 10, 10, 40], "soru_adlari": ["1", "2", "3a", "3b", "4"]}).raise_for_status()
r, e = rec(exm["id"])
ok(r["puanlar"] == [11, 12, None, None, 15], f"bölmede maddeler boş kalır, 4. soru yerinde: {r['puanlar']}")
ok("p2" in r["hucre_uyarilari"] and "15" in r["hucre_uyarilari"]["p2"], f"bölünen maddede eski puan notu: {r['hucre_uyarilari']}")
ks = {b["soru"]: b["y"] for b in e["sablon"]["kutular"]}
ok(ks == {0: 0.02, 1: 0.1, 2: 0.2, 5: 0.5}, f"bölünen sorunun kutusu atıldı, 4. sorunun kutusu S4'te: {ks}")
# yalnızca numara değişti (5 atlanmış kağıt): sırayla kalır
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [20, 20, 10, 10, 40], "soru_adlari": ["1", "2", "3a", "3b", "5"]}).raise_for_status()
r, e = rec(exm["id"])
ok(r["puanlar"] == [11, 12, None, None, 15], f"numara değişikliğinde puanlar yerinde: {r['puanlar']}")
# sona soru eklemek eskisi gibi
c.put(f"/api/sinavlar/{exm['id']}", json={"tam_puanlar": [20, 20, 10, 10, 40, 10], "soru_adlari": ["1", "2", "3a", "3b", "5", "6"]}).raise_for_status()
r, e = rec(exm["id"])
ok(r["puanlar"] == [11, 12, None, None, 15, None], f"sona eklenen soru boş: {r['puanlar']}")
c.delete(f"/api/sinavlar/{exm['id']}")

print("Sınıf listesi kütüphanesi")
c.post(f"/api/sinavlar/{eid}/liste", data={"metin": "1\t247\tDeniz\tKaya\n2\t412\tAyşe\tDemir"}).raise_for_status()
ex2 = c.post("/api/sinavlar", json={"sinif": "10/c", "ders": "Fizik", "tam_puanlar": [10]}).json()
ok(c.get(f"/api/sinavlar/{ex2['id']}").json()["ogrenci_listesi"][0]["no"] == "247", "aynı sınıfın yeni sınavına liste kendiliğinden geldi")
ok("10c" in c.get("/api/siniflar").json(), "sınıf listesi kütüphanede")

print("Galeriden 5 fotoğraf, 2 sayfalık sınav (sayfa gruplama)")
files = [("dosyalar", (f"{i}.jpg", jpg(f"foto {i}"), "image/jpeg")) for i in range(5)]
r = c.post(f"/api/sinavlar/{eid}/kagit", files=files, data={"gruplama": "sayfa"}).json()
e = c.get(f"/api/sinavlar/{eid}").json()
ok(len(r["kayitlar"]) == 3 and [len(k["resimler"]) for k in e["kayitlar"]] == [2, 2, 1], "2+2+1 sayfa olarak gruplandı")
ok(any("Sayfası eksik" in w for w in e["kayitlar"][2]["uyarilar"]) or e["kayitlar"][2].get("eksik_sayfa"), "tek sayfalık son kağıt işaretlendi")

print("Aynı dosya tekrar yüklenince atlanıyor")
r = c.post(f"/api/sinavlar/{eid}/kagit", files=files[:1], data={"gruplama": "ayri"}).json()
ok(r["atlanan"] == 1 and not r["kayitlar"], "tekrar yükleme atlandı")

print("PDF (6 sayfa) → 3 öğrenci")
r = c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("tarama.pdf", pdf(6), "application/pdf"))], data={"gruplama": "sayfa"}).json()
ok(r["sayfa"] == 6 and len(r["kayitlar"]) == 3, f"PDF bölündü: {r['sayfa']} sayfa, {len(r['kayitlar'])} kağıt")

print("Seri çekim oturumu (tek tek gelen fotoğraflar)")
sid = "test-oturum-1"
before = len(c.get(f"/api/sinavlar/{eid}").json()["kayitlar"])
infos = []
for i in range(3):
    r = c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", (f"s{i}.jpg", jpg(f"seri {i}"), "image/jpeg"))], data={"oturum": sid}).json()
    infos.append(r["oturum"])
e = c.get(f"/api/sinavlar/{eid}").json()
new = e["kayitlar"][before:]
ok([len(k["resimler"]) for k in new] == [2, 1], "1. ve 2. fotoğraf aynı öğrenci, 3. yeni öğrenci")
ok(new[0]["durum"] == "bekliyor" and new[1]["durum"] == "eksik", "tamamlanan okumaya gitti, yarım olan sayfa bekliyor")
ok(infos[-1] == {"ogrenci": 2, "acik_sayfa": 1, "sayfa_sayisi": 2}, f"oturum bilgisi {infos[-1]}")

print("Geri al")
r = c.post(f"/api/sinavlar/{eid}/oturum/{sid}/geri-al").json()
e = c.get(f"/api/sinavlar/{eid}").json()
ok(len(e["kayitlar"]) == before + 1 and r["ogrenci"] == 1, "yarım kağıt geri alındı")
r = c.post(f"/api/sinavlar/{eid}/oturum/{sid}/geri-al").json()
e = c.get(f"/api/sinavlar/{eid}").json()
k = e["kayitlar"][before]
ok(k["durum"] == "eksik" and len(k["resimler"]) == 1 and r["acik_sayfa"] == 1, "tamamlanmış kağıdın 2. sayfası geri alındı, kağıt yeniden sayfa bekliyor")
r = c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("s9.jpg", jpg("seri 9"), "image/jpeg"))], data={"oturum": sid}).json()
k = c.get(f"/api/sinavlar/{eid}").json()["kayitlar"][before]
ok(len(k["resimler"]) == 2 and k["durum"] == "bekliyor", "yeniden çekilen sayfa aynı kağıda eklendi")
ok(main._jobs.qsize() > 0, "kuyruğa alındı")

print("Bitir: yarım kağıt da okunsun")
c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("s10.jpg", jpg("seri 10"), "image/jpeg"))], data={"oturum": sid})
c.post(f"/api/sinavlar/{eid}/oturum/{sid}/bitir")
k = c.get(f"/api/sinavlar/{eid}").json()["kayitlar"][-1]
ok(k["durum"] == "bekliyor" and k.get("eksik_sayfa"), "son yarım kağıt okumaya gönderildi")

print("Tamamla ve geri alınamayan durum")
r = c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("s11.jpg", jpg("seri 11"), "image/jpeg"))], data={"oturum": "baska-oturum"}).json()
rid = c.get(f"/api/sinavlar/{eid}").json()["kayitlar"][-1]["id"]
ok(c.post(f"/api/sinavlar/{eid}/kagit/{rid}/tamamla").status_code == 200, "eksik kağıt elle tamamlandı")

def mark_done(e):
    for x in e["kayitlar"]:
        if x["id"] == rid:
            x["durum"] = "tamam"
main.store.update_exam(eid, mark_done)
ok(c.post(f"/api/sinavlar/{eid}/oturum/baska-oturum/geri-al").status_code == 409, "okunmuş kağıt geri alınamıyor")

print("Kuyruktaki eski girdi atlanıyor")
def mark_eksik(e):
    for x in e["kayitlar"]:
        if x["id"] == rid:
            x["durum"] = "eksik"
main.store.update_exam(eid, mark_eksik)
main._process(eid, rid)  # okuma motoru çağrılmamalı
ok(next(x for x in main.store.get_exam(eid)["kayitlar"] if x["id"] == rid)["durum"] == "eksik", "durumu 'bekliyor' olmayan kağıt okunmadı")

print("Okunmakta olan kağıt geri alınabiliyor (okuma sonucu atılır)")
c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("s12.jpg", jpg("seri 12"), "image/jpeg"))], data={"oturum": "oturum-3"})
c.post(f"/api/sinavlar/{eid}/kagit", files=[("dosyalar", ("s13.jpg", jpg("seri 13"), "image/jpeg"))], data={"oturum": "oturum-3"})
rid3 = c.get(f"/api/sinavlar/{eid}").json()["kayitlar"][-1]["id"]
def mark_running3(e):
    for x in e["kayitlar"]:
        if x["id"] == rid3:
            x["durum"] = "isleniyor"
main.store.update_exam(eid, mark_running3)
ok(c.post(f"/api/sinavlar/{eid}/oturum/oturum-3/geri-al").status_code == 200, "okunan kağıdın son sayfası geri alındı")
k = next(x for x in c.get(f"/api/sinavlar/{eid}").json()["kayitlar"] if x["id"] == rid3)
ok(k["durum"] == "eksik" and len(k["resimler"]) == 1, "kağıt yeniden sayfa bekliyor")

print("Tek hücre düzeltme (eşzamanlı düzeltmeler birbirini ezmesin)")
ex3 = c.post("/api/sinavlar", json={"sinif": "X", "tam_puanlar": [10, 10, 10]}).json()
c.post(f"/api/sinavlar/{ex3['id']}/kagit", files=[("dosyalar", ("a.jpg", jpg("tek"), "image/jpeg"))])
r3 = c.get(f"/api/sinavlar/{ex3['id']}").json()["kayitlar"][0]["id"]
def done3(e):
    e["kayitlar"][0].update(durum="tamam", puanlar=[1, 2, 3], eminlik_puan=[0.5, 0.5, 0.5])
main.store.update_exam(ex3["id"], done3)
c.patch(f"/api/sinavlar/{ex3['id']}/kagit/{r3}", json={"puan": {"soru": 0, "deger": 9}})
c.patch(f"/api/sinavlar/{ex3['id']}/kagit/{r3}", json={"puan": {"soru": 2, "deger": 7}})
x = c.get(f"/api/sinavlar/{ex3['id']}").json()["kayitlar"][0]
ok(x["puanlar"] == [9, 2, 7], f"iki ayrı düzeltme korundu: {x['puanlar']}")
ok("okunan" not in x, "arayüze modelin ham çıktısı gönderilmiyor")
ok("eminlik" not in x and "Eminlik" not in " ".join(x["uyarilar"]), "kağıtta yüzdelik eminlik yok (kaldırıldı)")
ok("eminlik_esigi" not in c.get("/api/durum").json()["ayarlar"], "eminlik eşiği ayarı kaldırıldı")

print("Bozuk dosya bütün yüklemeyi durdurmuyor")
r = c.post(f"/api/sinavlar/{ex3['id']}/kagit", files=[("dosyalar", ("iyi.jpg", jpg("iyi"), "image/jpeg")),
                                                       ("dosyalar", ("bozuk.heic", b"bozuk veri", "image/heic"))], data={"gruplama": "ayri"}).json()
ok(len(r["kayitlar"]) == 1 and len(r["hatali"]) == 1, f"1 kağıt eklendi, 1 dosya bildirildi: {r['hatali']}")

print("Türkçe Excel CSV (cp1254) ve ayırıcısı anlaşılamayan CSV")
csv_bytes = "No;Adı Soyadı\n247;Ayşe Şahin\n".encode("cp1254")
r = c.post(f"/api/sinavlar/{ex3['id']}/liste", files={"dosya": ("liste.csv", csv_bytes, "text/csv")})
ok(r.status_code == 200 and r.json()["ogrenci_listesi"][0]["ad"] == "Ayşe Şahin", "cp1254 CSV doğru okundu")
r = c.post(f"/api/sinavlar/{ex3['id']}/liste", files={"dosya": ("l.csv", "247 Ali Veli\n412 Ayşe Demir\n".encode(), "text/csv")})
ok(r.status_code == 200 and len(r.json()["ogrenci_listesi"]) == 2, "boşlukla ayrılmış CSV 500 vermiyor")
r = c.post(f"/api/sinavlar/{ex3['id']}/liste", files={"dosya": ("l.xlsx", b"bozuk", "application/octet-stream")})
ok(r.status_code == 400, "bozuk Excel 400 veriyor (500 değil)")

print("Ayar sınırları")
ok(c.put("/api/ayarlar", json={"max_kenar": 0}).status_code == 422, "çözünürlük 0 reddedildi")
ok(c.get("/api/durum").json()["ayarlar"]["max_kenar"] >= 512, "çözünürlük bozulmadı")

print("Sayfa sayısı değişince açık oturum kapanıyor")
ex4 = c.post("/api/sinavlar", json={"sinif": "Y", "tam_puanlar": [10], "sayfa_sayisi": 2}).json()
c.post(f"/api/sinavlar/{ex4['id']}/kagit", files=[("dosyalar", ("p1.jpg", jpg("p1"), "image/jpeg"))], data={"oturum": "o-44"})
c.put(f"/api/sinavlar/{ex4['id']}", json={"sinif": "Y", "tam_puanlar": [10], "sayfa_sayisi": 1})
c.post(f"/api/sinavlar/{ex4['id']}/kagit", files=[("dosyalar", ("p2.jpg", jpg("p2"), "image/jpeg"))], data={"oturum": "o-44"})
ks = c.get(f"/api/sinavlar/{ex4['id']}").json()["kayitlar"]
ok([len(k["resimler"]) for k in ks] == [1, 1], "farklı öğrencilerin sayfaları birleşmedi")

print("İşçi bozuk sınav dosyasında ölmüyor")
(main.config.EXAMS_DIR / ex4["id"] / "sinav.json").write_text("{bozuk", encoding="utf-8")
main._set_error(ex4["id"], "yok", "deneme")
ok(True, "_set_error istisna fırlatmadı")

print("Bir fotoğrafta birden çok kağıt")


def sheets_photo(n: int, touching: bool = False) -> bytes:
    """Koyu masada 2x2 (ya da yan yana 2) beyaz kağıt; her kağıdın köşesine sırası yazılı."""
    W, H = (2400, 1600) if n == 2 else (1800, 2400)
    im = Image.new("RGB", (W, H), (240, 240, 238) if touching else (70, 50, 35))
    d = ImageDraw.Draw(im)
    cols, rows = (2, 1) if n == 2 else (2, 2)
    gap = 0 if touching else 60
    cw, ch = (W - gap * (cols + 1)) // cols, (H - gap * (rows + 1)) // rows
    for i in range(n):
        r_, c_ = divmod(i, cols)
        x, y = gap + c_ * (cw + gap), gap + r_ * (ch + gap)
        d.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(250, 250, 248), outline=(120, 120, 120))
        d.rectangle([x + 40, y + 40, x + 40 + 60 * (i + 1), y + 100], fill=(220, 0, 0))  # sıra işareti: i+1 birim uzun
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


ex5 = c.post("/api/sinavlar", json={"sinif": "Ç", "tam_puanlar": [10], "sayfa_sayisi": 2}).json()
ok(c.put(f"/api/sinavlar/{ex5['id']}/fotodaki-kagit", json={"deger": 3}).status_code == 400, "fotoğrafta 3 kağıt reddedildi")
ok(c.put(f"/api/sinavlar/{ex5['id']}/fotodaki-kagit", json={"deger": 4}).json()["fotodaki_kagit"] == 4, "fotoğrafta 4 kağıt kaydedildi")
r = c.post(f"/api/sinavlar/{ex5['id']}/kagit", files=[("dosyalar", ("d.jpg", sheets_photo(4), "image/jpeg"))], data={"oturum": "o-cok"}).json()
e5 = c.get(f"/api/sinavlar/{ex5['id']}").json()
ok(r["sayfa"] == 4 and [len(k["resimler"]) for k in e5["kayitlar"]] == [2, 2], "4 kağıtlı fotoğraf 2 sayfalık iki öğrenciye ayrıldı")
ok(not r["esit_bolunen"], "kağıtlar kenarlarından ayrı ayrı bulundu")
from app.bolme import split_sheets  # noqa: E402
parts, how = split_sheets(Image.open(io.BytesIO(sheets_photo(4))), 4)


def mark_len(im):
    """Sıra işaretinin (kırmızı çubuk) uzunluğu: kağıdın fotoğraftaki sırasını verir."""
    import numpy as np
    a = np.array(im.convert("RGB")).astype(int)
    red = (a[..., 0] > 150) & (a[..., 1] < 80) & (a[..., 2] < 80)
    return int(red.any(axis=0).sum())


lens = [mark_len(p) for p in parts]
ok(how == "kenar" and lens == sorted(lens), f"okuma sırası soldan sağa, yukarıdan aşağı ({lens})")
r = c.post(f"/api/sinavlar/{ex5['id']}/oturum/o-cok/geri-al").json()
ok(r["ogrenci"] == 0 and not c.get(f"/api/sinavlar/{ex5['id']}").json()["kayitlar"], "geri al fotoğraftaki dört kağıdı birlikte kaldırdı")
r = c.post(f"/api/sinavlar/{ex5['id']}/kagit", files=[("dosyalar", ("d.jpg", sheets_photo(4), "image/jpeg"))], data={"oturum": "o-cok"}).json()
ok(r["atlanan"] == 0 and r["sayfa"] == 4, "geri alınan fotoğraf yeniden gönderilebiliyor")
r = c.post(f"/api/sinavlar/{ex5['id']}/kagit", files=[("dosyalar", ("b.jpg", sheets_photo(4, touching=True), "image/jpeg"))],
           data={"gruplama": "ayri"}).json()
ok(r["sayfa"] == 4 and r["esit_bolunen"] == ["b.jpg"], "birbirine değen kağıtlar eşit bölündü ve bildirildi")
rec = c.get(f"/api/sinavlar/{ex5['id']}").json()["kayitlar"][0]
r = c.post(f"/api/sinavlar/{ex5['id']}/kagit", files=[("dosyalar", ("s.jpg", jpg("tek sayfa"), "image/jpeg"))], data={"kayit_id": rec["id"]}).json()
ok(r["sayfa"] == 1, "var olan kağıda sayfa eklerken fotoğraf bölünmüyor")

print("Kontrol ekranı için puan kutusu resimleri")
ex6 = c.post("/api/sinavlar", json={"sinif": "K", "tam_puanlar": [10, 10]}).json()
rid6 = c.post(f"/api/sinavlar/{ex6['id']}/kagit", files=[("dosyalar", ("k.jpg", jpg("kutu"), "image/jpeg"))]).json()["kayitlar"][0]


def fake_read(images, maxes, settings, progress=None, **kw):
    return {"kutular": {0: Image.new("RGB", (200, 120), "white"), 1: Image.new("RGB", (200, 120), "blue")},
            "ogrenci_no": "247", "ad_soyad": "Deniz Kaya", "puanlar": [10.0, 7.0], "yazili_toplam": None, "supheli": {},
            "eminlik": {"puan": [1.0, 1.0], "alternatif": {}, "no": 1.0, "ad": 1.0, "toplam": None, "toplam_tutuyor": False},
            "liste_secimi": None, "duzen": {}, "ham": []}


main.recognize_auto = fake_read
main._process(ex6["id"], rid6)
k6 = c.get(f"/api/sinavlar/{ex6['id']}").json()["kayitlar"][0]
names6 = list((k6.get("kutu_resimleri") or {}).values())
ok(k6["durum"] == "tamam" and len(names6) == 2, f"okunan kağıdın iki kutu resmi kaydedildi: {k6.get('kutu_resimleri')}")
ok(all(c.get(f"/api/sinavlar/{ex6['id']}/resim/{n}").status_code == 200 for n in names6), "kutu resimleri sunuluyor")
main._process(ex6["id"], rid6)  # durum 'tamam' olduğu için atlanır (yeniden okuma yalnızca kuyruktan)
c.post(f"/api/sinavlar/{ex6['id']}/kagit/{rid6}/tekrar")
main._jobs.get_nowait()
main._process(ex6["id"], rid6)
k6b = c.get(f"/api/sinavlar/{ex6['id']}").json()["kayitlar"][0]
gone = all(not (main.store.image_dir(ex6["id"]) / n).exists() for n in names6)
ok(gone and len(k6b["kutu_resimleri"]) == 2, "yeniden okununca eski kutu resimleri silindi, yenileri geldi")
c.post(f"/api/sinavlar/{ex6['id']}/resimleri-sil")
k6c = c.get(f"/api/sinavlar/{ex6['id']}").json()["kayitlar"][0]
ok(not k6c["kutu_resimleri"] and not any((main.store.image_dir(ex6["id"]) / n).exists() for n in k6b["kutu_resimleri"].values()),
   "fotoğrafları sil, kutu resimlerini de siliyor (KVKK)")

print("Tarama bulgularının düzeltmeleri")
import numpy as _np  # noqa: E402
ex7 = c.post("/api/sinavlar", json={"sinif": "T", "tam_puanlar": [10, 10], "sayfa_sayisi": 2}).json()
# 16 bitlik gri tarama beyaz sayfaya dönüşmesin
arr16 = _np.full((400, 300), 0xE000, _np.uint16)
arr16[100:200, 50:250] = 0x2000
b16 = io.BytesIO()
Image.fromarray(arr16).save(b16, "PNG")
r = c.post(f"/api/sinavlar/{ex7['id']}/kagit", files=[("dosyalar", ("g16.png", b16.getvalue(), "image/png"))],
           data={"gruplama": "ayri"}).json()
k7 = c.get(f"/api/sinavlar/{ex7['id']}").json()["kayitlar"][0]
saved = _np.asarray(Image.open(main.store.image_dir(ex7["id"]) / k7["resimler"][0]).convert("L"))
ok(saved[150, 150] < 80 and saved[10, 10] > 180, f"16 bitlik gri tarama doğru dönüştürüldü ({saved[150, 150]}, {saved[10, 10]})")
# "her 2 sayfa bir öğrenci": aradan dosya düşerse hiçbir sayfa eklenmesin (öğrenciler karışmasın)
before = len(c.get(f"/api/sinavlar/{ex7['id']}").json()["kayitlar"])
files = [("dosyalar", ("a.jpg", jpg("a1"), "image/jpeg")), ("dosyalar", ("bozuk.jpg", b"bozuk", "image/jpeg")),
         ("dosyalar", ("b.jpg", jpg("b1"), "image/jpeg")), ("dosyalar", ("c.jpg", jpg("b2"), "image/jpeg"))]
r = c.post(f"/api/sinavlar/{ex7['id']}/kagit", files=files, data={"gruplama": "sayfa"})
ok(r.status_code == 400 and len(c.get(f"/api/sinavlar/{ex7['id']}").json()["kayitlar"]) == before,
   "sıralı gruplamada bozuk dosya varken hiçbir sayfa eklenmedi")
# yeniden okunmayı bekleyen kağıt düzenlenemez; eski okumanın hatası yeni sıraya yazılmaz
def to_wait(e):
    e["kayitlar"][0].update(durum="bekliyor")
main.store.update_exam(ex7["id"], to_wait)
ok(c.patch(f"/api/sinavlar/{ex7['id']}/kagit/{k7['id']}", json={"puan": {"soru": 0, "deger": 5}}).status_code == 409,
   "okunmayı bekleyen kağıtta düzeltme reddedildi")
main._set_error(ex7["id"], k7["id"], "eski hata")
ok(c.get(f"/api/sinavlar/{ex7['id']}").json()["kayitlar"][0]["durum"] == "bekliyor", "eski okumanın hatası yeni sırayı ezmedi")
# toplu onay: uyarılı kağıt onaylanmasın
def to_done(e):
    e["kayitlar"][0].update(durum="tamam", puanlar=[None, 5], ogrenci_no="", ad_soyad="")
main.store.update_exam(ex7["id"], to_done)
ok(c.patch(f"/api/sinavlar/{ex7['id']}/kagit/{k7['id']}", json={"onaylandi": True, "yalniz_uyarisiz": True}).status_code == 409,
   "uyarılı kağıt toplu onayda onaylanmadı")
# soru sayısı değişince onay kalkar
c.patch(f"/api/sinavlar/{ex7['id']}/kagit/{k7['id']}", json={"onaylandi": True})
c.put(f"/api/sinavlar/{ex7['id']}", json={"sinif": "T", "tam_puanlar": [10, 10, 10], "sayfa_sayisi": 2})
ok(c.get(f"/api/sinavlar/{ex7['id']}").json()["kayitlar"][0]["onaylandi"] is False, "soru eklenince onay kalktı (yeni soru kontrol edilsin)")
# bozulan sinav.json yedekten okunur
f7 = main.config.EXAMS_DIR / ex7["id"] / "sinav.json"
f7.write_text("", encoding="utf-8")
ok(c.get(f"/api/sinavlar/{ex7['id']}").status_code == 200, "boş kalan sinav.json yedekten okundu")
# çapraz kontrol modeli okuma modeliyle aynı olamaz
cur_model = c.get("/api/durum").json()["ayarlar"]["model"]
ok(c.put("/api/ayarlar", json={"ikinci_model": cur_model}).status_code == 400, "çapraz kontrol modeli okuma modeliyle aynı olamaz")
# çok sayfalı PDF'in bir kaydı silinince yeniden yükleme ötekileri çoğaltmasın
ex8 = c.post("/api/sinavlar", json={"sinif": "P", "tam_puanlar": [10]}).json()
pdf3 = pdf(3)  # aynı dosya: PDF her oluşturuluşta farklı baytlar (zaman damgası) içerebilir
c.post(f"/api/sinavlar/{ex8['id']}/kagit", files=[("dosyalar", ("d.pdf", pdf3, "application/pdf"))], data={"gruplama": "ayri"})
k8 = c.get(f"/api/sinavlar/{ex8['id']}").json()["kayitlar"]
first = min(k8, key=lambda k: k["eklenme"])
c.delete(f"/api/sinavlar/{ex8['id']}/kagit/{first['id']}")
r = c.post(f"/api/sinavlar/{ex8['id']}/kagit", files=[("dosyalar", ("d.pdf", pdf3, "application/pdf"))], data={"gruplama": "ayri"}).json()
ok(r["atlanan"] == 1 and len(c.get(f"/api/sinavlar/{ex8['id']}").json()["kayitlar"]) == 2,
   "PDF'in bir kaydı silinince yeniden yükleme ötekileri çoğaltmadı")

print("Kurşun kalem süzgeci")
import numpy as np  # noqa: E402
from app import kalem  # noqa: E402
pg = Image.new("RGB", (1800, 2400), (232, 230, 225))
d = ImageDraw.Draw(pg)
for x0, col in ((300, (40, 50, 150)), (600, (190, 30, 40)), (900, (95, 95, 92)), (1200, (25, 25, 25))):
    # el yazısı "10" boyutunda: mavi, kırmızı tükenmez; kurşun kalem (gri); siyah
    d.line([(x0 - 30, 340), (x0 - 30, 460)], fill=col, width=5)
    d.ellipse([x0, 340, x0 + 60, 460], outline=col, width=5)
only = np.array(kalem.pen_and_faded(np.array(pg))[0])
kept = lambda x: int((only[330:470, x - 45:x + 70] < 200).any(axis=2).sum())  # noqa: E731
ok(kept(300) > 300 and kept(600) > 300, "mavi ve kırmızı mürekkep kaldı")
ok(kept(900) == 0 and kept(1200) == 0, "kurşun kalem ve siyah iz silindi")
ok(c.put("/api/ayarlar", json={"yalnizca_renkli_kalem": False}).json()["yalnizca_renkli_kalem"] is False, "süzgeç ayarı kapatılabiliyor")
ok(c.put("/api/ayarlar", json={"yalnizca_renkli_kalem": True}).json()["yalnizca_renkli_kalem"] is True, "süzgeç ayarı açılabiliyor")
ok(c.put("/api/ayarlar", json={"ogretmen_kalem_rengi": "kirmizi"}).json()["ogretmen_kalem_rengi"] == "kirmizi", "kalem rengi seçilebiliyor")
ok(c.put("/api/ayarlar", json={"ogretmen_kalem_rengi": "mor-ötesi"}).json()["ogretmen_kalem_rengi"] == "kirmizi",
   "geçersiz kalem rengi yok sayıldı")
c.put("/api/ayarlar", json={"ogretmen_kalem_rengi": "otomatik"})

print("Eşleştirme (ağdaki cihazlar)")
main.AUTH_DISABLED = False
c2 = TestClient(main.app)
ok(c2.get("/api/sinavlar").status_code == 401, "eşleşmemiş cihaz sınavları göremiyor")
ok("eslestirme_kodu" not in c2.get("/api/durum").json(), "eşleşmemiş cihaz kodu göremiyor")
wrong = "000000" if main._pair_code != "000000" else "111111"
ok(c2.post("/api/eslestir", json={"kod": wrong}).status_code == 400, "yanlış kod reddedildi")
ok(c2.post("/api/eslestir", json={"kod": main._pair_code}).status_code == 200, "doğru kodla eşleşti")
ok(c2.get("/api/sinavlar").status_code == 200, "eşleşen cihaz (çerezle) erişebiliyor")
main.AUTH_DISABLED = True

print("\nSONUÇ:", "HATA VAR" if failed else "hepsi geçti", "| veri:", tmp)
