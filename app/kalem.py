"""Yalnızca renkli tükenmez kalem izlerini bırakan süzgeç (kurşun kalem ve gri/siyah izler yok sayılır).

Neden: Öğretmen puanı tükenmez kalemle verir. Kurşun kalemle sonradan yazılan bir sayı (ör. puanın yanına
eklenen bir rakam) okunursa sistem kandırılabilir.

Nasıl: Fotoğrafta kurşun kalem, siyah kalem ve basılı yazı aynı görünür: hepsi renksiz gridir. Gerçek kağıtlarda
ölçüldü: öğrencinin kurşun kalemi basılı yazıdan bile koyu çıkabiliyor, yani koyuluğa bakarak ayırmak mümkün değil.
Renkli mürekkep (mavi, kırmızı, yeşil, mor) ise kağıda göre belirgin bir renk taşır. Bu yüzden:
  1. Işık ve kağıt rengi farkı giderilir (her renk kanalı, kağıdın yerel rengine bölünür; kağıt = 1).
  2. Her pikselin çevresinde, izin Lab rengi koyuluğa bölünür ("renk / koyuluk"). Bu oran kalemin ne kadar
     bastırıldığından bağımsızdır: soluk mavi de koyu lacivert de yüksek, kurşun kalem ve baskı düşük kalır.
  3. Uzun düz renksiz çizgiler (basılı sütun ayıracı, tablo) şekillerinden tanınıp dışarıda bırakılır.
  4. Oranı yüksek yerler ve onlara bitişik zayıf renkli pikseller bırakılır, geri kalan her şey beyaz kağıt olur.
Sonuç: siyah tükenmez kalemle verilen puanlar da silinir; bu yüzden süzgeç ayarlardan kapatılabilir.
Öğrenci de renkli kalem kullandıysa (çoğunlukla mavi; öğretmen kırmızı) renkli izler renk ailelerine ayrılır
(kırmızı/pembe, mavi/mor, yeşil) ve yalnızca öğretmenin kalemi bırakılabilir (bkz. ink_family, family).
Süzgeç bütün sayfaya uygulanmalı (kırpıma değil): çizgi uzunluğu sayfa boyuna göre ölçülür.
"""
from __future__ import annotations

import cv2
import numpy as np

INK_DARKNESS = 0.10    # kağıttan en az bu kadar koyu olan pikseller iz sayılır
NEUTRAL_B = 1.0        # kurşun kalem ve baskının ölçülen hafif sarılığı (Lab b)
# Renk / koyuluk oranının eşikleri. Gerçek kağıtta ölçülen (piksellerin %99'u): kurşun kalem ≤0.076, basılı yazı ≤0.10,
# kalın basılı çizgi ≤0.15; mavi tükenmez çoğunlukla 0.2-0.45, en koyu lacivert çekirdekte 0.12-0.19.
STRONG_COLOR = 0.13    # bunu geçen yerler kesin renkli mürekkep
WEAK_COLOR = 0.085     # buna kadar düşenler, kesin mürekkebin hemen yanındaysa mürekkep
CLEAR_COLOR = 0.2      # bunun üstü açıkça renkli: basılı çizgi sayılmaz
LINE_LENGTH = 0.08     # sayfanın uzun kenarının bu oranından (A4'te ~2.4 cm) uzun, renksiz düz çizgiler basılı çizgi sayılır
                       # (süzgeç bu yüzden kırpıma değil bütün sayfaya uygulanmalı)
MIN_INK_AREA = 6e-6    # sayfanın bu oranından küçük renkli lekeler (JPEG gürültüsü) atılır
MAX_HALF_WIDTH = 0.004  # çizgi yarı kalınlığı sayfanın uzun kenarının bundan fazlaysa (2400 pikselde ~20 piksel
                        # kalınlık) mürekkep değil; tükenmez çizgisi 3-6 piksel


def _background(f: np.ndarray) -> np.ndarray:
    """Kağıdın yerel rengi: koyu izleri kapatıp (morfolojik kapama) yumuşatarak bulunur. Küçültülmüş görüntüde
    hesaplanır (hızlı); kağıt rengi yavaş değiştiği için kayıp olmaz."""
    h, w = f.shape[:2]
    k = 400 / max(h, w)
    small = cv2.resize(f, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else f
    size = max(5, int(max(small.shape[:2]) / 40)) | 1
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, se)
    bg = cv2.GaussianBlur(bg, (0, 0), size / 2)
    if bg.shape[:2] != (h, w):
        bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.maximum(bg, 1.0)


