# Android APK derlemek icin gereken araclari (JDK 17, Android komut satiri araclari, Gradle)
# C:\Users\<kullanici>\android-build klasorune indirir. Sisteme kurulum yapmaz.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Join-Path $env:USERPROFILE "android-build"
New-Item -ItemType Directory -Force $root | Out-Null
Set-Location $root

function Get-Zip($url, $zip, $dest) {
    if (Test-Path $dest) { Write-Output "zaten var: $dest"; return }
    Write-Output "indiriliyor: $url"
    Invoke-WebRequest -UseBasicParsing $url -OutFile $zip
    Expand-Archive $zip -DestinationPath "$dest.tmp" -Force
    Move-Item "$dest.tmp" $dest
    Remove-Item $zip
}

Get-Zip "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse" "jdk.zip" "$root\jdk"
Get-Zip "https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip" "cmdline.zip" "$root\cmdline"
Get-Zip "https://services.gradle.org/distributions/gradle-8.10.2-bin.zip" "gradle.zip" "$root\gradle"

# sdkmanager, cmdline-tools'un sdk\cmdline-tools\latest altinda olmasini bekler
$latest = "$root\sdk\cmdline-tools\latest"
if (-not (Test-Path $latest)) {
    New-Item -ItemType Directory -Force "$root\sdk\cmdline-tools" | Out-Null
    Move-Item "$root\cmdline\cmdline-tools" $latest
}
Get-ChildItem "$root\jdk" -Directory | Select-Object -First 1 -ExpandProperty FullName | Set-Content "$root\jdk_yolu.txt"
Get-ChildItem "$root\gradle" -Directory | Select-Object -First 1 -ExpandProperty FullName | Set-Content "$root\gradle_yolu.txt"
Write-Output "TAMAM"
