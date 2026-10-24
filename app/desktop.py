"""Sınav Okuyucu masaüstü programı.

Ollama'yı ve yerel sunucuyu başlatır, arayüzü tarayıcı yerine kendi penceresinde açar.
Pencere kapanınca sunucu ve Ollama da kapanır.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from . import config

PORT = 8000
URL = f"http://127.0.0.1:{PORT}"
CREATE_NO_WINDOW = 0x08000000


def _log_file():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return open(config.DATA_DIR / "program.log", "a", encoding="utf-8", buffering=1)


def _already_running() -> bool:
    try:
        return "sinav-okuyucu" in httpx.get(URL + "/api/durum", timeout=2).text
    except httpx.HTTPError:
        return False


APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def _ollama_exe() -> str | None:
    """Önce programla birlikte gelen (paketlenmiş) Ollama, yoksa bilgisayara kurulu olan."""
    import shutil
    for p in (APP_DIR / "ollama" / "ollama.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe"):
        if p.exists():
            return str(p)
    return shutil.which("ollama")


def _bundled_models() -> Path | None:
    """Paketle gelen modeller (program klasöründeki 'modeller'): başka bilgisayarda internetten indirmeye gerek kalmaz."""
    p = APP_DIR / "modeller"
    return p if (p / "manifests").exists() else None


# Okuma modelleri, doğruluk sırasıyla (gerçek kağıtlardaki 44 puan kırpımında, tek bakışta: qwen3.5:9b 44/44,
# qwen3-vl:8b 40/44). Küçük bellekli bilgisayarlar için küçük modeller ayrı.
BIG_MODELS = ["qwen3.5:9b", "qwen3-vl:8b-instruct"]
SMALL_MODELS = ["qwen3.5:4b", "qwen3-vl:4b-instruct"]
OLD_DEFAULTS = {"qwen3-vl:8b-instruct"}


def _models_dir() -> Path:
    return _bundled_models() or Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")


def _installed(name: str) -> bool:
    repo, _, tag = name.partition(":")
    return (_models_dir() / "manifests" / "registry.ollama.ai" / "library" / repo / (tag or "latest")).exists()


def _choose_model_for_this_pc(log) -> None:
    """İlk açılışta: bellek azsa (12 GB altı) büyük model sığmayabilir, küçük modele geç; varsayılan model yüklü değilse
    yüklü olan en doğru modeli seç. Eski varsayılanı (qwen3-vl:8b) kullananlar, daha doğru okuyan yeni varsayılan
    yüklüyse bir kez ona geçirilir; öğretmen sonra eskisini seçerse bir daha dokunulmaz."""
    if config.SETTINGS_FILE.exists():
        mark = config.DATA_DIR / "model_gecisi_qwen35"
        new = config.DEFAULTS["model"]
        if not mark.exists() and config.load_settings().get("model") in OLD_DEFAULTS and _installed(new):
            config.save_settings({"model": new})
            mark.touch()
            print(f"Okuma modeli {new} oldu (daha doğru okuyor).", file=log)
        return  # öğretmen ayarları daha önce kaydedildi; başka bir şeye dokunma
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        total_gb = st.ullTotalPhys / 1024 ** 3
    except Exception:
        total_gb = None
    small = total_gb is not None and total_gb < 12
    pick = next((m for m in (SMALL_MODELS + BIG_MODELS if small else BIG_MODELS + SMALL_MODELS) if _installed(m)), None)
    if pick and pick != config.DEFAULTS["model"]:
        config.save_settings({"model": pick})
        print(f"Bellek {total_gb or 0:.1f} GB: {pick} modeli seçildi.", file=log)


def _gpu_env(mode: str) -> dict:
    """Ekran kartı seçimi: cuda (Ollama'nın varsayılanı), vulkan (eski NVIDIA sürücüleri, AMD, Intel), cpu."""
    if mode == "vulkan":
        return {"OLLAMA_VULKAN": "1", "CUDA_VISIBLE_DEVICES": "-1"}
    if mode == "cpu":
        return {"OLLAMA_VULKAN": "0", "CUDA_VISIBLE_DEVICES": "-1", "HIP_VISIBLE_DEVICES": "-1"}
    return {}


def _start_ollama(log, mode: str | None = None) -> subprocess.Popen | None:
    exe = _ollama_exe()
    if not exe:
        return None
    # Bilgisayarda başka ayarlarla (ör. açılışta kendiliğinden) çalışan Ollama'yı kapat; bizim ayarlarımızla başlat
    for name in ("ollama app.exe", "ollama.exe", "llama-server.exe"):
        subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, creationflags=CREATE_NO_WINDOW)
    env = dict(os.environ)
    models = _bundled_models()
    if models is not None:
        env["OLLAMA_MODELS"] = str(models)
    mode = mode or config.load_settings().get("ollama_gpu", "otomatik")
    env.update(_gpu_env("cuda" if mode == "otomatik" else mode))
    # Ollama bu programın klasöründeki DLL'leri yüklemesin (yoksa güncelleme sırasında klasörü kilitler)
    own = str(Path(sys.executable).parent).lower()
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if not p.lower().startswith(own))
    return subprocess.Popen(
        [exe, "serve"], env=env, cwd=str(Path(exe).parent), stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW
    )


