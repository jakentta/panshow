# Builds Panshow:  dist\Panshow\  (the program)  and  dist\Panshow-Setup-<version>.exe  (the installer)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$version = (& $py -c "import sys; sys.path.insert(0, '.'); from panshow import version; print(version.VERSION)").Trim()
Write-Host "Building Panshow $version"

# 1. The program folder (with product name and version shown in Windows file properties)
New-Item -ItemType Directory -Force build | Out-Null
& $py tools\make_version_info.py build\version_info.txt
if ($LASTEXITCODE -ne 0) { throw "version information failed" }
& $py -m PyInstaller --noconfirm --clean --windowed --name Panshow `
    --icon panshow\panshow.ico `
    --version-file build\version_info.txt `
    --add-data "panshow\models\face_detection_yunet_2023mar.onnx;panshow\models" `
    --add-data "panshow\models\object_detection_yolox_2022nov.onnx;panshow\models" `
    --add-data "panshow\panshow.ico;panshow" `
    --collect-submodules glcontext `
    --collect-binaries glfw `
    --collect-data _soundfile_data --collect-binaries _soundfile_data `
    --collect-data _sounddevice_data --collect-binaries _sounddevice_data `
    --collect-data soundcard `
    --hidden-import pi_heif --hidden-import soundfile --hidden-import sounddevice --hidden-import soundcard `
    --exclude-module matplotlib --exclude-module scipy --exclude-module pillow_heif `
    Panshow.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# OpenCV brings a 25+ MB video library that Panshow never uses.
Get-ChildItem dist\Panshow\_internal -Recurse -Filter 'opencv_videoio_ffmpeg*.dll' | Remove-Item -Force
# Sound output: keep only the normal 64-bit PortAudio library. The "-asio" versions include
# Steinberg's ASIO, whose licence Panshow does not need or want; 32-bit/ARM ones are unused.
Get-ChildItem dist\Panshow\_internal -Recurse -Filter 'libportaudio*.dll' |
    Where-Object { $_.Name -ne 'libportaudio64bit.dll' } | Remove-Item -Force

Copy-Item -Force package\README.txt dist\Panshow\README.txt
& $py tools\make_notices.py dist\Panshow\THIRD-PARTY-NOTICES.txt
if ($LASTEXITCODE -ne 0) { throw "licence notices failed" }

# 2. Privacy check: no personal details may end up in what is shipped. Checked for: this Windows
# user name, and any words (one per line) in privacy-words.txt next to this script, a private file
# that is never uploaded.
$personal = @($env:USERNAME)
$wordsFile = Join-Path $PSScriptRoot 'privacy-words.txt'
if (Test-Path $wordsFile) { $personal += Get-Content $wordsFile | ForEach-Object { $_.Trim() } | Where-Object { $_ } }
$found = @()
foreach ($f in (Get-ChildItem panshow -Recurse -File -Include *.py, *.txt) + (Get-ChildItem dist\Panshow -File) + (Get-ChildItem package -File)) {
    $text = [IO.File]::ReadAllText($f.FullName, [Text.Encoding]::GetEncoding(28591))
    foreach ($p in $personal) { if ($text.IndexOf($p, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $found += "$($f.Name): '$p'" } }
}
if ($found) { throw "Personal details found in shipped files: $($found -join '; ')" }
Write-Host "Privacy check passed"

# 3. The installer (needs Inno Setup 6)
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 is not installed, so no Setup.exe was made (the program folder is ready)."
    exit 0
}
& $iscc /Q "/DAppVersion=$version" installer\panshow.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Built: $(Resolve-Path "dist\Panshow-Setup-$version.exe")"
