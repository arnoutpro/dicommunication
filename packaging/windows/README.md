# Windows MSI

Hospital PACS PCs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller and wraps the onedir payload in a per-machine WiX MSI.

The MSI **bundles a private Python runtime**. The UI opens in the app’s own window. They do not install Python from python.org.

The installer cannot live *inside* this app’s webpage. That page is served by the already-running Python server. Ship the MSI through IT (Intune / GPO / a USB stick), not through a button on localhost.

## What the user does

1. Install `dicommunication-<version>-win64.msi` (admin rights; lands in `Program Files\Dicommunication`).
2. Start **Dicommunication** or **Vue PACS Database Analytics** from the Start menu (**Sansation Bold** A icon, same as the watermark).
3. Each UI opens in its own window (Edge WebView2, not a browser tab). Close that window to stop the server if it started it. Both tools share `%LOCALAPPDATA%\dicommunication`.

`ping.exe` is already on Windows. ICMP uses `-n` / `-w`; TCP to the DICOM port is still the useful check on clinical networks.

If a modality must C-FIND this workstation (local MWL SCP on port 11112), allow inbound TCP for `dicommunication.exe` in Windows Firewall.

## Build (Windows)

```powershell
dotnet tool install --global wix
wix extension add -g WixToolset.UI.wixext
.\packaging\windows\build.ps1
```

CI does the same on `windows-latest` and uploads `dicommunication-*-win64.msi`. Local `build.ps1` also wraps the MSI as `dicommunication.msi.<version>.nupkg`.

A `v*` tag publishes that nupkg to GitHub Packages (`https://nuget.pkg.github.com/arnoutpro/index.json`, package id `dicommunication.msi`) and attaches it to the GitHub Release. GitHub has no native MSI feed; this is a NuGet package whose `tools/` folder holds the installer. Installing from GitHub Packages requires a PAT even when the repo is public — use the Release asset for anonymous downloads.

To publish an already-cut GitHub Release as a package without rebuilding, run **Windows MSI** with `nuget_from_release` set to the tag (for example `v0.2.0`).

Unsigned MSIs trigger SmartScreen. Code signing is a follow-up; keep the certificate out of git.

macOS also has a DMG (`packaging/macos`). Linux remains on Docker Compose.