def _stop_ollama(proc: subprocess.Popen) -> None:
    # ollama, modeli ayrı bir llama-server sürecinde çalıştırır; süreç ağacının tamamını kapat
    subprocess.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)


def _model_works() -> tuple[bool, str]:
    """Modeli bir kez yükleyip çok kısa bir yanıt iste: ekran kartı sürücüsü uyumsuzsa Ollama burada çöker."""
    settings = config.load_settings()
    base = settings["ollama_url"].rstrip("/")
    for _ in range(30):  # Ollama'nın açılmasını bekle
        try:
            httpx.get(base + "/api/version", timeout=2)
            break
        except httpx.HTTPError:
            time.sleep(1)
    try:
        r = httpx.post(base + "/api/generate", json={"model": settings["model"], "prompt": "1", "stream": False,
                                                    "keep_alive": "30m", "options": {"num_predict": 1, "num_ctx": 8192}},
                       timeout=300)
    except httpx.HTTPError as e:
        return False, str(e)
    if r.status_code == 404:
        return True, "model yok"  # model yüklü değil: ekran kartıyla ilgisi yok, kurulum gerekli
    return r.status_code == 200, r.text[:300]


class _Engine:
    """Ollama sürecini tutar; 'otomatik' modda çalışan ekran kartı yolunu bulup hatırlar."""

    def __init__(self, log):
        self.log = log
        self.proc = _start_ollama(log)
        if self.proc is None:
            os.environ["SINAV_OKUYUCU_OLLAMA_YOK"] = "1"
        elif config.load_settings().get("ollama_gpu", "otomatik") == "otomatik":
            threading.Thread(target=self._auto_gpu, daemon=True).start()

    def _auto_gpu(self) -> None:
        for mode in ("cuda", "vulkan", "cpu"):
            if mode != "cuda":
                print(f"Ekran kartı denemesi başarısız; {mode} ile yeniden başlatılıyor.", file=self.log)
                if self.proc is not None:
                    _stop_ollama(self.proc)
                self.proc = _start_ollama(self.log, mode)
            ok, detail = _model_works()
            print(f"Ollama {mode}: {'çalışıyor' if ok else 'başarısız'} ({detail[:120]})", file=self.log)
            if ok:
                config.save_settings({"ollama_gpu": mode})
                return

    def stop(self) -> None:
        if self.proc is not None:
            _stop_ollama(self.proc)


def _start_server(log):
    import uvicorn

    from .main import app

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, log_config=None, access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return server


