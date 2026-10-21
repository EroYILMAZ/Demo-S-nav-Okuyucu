"""Tek fotoğrafta yan yana duran 2 ya da 4 kağıdı ayrı sayfalara böler.

Öğretmen kağıtları masaya dizip tek fotoğraf çeker; her kağıt okuma sırasına göre (soldan sağa, yukarıdan aşağı)
ayrı bir sayfa olur. Kağıtlar:
  1. Masadan ayırt edilebiliyorsa (kağıt açık renkli ve renksiz, masa daha koyu ya da renkli) her biri kendi
     dörtgeninden bulunur ve eğikliği düzeltilerek kesilir.
  2. Birbirine değiyor ya da masa da beyazsa, kağıtların kapladığı alan eşit parçalara bölünür. Parçalar biraz
     geniş kesilir; şablonlu okumada her parça zaten şablona oturtulduğu için komşu kağıdın kenarı sorun olmaz.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

WORK = 1000          # algılama bu boyuta küçültülmüş görüntüde yapılır
GRID_MARGIN = 0.03   # eşit bölmede her parça komşusuna doğru bu kadar taşar
SHEET_MARGIN = 0.03  # ayrı bulunan kağıt bu kadar geniş kesilir


def _paper_mask(small: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    v = cv2.GaussianBlur(hsv[..., 2], (0, 0), 3)
    sat = cv2.GaussianBlur(hsv[..., 1], (0, 0), 3)
    thr, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = ((v > max(thr, 90)) & (sat < 70)).astype(np.uint8)
    k = max(3, int(max(small.shape[:2]) / 60))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    return mask


def paper_region(a: np.ndarray) -> np.ndarray:
    """Fotoğrafta kağıdın kapladığı alan (True). Kağıdın içindeki yazı ve daireler alana dahildir; masa, klavye ve
    kağıdın kenarından içeri giren el dışarıda kalır. Kağıt bulunamazsa bütün fotoğraf."""
    H, W = a.shape[:2]
    k = WORK / max(H, W)
    small = cv2.resize(a, (max(1, int(W * k)), max(1, int(H * k))), interpolation=cv2.INTER_AREA) if k < 1 else a
    m = _paper_mask(small)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1 or stats[1:, cv2.CC_STAT_AREA].max() < 0.15 * m.size:
        return np.ones((H, W), bool)
    m = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(m)
    cv2.drawContours(filled, cnts, -1, 1, -1)  # içteki delikler (mürekkep, daireler) dolsun
    filled = cv2.erode(filled, np.ones((5, 5), np.uint8))
    return cv2.resize(filled, (W, H), interpolation=cv2.INTER_NEAREST) > 0


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Dörtgenin köşeleri: sol üst, sağ üst, sağ alt, sol alt."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.float32([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])


def _warp_quad(a: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = quad
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(a, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _reading_order(boxes: list[tuple[float, float]], n: int) -> list[int]:
    """Merkezlerden okuma sırası: önce satırlar (yukarıdan aşağı), satır içinde soldan sağa."""
    idx = sorted(range(len(boxes)), key=lambda i: boxes[i][1])
    if n == 4:
        rows = [sorted(idx[:2], key=lambda i: boxes[i][0]), sorted(idx[2:], key=lambda i: boxes[i][0])]
        return rows[0] + rows[1]
    # iki kağıt: yan yanaysa soldan sağa, alt alta ise yukarıdan aşağı
    (x0, y0), (x1, y1) = boxes[idx[0]], boxes[idx[1]]
    if abs(x0 - x1) > abs(y0 - y1):
        return sorted(idx, key=lambda i: boxes[i][0])
    return idx


def _grid(a: np.ndarray, rect: tuple[int, int, int, int], n: int) -> list[np.ndarray]:
    """Dikdörtgeni n eşit parçaya böler (2: uzun kenar boyunca, 4: 2x2), parçalar biraz taşar."""
    x, y, w, h = rect
    if n == 2:
        cells = [(0, 0, 0.5, 1), (0.5, 0, 0.5, 1)] if w >= h else [(0, 0, 1, 0.5), (0, 0.5, 1, 0.5)]
    else:
        cells = [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)]
    H, W = a.shape[:2]
    out = []
    for cx, cy, cw, ch in cells:
        x1 = x + (cx - GRID_MARGIN) * w
        y1 = y + (cy - GRID_MARGIN) * h
        x2 = x + (cx + cw + GRID_MARGIN) * w
        y2 = y + (cy + ch + GRID_MARGIN) * h
        out.append(a[int(max(0, y1)):int(min(H, y2)), int(max(0, x1)):int(min(W, x2))])
    return out


def split_sheets(im: Image.Image, n: int) -> tuple[list[Image.Image], str]:
    """Fotoğraftaki n kağıdı (2 ya da 4) okuma sırasıyla ayrı resimler olarak döndürür.
    İkinci değer yöntem: "kenar" (kağıtlar tek tek bulundu) ya da "esit" (eşit bölündü)."""
    if n <= 1:
        return [im], "tek"
    a = np.array(im.convert("RGB"))
    H, W = a.shape[:2]
    k = WORK / max(H, W)
    small = cv2.resize(a, (int(W * k), int(H * k)), interpolation=cv2.INTER_AREA) if k < 1 else a.copy()
    k = small.shape[1] / W
    mask = _paper_mask(small)
    area = mask.shape[0] * mask.shape[1]
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    big = sorted((c for c in cnts if cv2.contourArea(c) >= 0.25 * area / n), key=cv2.contourArea, reverse=True)

    if len(big) >= n:
        sheets = big[:n]
        sizes = [cv2.contourArea(c) for c in sheets]
        if min(sizes) >= 0.5 * max(sizes):  # benzer büyüklükte n ayrı kağıt
            quads, centers = [], []
            for c in sheets:
                rect = cv2.minAreaRect(c)
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                pts = approx.reshape(-1, 2).astype(np.float32) if len(approx) == 4 else cv2.boxPoints(rect)
                # biraz dışa genişlet: kenara düşen gölge kağıdın bir şeridini maskenin dışında bırakmış olabilir
                center = pts.mean(axis=0)
                pts = center + (pts - center) * (1 + SHEET_MARGIN)
                pts[:, 0] = np.clip(pts[:, 0], 0, small.shape[1] - 1)
                pts[:, 1] = np.clip(pts[:, 1], 0, small.shape[0] - 1)
                quads.append(_order_quad(pts / k))
                centers.append(tuple(rect[0]))
            order = _reading_order(centers, n)
            return [Image.fromarray(_warp_quad(a, quads[i])) for i in order], "kenar"

    # kağıtlar birbirine değiyor (tek büyük beyaz alan) ya da masadan ayrılamıyor: kapladıkları alanı eşit böl
    if big:
        x, y, w, h = cv2.boundingRect(np.vstack(big[:n]))
        rect = (int(x / k), int(y / k), int(w / k), int(h / k))
        if rect[2] * rect[3] < 0.35 * W * H:
            rect = (0, 0, W, H)
    else:
        rect = (0, 0, W, H)
    return [Image.fromarray(p) for p in _grid(a, rect, n)], "esit"
