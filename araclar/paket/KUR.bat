@echo off
rem Sinav Okuyucu kurulumu: internet gerekmez, yapay zeka modeli paketin icindedir.
rem Not: Bu dosyada bilerek Turkce karakter kullanilmiyor; cmd.exe UTF-8 bat dosyalarini hatali okuyor.
cd /d "%~dp0"
title Sinav Okuyucu kurulumu
echo === Sinav Okuyucu kurulumu ===
echo.
if not exist "SinavOkuyucu\SinavOkuyucu.exe" (
  echo "SinavOkuyucu" klasoru bulunamadi. Paketin tamamini kopyaladiginizdan emin olun.
  pause
  exit /b 1
)

set "HEDEF=%LOCALAPPDATA%\Programs\SinavOkuyucu"
taskkill /f /im SinavOkuyucu.exe >nul 2>nul
taskkill /f /im ollama.exe >nul 2>nul
taskkill /f /im llama-server.exe >nul 2>nul

echo Dosyalar kopyalaniyor (yaklasik 12 GB, birkac dakika surebilir)...
robocopy "SinavOkuyucu" "%HEDEF%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo Kopyalama sirasinda hata olustu. Diskte yeterli yer var mi?
  pause
  exit /b 1
)

echo Masaustu ve Baslat menusu kisayollari olusturuluyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ad = 'S' + [char]0x0131 + 'nav Okuyucu';" ^
  "$exe = Join-Path $env:LOCALAPPDATA 'Programs\SinavOkuyucu\SinavOkuyucu.exe';" ^
  "$sh = New-Object -ComObject WScript.Shell;" ^
  "foreach ($yer in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $l = $sh.CreateShortcut((Join-Path $yer ($ad + '.lnk'))); $l.TargetPath = $exe;" ^
  "  $l.WorkingDirectory = (Split-Path $exe); $l.IconLocation = $exe + ',0'; $l.Save() }"

echo.
echo Kurulum tamam. Program aciliyor...
echo Ilk acilista Windows Guvenlik Duvari izin isterse "Izin ver" deyin (telefonun baglanabilmesi icin).
start "" "%HEDEF%\SinavOkuyucu.exe"
timeout /t 5 /nobreak >nul
exit /b 0
