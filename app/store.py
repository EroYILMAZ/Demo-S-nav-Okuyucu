"""Sınav ve kağıt kayıtlarının diskte (JSON) saklanması."""
import json
import os
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


def write_durable(path: Path, text: str) -> None:
    """Dosyayı diske gerçekten yazıp (fsync) öyle yerine koyar: elektrik kesilir ya da bilgisayar kapanırsa dosya
    boş/bozuk kalmasın (yalnızca yeniden adlandırma günlüğe yazılıp veri yazılmamış olabiliyordu)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_exam_dir(d: Path) -> dict:
    """sinav.json okunamazsa (bozuk/boş) son sağlam kopyaya (sinav.json.bak) döner."""
    for name in ("sinav.json", "sinav.json.bak"):
        try:
            data = json.loads((d / name).read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                return data
        except (OSError, ValueError):
            continue
    raise KeyError(d.name)


def list_exams() -> list[dict]:
    with _lock:
        if not EXAMS_DIR.exists():
            return []
        exams = []
        for d in EXAMS_DIR.iterdir():
            if not d.is_dir():
                continue
            try:
                exams.append(_read_exam_dir(d))
            except KeyError:
                continue
        exams.sort(key=lambda e: e.get("olusturma", 0), reverse=True)
        return exams


def get_exam(exam_id: str) -> dict:
    with _lock:
        d = _exam_dir(exam_id)
        if not d.is_dir():
            raise KeyError(exam_id)
        return _read_exam_dir(d)


def save_exam(exam: dict) -> None:
    with _lock:
        d = _exam_dir(exam["id"])
        (d / "resimler").mkdir(parents=True, exist_ok=True)
        f = d / "sinav.json"
        if f.exists():
            try:
                old = f.read_text(encoding="utf-8")
                json.loads(old)  # yalnızca sağlam bir önceki sürüm yedeklensin
                write_durable(d / "sinav.json.bak", old)
            except (OSError, ValueError):
                pass
        write_durable(f, json.dumps(exam, ensure_ascii=False, indent=1))


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
        write_durable(CLASSES_FILE, json.dumps(data, ensure_ascii=False, indent=1))


def remove_tree(d: Path, tries: int = 20) -> bool:
    """Klasörü siler; Windows'ta o an açık bir dosya (sunulan resim, virüs taraması) varsa kısa süre yeniden dener."""
    for _ in range(tries):
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            return True
        time.sleep(0.1)
    return not d.exists()


def unlink(path: Path, tries: int = 20) -> bool:
    """Dosyayı siler; açıksa kısa süre yeniden dener. Silinemezse False (öğrenci fotoğrafı sessizce kalmasın)."""
    for _ in range(tries):
        try:
            path.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(0.1)
        except OSError:
            return False
    return False


def delete_exam(exam_id: str) -> None:
    with _lock:
        if not remove_tree(_exam_dir(exam_id)):
            raise OSError("Bazı fotoğraflar şu an kullanımda olduğu için silinemedi")


def cleanup_orphans() -> None:
    """Silinirken bir dosyası açık kalmış sınav klasörlerini temizle (sinav.json'u ve yedeği olmayan)."""
    with _lock:
        if not EXAMS_DIR.exists():
            return
        for d in EXAMS_DIR.iterdir():
            if d.is_dir() and not (d / "sinav.json").exists() and not (d / "sinav.json.bak").exists():
                remove_tree(d, tries=3)


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
