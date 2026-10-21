"""Sınav ve kağıt kayıtlarının diskte (JSON) saklanması."""
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import EXAMS_DIR

_lock = threading.RLock()


def _exam_dir(exam_id: str) -> Path:
    # exam_id dışarıdan gelir; dizin dışına çıkmayı engelle
    if not exam_id.isalnum():
        raise KeyError(exam_id)
    return EXAMS_DIR / exam_id


def _exam_file(exam_id: str) -> Path:
    return _exam_dir(exam_id) / "sinav.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def list_exams() -> list[dict]:
    with _lock:
        if not EXAMS_DIR.exists():
            return []
        exams = []
        for f in EXAMS_DIR.glob("*/sinav.json"):
            try:
                exams.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        exams.sort(key=lambda e: e.get("olusturma", 0), reverse=True)
        return exams


def get_exam(exam_id: str) -> dict:
    with _lock:
        f = _exam_file(exam_id)
        if not f.exists():
            raise KeyError(exam_id)
        return json.loads(f.read_text(encoding="utf-8"))


def save_exam(exam: dict) -> None:
    with _lock:
        d = _exam_dir(exam["id"])
        (d / "resimler").mkdir(parents=True, exist_ok=True)
        tmp = d / "sinav.json.tmp"
        tmp.write_text(json.dumps(exam, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "sinav.json")


def create_exam(ad: str, sinif: str, ders: str, tam_puanlar: list[float], sayfa_sayisi: int = 1) -> dict:
    exam = {
        "id": new_id(),
        "ad": ad,
        "sinif": sinif,
        "ders": ders,
        "olusturma": time.time(),
        "tam_puanlar": tam_puanlar,
        "sayfa_sayisi": sayfa_sayisi,
        # bu sınıfın listesi daha önce kaydedildiyse kendiliğinden ekle
        "ogrenci_listesi": get_class_roster(sinif),
        "kayitlar": [],
    }
    save_exam(exam)
    return exam


# ---------------------------------------------------------------- sınıf listeleri (sınıf başına bir kez)

CLASSES_FILE = EXAMS_DIR.parent / "siniflar.json"


def class_key(sinif: str) -> str:
    """'10-C', '10/C', '10 c' aynı sınıf sayılsın."""
    from .matching import tr_norm
    return tr_norm(sinif).replace(" ", "")


def _load_classes() -> dict:
    try:
        return json.loads(CLASSES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_classes() -> dict:
    with _lock:
        return {k: {"ad": v["ad"], "sayi": len(v["liste"])} for k, v in _load_classes().items()}


def get_class_roster(sinif: str) -> list[dict]:
    key = class_key(sinif)
    if not key:
        return []
    with _lock:
        return list(_load_classes().get(key, {}).get("liste", []))


def save_class_roster(sinif: str, roster: list[dict]) -> None:
    key = class_key(sinif)
    if not key:
        return
    with _lock:
        data = _load_classes()
        data[key] = {"ad": sinif, "liste": roster, "guncelleme": time.time()}
        CLASSES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CLASSES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CLASSES_FILE)


def delete_exam(exam_id: str) -> None:
    with _lock:
        shutil.rmtree(_exam_dir(exam_id), ignore_errors=True)


def image_dir(exam_id: str) -> Path:
    return _exam_dir(exam_id) / "resimler"


def update_exam(exam_id: str, fn) -> dict:
    """Sınavı kilit altında okuyup fn(exam) ile değiştirip kaydeder."""
    with _lock:
        exam = get_exam(exam_id)
        fn(exam)
        save_exam(exam)
        return exam


def find_record(exam: dict, record_id: str) -> dict:
    for r in exam["kayitlar"]:
        if r["id"] == record_id:
            return r
    raise KeyError(record_id)
