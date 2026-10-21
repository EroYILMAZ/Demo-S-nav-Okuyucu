# Baska bir bilgisayara verilebilecek, yapay zeka modeli DAHIL tek parca paket olusturur.
# Alan kisinin internetten hicbir sey indirmesi gerekmez.
#
# Kullanim:  powershell -ExecutionPolicy Bypass -File araclar\paket_olustur.ps1 [-Hedef C:\...] [-Sadece8b]
# Cikti:     <Hedef>\SinavOkuyucu-Paket\
#              KUR.bat              -> cift tiklayinca kurar, masaustune kisayol koyar
#              OKU-BENI.txt
#              SinavOkuyucu\        -> program + ollama\ + modeller\
#
# Not: Paket ~12 GB'dir (8B + 4B model). OneDrive klasorune koymayin; USB bellek/harici disk ile tasiyin.
param(
    [string]$Hedef = (Join-Path $env:USERPROFILE "SinavOkuyucu-Paket"),
    [switch]$Sadece8b
)
$ErrorActionPreference = "Stop"
$proje = Split-Path $PSScriptRoot -Parent
$derleme = Join-Path $env:TEMP "sinav-okuyucu-derleme\dist\SinavOkuyucu"
$ollamaKaynak = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$modelKaynak = Join-Path $env:USERPROFILE ".ollama\models"
$modeller = @("8b-instruct")
if (-not $Sadece8b) { $modeller += "4b-instruct" }

if ($Hedef -like "*OneDrive*") { throw "Paketi OneDrive klasorune koymayin (12 GB buluta yuklenir). Baska bir -Hedef verin." }

# 1) Program: en guncel derlemeyi kullan (yoksa derle)
if (-not (Test-Path (Join-Path $derleme "SinavOkuyucu.exe"))) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "exe_derle.ps1")
}
$paket = $Hedef
$prog = Join-Path $paket "SinavOkuyucu"
if (Test-Path $paket) { Remove-Item -Recurse -Force $paket }
New-Item -ItemType Directory -Force $prog | Out-Null
Write-Output "[1/4] Program kopyalaniyor..."
robocopy $derleme $prog /E /NFL /NDL /NJH /NJS /NP | Out-Null

# 2) Ollama (AMD ROCm kutuphaneleri haric: AMD kartlarda da Vulkan kullanilir)
Write-Output "[2/4] Ollama kopyalaniyor..."
$ollamaHedef = Join-Path $prog "ollama"
robocopy $ollamaKaynak $ollamaHedef /E /XD rocm* /XF "unins*" "ollama app.exe" /NFL /NDL /NJH /NJS /NP | Out-Null
if (-not (Test-Path (Join-Path $ollamaHedef "ollama.exe"))) { throw "Ollama bulunamadi: $ollamaKaynak" }

# 3) Modeller: yalnizca gereken manifest ve bloblar
Write-Output "[3/4] Modeller kopyalaniyor ($($modeller -join ', '))... bu biraz surer"
$mHedef = Join-Path $prog "modeller"
foreach ($tag in $modeller) {
    $man = Join-Path $modelKaynak "manifests\registry.ollama.ai\library\qwen3-vl\$tag"
    if (-not (Test-Path $man)) { throw "Model yuklu degil: qwen3-vl:$tag  (once: ollama pull qwen3-vl:$tag)" }
    $manHedef = Join-Path $mHedef "manifests\registry.ollama.ai\library\qwen3-vl"
    New-Item -ItemType Directory -Force $manHedef | Out-Null
    Copy-Item $man (Join-Path $manHedef $tag)
    $j = Get-Content $man -Raw | ConvertFrom-Json
    $digests = @($j.config.digest) + @($j.layers | ForEach-Object { $_.digest })
    New-Item -ItemType Directory -Force (Join-Path $mHedef "blobs") | Out-Null
    foreach ($d in $digests) {
        $name = $d -replace ":", "-"
        $src = Join-Path $modelKaynak "blobs\$name"
        $dst = Join-Path $mHedef "blobs\$name"
        if (-not (Test-Path $dst)) { Copy-Item $src $dst }
    }
}

# 4) Kurulum dosyalari
Write-Output "[4/4] Kurulum dosyalari yaziliyor..."
Copy-Item (Join-Path $PSScriptRoot "paket\KUR.bat") (Join-Path $paket "KUR.bat")
Copy-Item (Join-Path $PSScriptRoot "paket\OKU-BENI.txt") (Join-Path $paket "OKU-BENI.txt")

$boyut = (Get-ChildItem $paket -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
Write-Output ("Paket hazir: {0}  ({1:N1} GB)" -f $paket, $boyut)
