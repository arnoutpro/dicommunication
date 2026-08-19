# Windows MSI

Hospital PACS PCs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller and wraps the onedir payload in a per-machine WiX MSI.

The MSI **bundles a private Python runtime**. The person who opens the UI only needs a browser. They do not install Python from python.org.

The installer cannot live *inside* this app’s webpage. That page is served by the already-running Python server. Ship the MSI through IT (Intune / GPO / a USB stick), not through a button on localhost.

## What the user does

1. Install `dicommunication-<version>-win64.msi` (admin rights; lands in `Program Files\Dicommunication`).
2. Start **Dicommunication** from the Start menu.
3. Leave the console window open. The default browser opens [http://127.0.0.1:8080](http://127.0.0.1:8080).
4. Config stays in `%LOCALAPPDATA%\dicommunication` across upgrades and uninstalls.

`ping.exe` is already on Windows. ICMP uses `-n` / `-w`; TCP to the DICOM port is still the useful check on clinical networks.

If a modality must C-FIND this workstation (local MWL SCP on port 11112), allow inbound TCP for `dicommunication.exe` in Windows Firewall.

## Build (Windows)

```powershell
dotnet tool install --global wix
wix extension add -g WixToolset.UI.wixext
.\packaging\windows\build.ps1
```

CI does the same on `windows-latest` and uploads `dicommunication-*-win64.msi`.

Unsigned MSIs trigger SmartScreen. Code signing is a follow-up; keep the certificate out of git.

Docker Compose remains the supported path on macOS and Linux.
