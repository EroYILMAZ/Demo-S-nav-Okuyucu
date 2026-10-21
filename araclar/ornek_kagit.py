"""Test için el yazısı görünümlü sahte sınav kağıtları üretir.

Kullanım: python araclar/ornek_kagit.py <çıktı_klasörü>
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path("C:/Windows/Fonts")
PRINT = ImageFont.truetype(str(FONTS / "arial.ttf"), 30)
PRINT_B = ImageFont.truetype(str(FONTS / "arialbd.ttf"), 38)
HAND = [FONTS / "segoepr.ttf", FONTS / "Inkfree.ttf"]

STUDENTS = [
    ("101", "Ali Yılmaz", [8, 10, 5, 7, 12, 9, 15, 4, 10, 6]),
    ("117", "Ayşe Demir", [10, 10, 10, 9, 15, 10, 13, 10, 8, 10]),
    ("124", "Mehmet Çelik", [3, 6, 0, 5, 8, 4, 7.5, 2, 5, 0]),
]
MAXES = [10, 10, 10, 10, 15, 10, 15, 10, 10, 10]


def hand(size):
    return ImageFont.truetype(str(random.choice([p for p in HAND if p.exists()])), size)


def make(no, name, scores, out: Path, table: bool):
    W, H = 1700, 2300
    im = Image.new("RGB", (W, H), (250, 250, 246))
    d = ImageDraw.Draw(im)
    d.text((W // 2, 70), "2026-2027 EĞİTİM ÖĞRETİM YILI", font=PRINT_B, fill="black", anchor="mm")
    d.text((W // 2, 120), "9. SINIF MATEMATİK 1. DÖNEM 1. YAZILI SINAVI", font=PRINT_B, fill="black", anchor="mm")
    d.text((90, 190), "Adı Soyadı:", font=PRINT, fill="black")
    d.text((90, 245), "Numara:", font=PRINT, fill="black")
    blue = (25, 45, 140)
    d.text((270, 178), name, font=hand(44), fill=blue)
    d.text((230, 233), no, font=hand(44), fill=blue)
    red = (200, 20, 20)

    y0 = 330
    if table:
        # üstte puan tablosu
        cw, x0 = 125, 90
        d.text((x0, y0), "Soru", font=PRINT, fill="black")
        d.text((x0, y0 + 60), "Puan", font=PRINT, fill="black")
        for i in range(10):
            x = x0 + 110 + i * cw
            d.rectangle([x, y0 - 10, x + cw, y0 + 110], outline="black", width=2)
            d.line([x, y0 + 45, x + cw, y0 + 45], fill="black", width=2)
            d.text((x + cw // 2, y0 + 18), str(i + 1), font=PRINT, fill="black", anchor="mm")
            d.text((x + cw // 2 + random.randint(-8, 8), y0 + 80), f"{scores[i]:g}".replace(".", ","), font=hand(40), fill=red, anchor="mm")
        y0 += 170
    total = sum(scores)
    d.ellipse([W - 300, 170, W - 110, 300], outline=red, width=4)
    d.text((W - 205, 235), f"{total:g}".replace(".", ","), font=hand(64), fill=red, anchor="mm")

    for i in range(10):
        y = y0 + i * 175
        d.text((90, y), f"{i + 1}) Aşağıdaki işlemin sonucunu bulunuz. ({MAXES[i]} puan)", font=PRINT, fill="black")
        d.text((140, y + 55), f"{random.randint(2, 99)} + {random.randint(2, 99)} x {random.randint(2, 9)} = ...", font=hand(36), fill=blue)
        if not table:
            d.text((W - 200 + random.randint(-15, 15), y + 40), f"{scores[i]:g}".replace(".", ","), font=hand(54), fill=red)

    # telefon fotoğrafı gibi: hafif döndür, bulanıklaştır, gölge
    im = im.rotate(random.uniform(-2.5, 2.5), expand=True, fillcolor=(90, 80, 70), resample=Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(0.8))
    shade = Image.linear_gradient("L").resize(im.size).point(lambda v: 255 - v // 6)
    im = Image.composite(im, Image.new("RGB", im.size, (0, 0, 0)), shade)
    im.thumbnail((2000, 2000))
    im.save(out, "JPEG", quality=85)


if __name__ == "__main__":
    random.seed(7)
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ornek_kagitlar")
    out.mkdir(parents=True, exist_ok=True)
    for k, (no, name, scores) in enumerate(STUDENTS):
        make(no, name, scores, out / f"kagit_{no}.jpg", table=(k == 1))
    print("Üretildi:", out.resolve())
