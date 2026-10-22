"""Şablonlu okuma: her öğrencinin fotoğrafı şablona hizalanır, her sorunun puan kutusu ayrı ayrı yakın planda okunur.

Neden: Kağıdın tamamına bakıp "hangi puan hangi soruya ait" diye modele sormak, soru numarası çemberin altında
kaldığında, fotoğraf kırpıldığında ya da sorular "Örnek 3" gibi numaralandığında yanlış eşleşmelere yol açıyordu.
Şablonda öğretmen, her sorunun puanının yazıldığı bölgeyi bir kez işaretler; sonra:
  1. Fotoğraf, basılı içerik (her kopyada aynı) üzerinden şablona oturtulur (ORB öznitelikleri + RANSAC homografi).
  2. Her soru kutusu kırpılır; kutu soruyu belirlediği için eşleştirme hatası olmaz.
  3. Her kutu birkaç farklı biçimde okunur (sıkı kırpım, geniş kırpım, yalnızca öğretmenin renkli mürekkebi)
     ve oylanır. Oylar ayrışırsa ek okumalar yapılır; yine de ayrışırsa hücre kontrole düşer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

MIN_INLIERS = 60  # bundan az eşleşme: fotoğraf şablona güvenle oturtulamadı
MIN_SPAN = 0.3    # eşleşen noktalar şablon sayfasının en az bu kadar enine ve boyuna yayılmalı


@dataclass
class Box:
    soru: int        # 1..n; 0 = kimlik (ad/numara), -1 = kağıdın toplamı
    sayfa: int       # şablon sayfası (0'dan)
    x: float         # sayfaya göre oransal (0-1)
    y: float
    w: float
    h: float

    @staticmethod
    def from_dict(d: dict) -> "Box":
        return Box(int(d["soru"]), int(d["sayfa"]), float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))


def to_array(im: Image.Image) -> np.ndarray:
    return np.array(im.convert("RGB"))


class Aligner:
    """Şablon sayfalarının özniteliklerini bir kez çıkarır; her fotoğrafı en iyi eşleşen sayfaya oturtur."""

    def __init__(self, pages: list[np.ndarray]):
        self.pages = pages
        self.orb = cv2.ORB_create(nfeatures=8000)
        self.feats = [self._features(p) for p in pages]
        self.last_cover = None

    def _features(self, a: np.ndarray):
        g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
        k = 1400 / max(g.shape)
        g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)  # gölge/ışık farkını azalt
        kp, des = self.orb.detectAndCompute(g, None)
        return kp, des, k

    def _homography(self, src_feat, tpl_feat, src_shape, tpl_shape):
        kp1, d1, k1 = src_feat
        kp2, d2, k2 = tpl_feat
        if d1 is None or d2 is None or len(kp1) < 10 or len(kp2) < 10:
            return None, 0
        matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
        good = [m[0] for m in matches if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
        if len(good) < 12:
            return None, 0
        p1 = np.float32([kp1[g.queryIdx].pt for g in good]) / k1
        p2 = np.float32([kp2[g.trainIdx].pt for g in good]) / k2
        H, mask = cv2.findHomography(p1, p2, cv2.RANSAC, 4.0)
        if H is None:
            return None, 0
        inliers = int(mask.sum())
        # Eşleşmeler sayfanın yalnızca bir şeridinde toplandıysa (her sınavda aynı olan okul başlığı) fotoğraf başka bir
        # kağıt olabilir: kutular o kağıdın alakasız yerlerine düşer ve oradaki bir renkli işaret puan diye okunurdu.
        inl = p2[mask.ravel() == 1]
        span = (inl.max(axis=0) - inl.min(axis=0)) / np.float32([tpl_shape[1], tpl_shape[0]])
        if span.min() < MIN_SPAN:
            return None, 0
        # aşırı bükülmüş (yanlış) eşleşmeleri ele: fotoğrafın köşeleri dışbükey bir dörtgene gitmeli
        h, w = src_shape[:2]
        corners = cv2.perspectiveTransform(np.float32([[[0, 0]], [[w, 0]], [[w, h]], [[0, h]]]), H)
        if not cv2.isContourConvex(corners.astype(np.float32)):
            return None, 0
        return H, inliers

    def align(self, photo: np.ndarray, page_hint: int | None = None) -> tuple[int | None, np.ndarray | None, int]:
        """Dönüş: (şablon sayfası, şablon koordinatlarına oturtulmuş fotoğraf, eşleşme sayısı)."""
        feat = self._features(photo)
        order = list(range(len(self.pages)))
        if page_hint is not None and page_hint in order:
            order.remove(page_hint)
            order.insert(0, page_hint)
        best = (None, None, 0)
        for i in order:
            H, inl = self._homography(feat, self.feats[i], photo.shape, self.pages[i].shape)
            if H is not None and inl > best[2]:
                best = (i, H, inl)
            if page_hint == i and inl >= 3 * MIN_INLIERS:
                break  # beklenen sayfa güçlü eşleşti; diğerlerine bakmaya gerek yok
        i, H, inl = best
        if i is None or inl < MIN_INLIERS:
            return None, None, inl
        tpl = self.pages[i]
        warped = cv2.warpPerspective(photo, H, (tpl.shape[1], tpl.shape[0]), flags=cv2.INTER_CUBIC,
                                     borderValue=(255, 255, 255))
        # fotoğrafın şablonda kapladığı alan: kağıt fotoğrafta kırpılmışsa dışarıda kalan kutular beyaz görünür ve
        # kesik rakam bütün bakışlarda aynı (yanlış) okunurdu
        self.last_cover = cv2.warpPerspective(np.full(photo.shape[:2], 255, np.uint8), H, (tpl.shape[1], tpl.shape[0]),
                                              flags=cv2.INTER_NEAREST, borderValue=0) > 0
        return i, warped, inl


def crop(page: np.ndarray, box: Box, pad: float = 0.0) -> np.ndarray:
    H, W = page.shape[:2]
    x1, y1 = box.x * W, box.y * H
    x2, y2 = x1 + box.w * W, y1 + box.h * H
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    x1, y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
    x2, y2 = int(min(W, x2 + px)), int(min(H, y2 + py))
    return page[y1:y2, x1:x2]


def teacher_ink(a: np.ndarray) -> tuple[np.ndarray, float]:
    """Yalnızca renkli (mavi/kırmızı/yeşil) mürekkebi bırak; kurşun kalem ve basılı siyah yazı silinir.
    Öğretmenler çoğunlukla renkli kalemle puan verir; öğrenci kurşun kalem ya da siyah kalem kullanır."""
    f = a.astype(np.float32)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    gray = (R + G + B) / 3
    blue = B - (R + G) / 2
    red = R - (G + B) / 2
    green = G - (R + B) / 2
    blue -= np.median(blue)
    red -= np.median(red)
    green -= np.median(green)
    dark = gray < np.median(gray) - 25
    mask = dark & ((blue > 8) | (red > 18) | (green > 14))
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    out = np.full_like(a, 255)
    out[mask] = a[mask]
    return out, float(mask.mean())


def weighted_vote(items: list[tuple[float | None, int]], probs: list[float] | None = None) -> tuple[float | None, float, float]:
    """Ağırlıklı çoğunluk oyu. items: [(değer ya da None, ağırlık)]. Dönüş: (değer, kazananın ağırlığı, toplam ağırlık).
    None ("puan yok") de bir oy sayılır; böylece kutuda puan yokken tek bir yanlış okuma kazanmaz.
    Beraberlikte modelin daha emin olduğu (ortalama olasılığı yüksek) değer kazanır."""
    total = float(sum(w for _, w in items))
    if not items:
        return None, 0.0, 0.0
    weights: dict = {}
    conf: dict = {}
    for i, (v, w) in enumerate(items):
        weights[v] = weights.get(v, 0) + w
        conf.setdefault(v, []).append(probs[i] if probs else 0.0)
    val, top = max(weights.items(), key=lambda kv: (kv[1], sum(conf[kv[0]]) / len(conf[kv[0]])))
    return val, float(top), total


def vote(values: list[float | None]) -> tuple[float | None, int, int]:
    """Çoğunluk oyu. Dönüş: (değer, aynı oyu verenlerin sayısı, geçerli oy sayısı)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None, 0, 0
    (val, n), = Counter(vals).most_common(1)
    return val, n, len(vals)
