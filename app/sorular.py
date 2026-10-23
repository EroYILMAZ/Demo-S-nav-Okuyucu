"""Soru numaraları: kağıtta yazdığı gibi ("1", "2", "3a", "3b", "4"...).

Bir soru A/B maddelerine ayrılmışsa ve öğretmen her maddeye ayrı puan veriyorsa her madde ayrı bir puan hücresidir
("3a", "3b"). Kağıttaki numaralar 1'den sırayla gitmiyorsa (ör. 5 atlanmış: 1, 2, 3, 4, 6, 7) numaralar kağıttaki gibi
yazılır. Şablonsuz okuma, puanın yanındaki basılı numarayı ("3)", "B.") bu numaralarla karşılaştırarak puanı doğru
hücreye bağlar; numaralar kağıttakiyle uyuşmazsa puanlar bir soru kayabilirdi.
"""
from __future__ import annotations

import re

_LABEL = re.compile(r"^(\d{1,3})([a-zçğıöşü]?)$")


def normalize(label) -> str:
    """'3-A', '3 a)', '3.B' → '3a', '3b'; '07' → '7'."""
    s = re.sub(r"[\s\-.)(:/]", "", str(label or "")).replace("I", "ı").replace("İ", "i").lower()
    m = _LABEL.match(s)
    return f"{int(m.group(1))}{m.group(2)}" if m else s


def parse(label: str) -> tuple[int | None, str | None]:
    """'3b' → (3, 'b'); '4' → (4, None); geçersizse (None, None)."""
    m = _LABEL.match(normalize(label))
    if not m:
        return None, None
    return int(m.group(1)), (m.group(2) or None)


def defaults(n: int) -> list[str]:
    return [str(i + 1) for i in range(n)]


def labels_of(exam: dict) -> list[str]:
    """Sınavın soru numaraları; tanımlanmamışsa ya da soru sayısıyla uyuşmuyorsa 1..n."""
    n = len(exam.get("tam_puanlar") or [])
    names = [normalize(x) for x in (exam.get("soru_adlari") or [])]
    return names if len(names) == n and not check(names) else defaults(n)


def check(names: list[str]) -> str | None:
    """Geçersizse açıklama, geçerliyse None. Numaralar kağıttaki sırayla artmalı; maddeler aynı sorunun içinde a, b, c
    diye sıralanmalı."""
    seen = set()
    prev: tuple[int, str] | None = None
    for raw in names:
        lab = normalize(raw)
        q, sub = parse(lab)
        if q is None:
            return f"“{raw}” geçerli bir soru numarası değil (örnek: 3 ya da 3a)"
        if lab in seen:
            return f"{lab} numarası iki kez yazılmış"
        seen.add(lab)
        key = (q, sub or "")
        if prev is not None and key <= prev:
            return f"Soru numaraları kağıttaki sırayla artmalı ({lab} yanlış yerde)"
        prev = key
    for lab in seen:
        q, sub = parse(lab)
        if sub is None and any(parse(o)[0] == q and parse(o)[1] for o in seen):
            return f"{q}. soru hem bölünmemiş hem maddeli yazılmış"
    return None


def has_parts(names: list[str]) -> bool:
    return any(parse(x)[1] for x in names)


def remap(old: list[str], new: list[str]) -> list[tuple[str, list[int]]]:
    """Soru numaraları değişince her yeni hücrenin eski hücrelerdeki karşılığı (okunmuş puanlar ve şablon kutuları
    taşınırken). Her yeni hücre için: ("ayni", [i]) aynı soru; ("toplam", [i, j, ...]) maddeler tek soruda birleşti
    (3a + 3b → 3); ("bolundu", [i]) eski soru maddelere bölündü (3 → 3a, 3b); ("yeni", []) karşılığı yok.
    Hücre sayısı aynı ve bölme/birleştirme yoksa yalnızca numara değişmiştir (5 → 6): sırayla eşlenir.
    Konuma göre eşlemek (sondan doldurup sondan kesmek) ortadaki bir bölmeden sonra puanları bir sütun kaydırıyordu."""
    old = [normalize(x) for x in old]
    new = [normalize(x) for x in new]
    po = [parse(x) for x in old]
    plain = {q for q, p in po if p is None}
    parted = {q for q, p in po if p}

    def structural() -> bool:
        for lab in new:
            q, p = parse(lab)
            if (p and q in plain) or (p is None and q in parted):
                return True
        return False

    if len(old) == len(new) and not structural():
        return [("ayni", [i]) for i in range(len(new))]
    idx = {lab: i for i, lab in enumerate(old)}
    out = []
    for lab in new:
        q, p = parse(lab)
        if lab in idx:
            out.append(("ayni", [idx[lab]]))
        elif p is None and q in parted:
            out.append(("toplam", [i for i, (q2, p2) in enumerate(po) if q2 == q and p2]))
        elif p and str(q) in idx:
            out.append(("bolundu", [idx[str(q)]]))
        else:
            out.append(("yeni", []))
    return out
