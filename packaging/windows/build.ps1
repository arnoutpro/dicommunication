# Build a per-machine x64 MSI on Windows.
# Requires: Python 3.12+, .NET SDK 8 (for `dotnet tool install`).

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Version = $env:DICOMM_MSI_VERSION
if (-not $Version) {
    $Version = python -c "from app import __version__; print(__version__)"
}

python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-desktop.txt pyinstaller
python -m PyInstaller --noconfirm --clean packaging\windows\dicommunication.spec
python packaging\windows\harvest.py dist\dicommunication packaging\windows\harvested.wxs

# Same WiX + extension versions as .github/workflows/windows-msi.yml. Both
# commands are no-ops if already installed at this version.
dotnet tool install --global wix --version 5.0.2 2>$null
wix extension add -g WixToolset.UI.wixext/5.0.2 2>$null

$License = Join-Path $Root "packaging\windows\License.rtf"
wix build `
    packaging\windows\Product.wxs `
    packaging\windows\harvested.wxs `
    -ext WixToolset.UI.wixext `
    -arch x64 `
    -d ProductVersion=$Version `
    -bindpath dist\dicommunication `
    -o "dist\dicommunication-$Version-win64.msi"

python packaging\windows\pack_nuget.py "dist\dicommunication-$Version-win64.msi" --version $Version --output dist

# The service edition: same program, installed as a Windows service (see Service.wxs).
python packaging\windows\fetch_winsw.py dist\service-host
wix extension add -g WixToolset.Util.wixext/5.0.2 2>$null
wix extension add -g WixToolset.Firewall.wixext/5.0.2 2>$null
wix build `
    packaging\windows\Service.wxs `
    packaging\windows\harvested.wxs `
    -ext WixToolset.UI.wixext `
    -ext WixToolset.Util.wixext `
    -ext WixToolset.Firewall.wixext `
    -arch x64 `
    -d ProductVersion=$Version `
    -bindpath dist\dicommunication `
    -bindpath dist\service-host `
    -o "dist\dicommunication-service-$Version-win64.msi"

Write-Host "Built dist\dicommunication-$Version-win64.msi"
Write-Host "Built dist\dicommunication.msi.$Version.nupkg"
Write-Host "Built dist\dicommunication-service-$Version-win64.msi"
