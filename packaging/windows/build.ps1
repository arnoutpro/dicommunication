# Build a per-machine x64 MSI on Windows.
# Requires: Python 3.12+, WiX 5 (`dotnet tool install --global wix`).

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Version = $env:DICOMM_MSI_VERSION
if (-not $Version) {
    $Version = "0.1.0"
}

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean packaging\windows\dicommunication.spec
python packaging\windows\harvest.py dist\dicommunication packaging\windows\harvested.wxs

$License = Join-Path $Root "packaging\windows\License.rtf"
wix build `
    packaging\windows\Product.wxs `
    packaging\windows\harvested.wxs `
    -ext WixToolset.UI.wixext `
    -arch x64 `
    -d ProductVersion=$Version `
    -bindpath dist\dicommunication `
    -o "dist\dicommunication-$Version-win64.msi"

Write-Host "Built dist\dicommunication-$Version-win64.msi"
