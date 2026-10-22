"""Uygulama ayarları. Ayarlar data/ayarlar.json dosyasında saklanır."""
import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Veriler (öğrenci fotoğrafları, puanlar) OneDrive gibi bulutla eşitlenen klasörlere konmasın diye
# kullanıcının yerel uygulama klasöründe tutulur.
DATA_DIR = Path(os.environ.get("SINAV_OKUYUCU_VERI") or Path(os.environ["LOCALAPPDATA"]) / "SinavOkuyucu" / "data")
EXAMS_DIR = DATA_DIR / "sinavlar"
SETTINGS_FILE = DATA_DIR / "ayarlar.json"

DEFAULTS = {
    # Ollama yerel sunucusu (bilgisayar dışına veri çıkmaz)
    "ollama_url": "http://127.0.0.1:11434",
    # Görüntü okuyabilen yerel model. Gerçek kağıt denemesinde 8b 10/10, 4b 9/10 doğru okudu.
    "model": "qwen3-vl:8b-instruct",
    # Modele gönderilen her parçanın (sayfa/sütun) uzun kenarı (piksel). Büyüdükçe doğruluk artar, hız düşer.
    "max_kenar": 1400,
    # Tek bir model çağrısı için zaman aşımı (saniye). Ekran kartı zayıfsa artırın.
    "zaman_asimi": 600,
    # Ekran kartı kullanımı: "otomatik" (önce CUDA dener, çökerse Vulkan'a geçip bunu hatırlar), "vulkan", "cuda", "cpu"
    "ollama_gpu": "otomatik",
    # Puanlar yalnızca renkli tükenmez kalemden (mavi, kırmızı, yeşil, mor) okunur; kurşun kalem ve siyah/gri izler
    # yok sayılır (kurşun kalemle sonradan yazılan bir rakam sistemi kandıramasın). Öğretmen siyah kalem kullanıyorsa kapatılır.
    "yalnizca_renkli_kalem": True,
    # Çapraz kontrol için ikinci (farklı eğitilmiş) model: okunan her puan bununla bir kez daha okunur, tutmazsa hücre
    # kontrole düşer. Varsayılan kapalı: denenen minicpm-v:8b Türkçe el yazısında ana modelden zayıf çıktı (açık uçlu
    # "4"ü "1"/"7" okudu) ve doğru okunan hücrelerin ~%20'sini gereksiz yere kontrole düşürdü; sessiz hataları ise
    # süzgeçsiz bakış zaten yakalıyor. "kapali" ya da bir model adı.
    "ikinci_model": "kapali",
}

_lock = threading.Lock()


def load_settings() -> dict:
    with _lock:
        data = dict(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return data


# Sayısal ayarların geçerli aralıkları: boş bırakılan bir alan 0 olarak kaydedilip okumayı bozmasın
RANGES = {"max_kenar": (512, 3000), "zaman_asimi": (30, 3600)}


def save_settings(changes: dict) -> dict:
    current = load_settings()
    for key in DEFAULTS:
        if key not in changes or changes[key] in (None, ""):
            continue
        if key == "ollama_gpu":
            if changes[key] in ("otomatik", "vulkan", "cuda", "cpu"):
                current[key] = changes[key]
            continue
        if isinstance(DEFAULTS[key], bool):
            current[key] = changes[key] in (True, 1, "1", "true", "evet")
            continue
        try:
            value = type(DEFAULTS[key])(changes[key])
        except (TypeError, ValueError):
            continue
        if key in RANGES:
            lo, hi = RANGES[key]
            if not lo <= value <= hi:
                continue
        if key == "model" and not str(value).strip():
            continue
        current[key] = value
    current.pop("eminlik_esigi", None)  # kaldırılan ayar
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:  # yarım yazılmış ayar dosyası kalmasın
            fh.write(json.dumps(current, ensure_ascii=False, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS_FILE)
    return current