PORT_BUSY_HTML = """<html><body style="font:15px Segoe UI,sans-serif;padding:24px;color:#1b2230">
<h3 style="margin-top:0">Sınav Okuyucu başlatılamadı</h3>
<p>Bilgisayardaki başka bir program 8000 numaralı bağlantı noktasını kullanıyor.
O programı kapatıp Sınav Okuyucu'yu yeniden açın. Sorun sürerse bilgisayarı yeniden başlatın.</p></body></html>"""


class Api:
    """Pencere içindeki sayfanın çağırabildiği masaüstü işlemleri (window.pywebview.api)."""

    def __init__(self):
        self._window = None

    def excel_kaydet(self, exam_id: str):
        import webview

        resp = httpx.get(f"{URL}/api/sinavlar/{exam_id}/excel", timeout=30)
        resp.raise_for_status()
        name = "sinav.xlsx"
        cd = resp.headers.get("content-disposition", "")
        if "filename*=UTF-8''" in cd:
            from urllib.parse import unquote
            name = unquote(cd.split("filename*=UTF-8''", 1)[1].strip('"; '))
        dialog = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        path = self._window.create_file_dialog(
            dialog,
            directory=str(Path.home() / "Documents"),
            save_filename=name,
            file_types=("Excel dosyası (*.xlsx)",),
        )
        if not path:
            return None
        path = path[0] if isinstance(path, (list, tuple)) else path
        if not str(path).lower().endswith(".xlsx"):
            path = str(path) + ".xlsx"
        try:
            Path(path).write_bytes(resp.content)
        except PermissionError:
            raise RuntimeError("Dosya kaydedilemedi: aynı adlı dosya Excel'de açık olabilir. Kapatıp tekrar deneyin.")
        except OSError as e:
            raise RuntimeError(f"Dosya kaydedilemedi: {e.strerror or e}")
        return str(path)

    def klasoru_ac(self, path: str):
        subprocess.run(["explorer", "/select,", path], creationflags=CREATE_NO_WINDOW)


def main() -> None:
    import webview

    log = _log_file()
    # pencereli (konsolsuz) çalışmada stdout/stderr yoktur; günlüğe yönlendir
    if sys.stdout is None or getattr(sys, "frozen", False):
        sys.stdout = sys.stderr = log

    engine = server = None
    if not _already_running():
        _choose_model_for_this_pc(log)
        engine = _Engine(log)
        server = _start_server(log)
        if not server.started:
            # 8000 portunu başka bir program tutuyor: yanlış bir sayfa göstermek yerine açıkça söyle
            if engine is not None:
                engine.stop()
            webview.create_window("Sınav Okuyucu", html=PORT_BUSY_HTML, width=560, height=300)
            webview.start()
            return

    webview.settings["ALLOW_DOWNLOADS"] = True
    api = Api()
    window = webview.create_window("Sınav Okuyucu", URL, width=1280, height=860, min_size=(420, 600), js_api=api)
    api._window = window

    def on_closing():
        # kağıtlar okunurken pencere kapanırsa okuma durur: sor
        try:
            from .main import _queue_count
            waiting = _queue_count() if server is not None else 0
        except Exception:
            waiting = 0
        if waiting:
            try:
                ok = window.create_confirmation_dialog(
                    "Sınav Okuyucu", f"{waiting} kağıt hâlâ okunuyor. Program kapanırsa okuma durur "
                    "(sonraki açılışta kaldığı yerden devam eder). Yine de kapatılsın mı?")
            except Exception:
                ok = True
            if not ok:
                return False
        return True

    def on_closed():
        if server is not None:
            server.should_exit = True
        if engine is not None:
            engine.stop()

    window.events.closing += on_closing
    window.events.closed += on_closed
    webview.start(private_mode=False, storage_path=str(config.DATA_DIR / "webview"))


if __name__ == "__main__":
    main()
