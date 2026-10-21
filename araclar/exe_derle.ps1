# Masaustu programini (.exe) derler, %LOCALAPPDATA%\Programs\SinavOkuyucu altina kurar
# ve Masaustu ile Baslat menusune kisayol ekler.
$ErrorActionPreference = "Stop"
$proje = Split-Path $PSScriptRoot -Parent
$py = Join-Path $proje ".venv\Scripts\python.exe"
$is = Join-Path $env:TEMP "sinav-okuyucu-derleme"
$hedef = Join-Path $env:LOCALAPPDATA "Programs\SinavOkuyucu"

& $py -c "from PIL import Image; Image.open(r'$proje\app\static\icon-512.png').save(r'$is-icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

Push-Location $proje
try {
    & $py -m PyInstaller --noconfirm --clean --windowed --name SinavOkuyucu `
        --icon "$is-icon.ico" `
        --add-data "$proje\app\static;app\static" `
        --collect-submodules uvicorn `
        --collect-all rapidocr_onnxruntime `
        --collect-binaries onnxruntime `
        --collect-all pypdfium2 `
        --collect-all pypdfium2_raw `
        --workpath "$is\build" --distpath "$is\dist" --specpath "$is" `
        SinavOkuyucu.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller basarisiz" }
} finally { Pop-Location }

# Calisan program varsa kapat, yeni surumu kur
Get-Process SinavOkuyucu -ErrorAction SilentlyContinue | Stop-Process -Force
# Eski surumun baslattigi Ollama da klasoru kilitleyebilir; program acilinca yeniden baslatilir
Get-Process "ollama*", "llama-server" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
# Once yeni surumu yanina kopyala, sonra yer degistir: bir dosya kilitliyse eski kurulum bozulmadan kalir
$yeni = "$hedef.yeni"
$eski = "$hedef.eski"
foreach ($d in @($yeni, $eski)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
Copy-Item -Recurse "$is\dist\SinavOkuyucu" $yeni
if (Test-Path $hedef) {
    # paketle gelen Ollama ve modeller (12 GB) guncellemede silinmesin
    foreach ($sub in @("ollama", "modeller")) {
        if (Test-Path (Join-Path $hedef $sub)) { Move-Item (Join-Path $hedef $sub) (Join-Path $yeni $sub) }
    }
    Rename-Item $hedef (Split-Path $eski -Leaf)
}
Rename-Item $yeni (Split-Path $hedef -Leaf)
if (Test-Path $eski) { Remove-Item -Recurse -Force $eski -ErrorAction SilentlyContinue }

$exe = Join-Path $hedef "SinavOkuyucu.exe"
# Windows PowerShell 5 betigi ANSI okur; Turkce harfleri koddan uret (i: U+0131, g: U+011F)
$ad = "S" + [char]0x0131 + "nav Okuyucu"
$aciklama = "S" + [char]0x0131 + "nav ka" + [char]0x011F + [char]0x0131 + "tlar" + [char]0x0131 + "n" + [char]0x0131 + " okuyup Excel'e aktar" + [char]0x0131 + "r"
$sh = New-Object -ComObject WScript.Shell
foreach ($yer in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    Get-ChildItem $yer -Filter "S*nav Okuyucu.lnk" -ErrorAction SilentlyContinue | Remove-Item -Force
    $lnk = $sh.CreateShortcut((Join-Path $yer "$ad.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $hedef
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = $aciklama
    $lnk.Save()
}
Write-Output "Kuruldu: $exe"
