@echo off
rem Sinav Okuyucu - KAYNAKTAN kurulum (gelistirici icin, internet gerekir).
rem Baska bir ogretmene vermek icin bunu degil, araclar\paket_olustur.ps1 ile olusan paketi (KUR.bat) kullanin.
rem Not: Bu dosyada bilerek Turkce karakter kullanilmiyor; cmd.exe UTF-8 bat dosyalarini hatali okuyor.
cd /d "%~dp0"
title Sinav Okuyucu kurulumu
echo === Sinav Okuyucu kurulumu (kaynaktan) ===
echo.

rem "where python" Microsoft Store kisayolunu da bulur; gercekten calisiyor mu diye dene
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 veya daha yenisi bulunamadi. https://www.python.org adresinden Python 3.12 kurun
  echo ^(kurulumda "Add python.exe to PATH" kutusunu isaretleyin^) ve bu dosyayi tekrar calistirin.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Python ortami olusturuluyor...
  python -m venv .venv
  if errorlevel 1 goto hata
)
echo [2/4] Python paketleri kuruluyor...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto hata

set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA%" (
  echo Ollama kuruluyor...
  winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
)
if not exist "%OLLAMA%" (
  echo Ollama kurulamadi. https://ollama.com/download adresinden elle kurun.
  pause
  exit /b 1
)

echo [3/4] Okuma modelleri indiriliyor (yaklasik 9 GB, ilk seferde biraz surer)...
tasklist /fi "imagename eq ollama.exe" | find /i "ollama.exe" >nul
if errorlevel 1 (
  start "Ollama" /min "%OLLAMA%" serve
  timeout /t 4 /nobreak >nul
)
"%OLLAMA%" pull qwen3-vl:8b-instruct
if errorlevel 1 goto hata
"%OLLAMA%" pull qwen3-vl:4b-instruct
if errorlevel 1 goto hata

echo [4/4] Program derlenip kuruluyor...
powershell -NoProfile -ExecutionPolicy Bypass -File "araclar\exe_derle.ps1"
if errorlevel 1 goto hata

echo.
echo Kurulum tamam. Masaustundeki "Sinav Okuyucu" programini acabilirsiniz.
pause
exit /b 0

:hata
echo.
echo Kurulum sirasinda hata olustu. Yukaridaki mesaji kontrol edin.
pause
exit /b 1
