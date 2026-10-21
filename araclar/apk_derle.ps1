# Android uygulamasini derler ve bilgisayar programinin telefona sunabilmesi icin app\static altina kopyalar.
# Once araclar\android_araclari_indir.ps1 calistirilmis olmali.
$ErrorActionPreference = "Stop"
$proje = Split-Path $PSScriptRoot -Parent
$tools = Join-Path $env:USERPROFILE "android-build"
$env:JAVA_HOME = (Get-Content "$tools\jdk_yolu.txt").Trim()
$env:ANDROID_HOME = "$tools\sdk"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
$gradle = Join-Path (Get-Content "$tools\gradle_yolu.txt").Trim() "bin\gradle.bat"

$android = Join-Path $proje "android"
"sdk.dir=$($env:ANDROID_HOME -replace '\\','\\')" | Set-Content "$android\local.properties" -Encoding ASCII

# Ikonlar
$py = Join-Path $proje ".venv\Scripts\python.exe"
& $py -c @"
from PIL import Image
src = Image.open(r'$proje\app\static\icon-512.png')
for d, s in {'mdpi':48,'hdpi':72,'xhdpi':96,'xxhdpi':144,'xxxhdpi':192}.items():
    import os; p = rf'$android\app\src\main\res\mipmap-{d}'; os.makedirs(p, exist_ok=True)
    src.resize((s, s), Image.LANCZOS).save(p + r'\ic_launcher.png')
"@

Push-Location $android
try {
    & $gradle --no-daemon -q assembleRelease
    if ($LASTEXITCODE -ne 0) { throw "Gradle derlemesi basarisiz" }
} finally { Pop-Location }

$apk = "$android\app\build\outputs\apk\release\app-release.apk"
Copy-Item $apk "$proje\app\static\SinavOkuyucu.apk" -Force
Write-Output ("APK hazir: app\static\SinavOkuyucu.apk ({0:N1} MB)" -f ((Get-Item $apk).Length / 1MB))