def analyze(a: np.ndarray) -> dict:
    """RGB görüntüden: normalleştirilmiş görüntü, iz maskesi, renkli mürekkep maskesi."""
    f = a.astype(np.float32)
    n = np.clip(f / _background(f), 0.0, 1.0)
    dark = 1.0 - n.mean(axis=2)
    lab = cv2.cvtColor(n, cv2.COLOR_RGB2Lab)  # L 0-100, a/b renk eksenleri (gri = 0)
    ink = dark > INK_DARKNESS * 0.6
    # Komşuluk ortalaması (yalnızca iz piksellerinde; koyuluğa göre ağırlıklı): JPEG renk gürültüsü ve
    # çizgi kenarlarındaki renk saçaklanması (bir yanda mavi, öbür yanda sarı) böylece birbirini götürür.
    s = max(1.0, max(a.shape[:2]) / 1000)
    w = dark * ink
    den = np.maximum(cv2.GaussianBlur(w, (0, 0), s), 1e-3)
    ca = cv2.GaussianBlur(lab[..., 1] * w, (0, 0), s) / den
    cb = cv2.GaussianBlur(lab[..., 2] * w, (0, 0), s) / den - NEUTRAL_B
    dw = cv2.GaussianBlur(dark * w, (0, 0), s) / den  # komşuluktaki izin tipik koyuluğu
    # Renk / koyuluk: kalemin ne kadar bastırıldığından bağımsızdır (soluk mavi de koyu lacivert de aynı orana yakın).
    # Kurşun kalem ve baskı hafif sarımsı gri çıkar; sarı yöndeki fark az sayılır (sarı kalemle puan verilmez).
    color = np.hypot(ca, np.where(cb < 0, cb, cb * 0.3)) / np.maximum(100 * dw, 5.0)
    dark_s = cv2.GaussianBlur(dark, (0, 0), s * 0.6)
    strong = (dark_s > INK_DARKNESS) & (color > STRONG_COLOR)
    weak = (dark_s > INK_DARKNESS * 0.8) & (color > WEAK_COLOR)
    # Uzun düz basılı çizgiler (sütun ayıracı, tablo, çerçeve) kenarlarında renk saçaklanması taşıyabilir: şekillerinden
    # tanınıp dışarıda bırakılır. El yazısı rakamlar ve daireler bu kadar uzun ve düz değildir.
    # Yalnızca belirgin renk taşımayan çizgiler: öğretmenin uzun ve düz çektiği mavi bir "1" silinmesin.
    length = max(15, int(LINE_LENGTH * max(a.shape[:2])))
    solid = ((dark_s > 0.2) & (color < CLEAR_COLOR)).astype(np.uint8)
    lines = cv2.morphologyEx(solid, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8)) | \
        cv2.morphologyEx(solid, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8), iterations=max(1, int(round(s)))) > 0
    # çizgiyi kesen mavi mürekkep kalsın (puan rakamı basılı çerçeve çizgisinin üstünden geçebiliyor)
    lines &= color < CLEAR_COLOR
    strong &= ~lines
    weak &= ~lines
    # Çift eşik: kesin renkli bir yerin hemen yanındaki zayıf renkli pikseller de mürekkeptir (koyu lacivert
    # çizginin ortası neredeyse gri görünebilir). Yalnızca birkaç piksel uzağa kadar: mavi daireye değen basılı bir
    # çizgi ya da kurşun kalem çizgisi boydan boya mürekkebe katılmasın.
    near = max(2, int(round(1.6 * s)))
    grown = cv2.dilate(strong.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near + 1, 2 * near + 1))) > 0
    core = strong | (weak & grown)
    # Tükenmez çizgisi incedir. Kalın, dolu renkli alanlar mürekkep değildir: kağıdın üstündeki el (ten rengi), masada
    # duran kalem, fosforlu kalem. Yalnızca kalın KISIM silinir (içine belli bir çapta daire sığan yerler): bir lekenin
    # tamamını silmek, ona değen ince bir rakamı da götürüyordu (fosforlu "+" ile ona değen "1").
    rad = max(2, int(MAX_HALF_WIDTH * max(a.shape[:2])))
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1))
    thick = cv2.morphologyEx(core.astype(np.uint8), cv2.MORPH_OPEN, disk)
    # yalnızca geniş kalın alanlar (el, kalem): iki mürekkep çizgisinin birleştiği yerde oluşan tek dairelik kalınlık
    # rakamdan delik açmasın
    tn, tlab, tstats, _ = cv2.connectedComponentsWithStats(thick, connectivity=8)
    big = tstats[:, cv2.CC_STAT_AREA] >= 4 * np.pi * rad * rad
    big[0] = False
    thick = big[tlab]
    core &= ~cv2.dilate(thick.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    cnt, lab_ids, stats, _ = cv2.connectedComponentsWithStats(core.astype(np.uint8), connectivity=8)
    min_area = max(4, int(MIN_INK_AREA * a.shape[0] * a.shape[1]))
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = False
    core = keep[lab_ids]
    # çizgi kenarlarını geri kazan (yalnızca bir-iki piksel; dokunan kurşun kalem çizgisine taşmasın)
    r = max(1, int(round(s)))
    pen = cv2.dilate(core.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
    pen &= ink
    return {"norm": n, "ink": ink, "pen": pen, "color": color, "ca": ca, "cb": cb}


FAMILIES = {"kirmizi": 1, "mavi": 2, "yesil": 3}
FAMILY_NAMES = {1: "kırmızı", 2: "mavi", 3: "yeşil"}


def ink_family(an: dict) -> np.ndarray:
    """Renkli mürekkep piksellerinin renk ailesi: 1 kırmızı/pembe/turuncu, 2 mavi/mor/lacivert, 3 yeşil (0: mürekkep
    değil). Renk, piksel piksel değil her kalem çizgisinin (bağlı bileşenin) ortalama Lab renk açısından bulunur:
    fotoğrafta rengi zayıf kenar pikselleri rastgele açılara dağılıyordu (mavi kağıtta mürekkebin %12'si "yeşil" çıktı).
    Gerçek kağıtlarda ölçülen: mavi tükenmez -120..-45 derece, kırmızı -30..+15. Mor maviye, pembe kırmızıya düşer."""
    pen = an["pen"]
    num, lab = cv2.connectedComponents(pen.astype(np.uint8), connectivity=8)
    w = pen.ravel().astype(np.float64)
    ca = np.bincount(lab.ravel(), weights=an["ca"].ravel() * w, minlength=num)
    cb = np.bincount(lab.ravel(), weights=an["cb"].ravel() * w, minlength=num)
    ang = np.degrees(np.arctan2(cb, ca))
    fam = np.full(num, 2, np.uint8)                        # -170..-40 derece: mavi, mor, lacivert
    fam[(ang >= -40) & (ang < 80)] = 1                     # kırmızı, pembe, turuncu
    fam[(ang >= 80) | (ang < -170)] = 3                    # yeşil
    fam[0] = 0
    return fam[lab]


def family(an: dict, name: str | None) -> np.ndarray:
    """Renkli mürekkep maskesi; name verilirse ("kirmizi", "mavi", "yesil") yalnızca o renk ailesi."""
    if not name or name not in FAMILIES:
        return an["pen"]
    return an["pen"] & (ink_family(an) == FAMILIES[name])


def pen_only(a: np.ndarray, color: str | None = None) -> tuple[np.ndarray, float]:
    """Yalnızca renkli mürekkebi (color verilirse yalnızca o renk ailesini) bırakır, beyaz kağıt üzerinde.
    Dönüş: (görüntü, renkli mürekkep oranı)."""
    an = analyze(a)
    pen = family(an, color)
    out = np.full(a.shape, 255, np.uint8)
    out[pen] = (an["norm"][pen] * 255).astype(np.uint8)
    return out, float(pen.mean())


def pen_and_faded(a: np.ndarray, strength: float = 0.5, color: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """İki görüntü birden: (yalnızca renkli mürekkep, renksizleri soluklaştırılmış sayfa).
    İkincisi şablonsuz okumada kullanılır: renkli mürekkep olduğu gibi kalır, gri/siyah her şey (basılı yazı,
    kurşun kalem) soluklaşır. Model basılı soru numaralarını yine görür ama puan diye yalnızca renkli mürekkebe
    bakması istenir."""
    an = analyze(a)
    n, pen = an["norm"], family(an, color)
    only = np.full(a.shape, 255, np.uint8)
    only[pen] = (n[pen] * 255).astype(np.uint8)
    gray = n.mean(axis=2, keepdims=True)
    light = 1.0 - (1.0 - gray) * strength  # renksiz ve soluk
    out = np.where(pen[..., None], n, np.repeat(light, 3, axis=2))
    return only, (np.clip(out, 0, 1) * 255).astype(np.uint8)
