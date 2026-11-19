# Windows MSI

Hospital PACS PCs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller and wraps the onedir payload in a per-machine WiX MSI.

The MSI **bundles a private Python runtime**. The UI opens in the app’s own window. They do not install Python from python.org.

The installer cannot live *inside* this app’s webpage. That page is served by the already-running Python server. Ship the MSI through IT (Intune / GPO / a USB stick), not through a button on localhost.

## What the user does

1. Install `dicommunication-<version>-win64.msi` (admin rights; lands in `Program Files\Dicommunication`). The setup wizard shows a single **Dicommunication** feature (on by default) with an off-by-default **Desktop shortcut** sub-feature (check it to also get an icon on the Desktop, not just the Start Menu). Upgrading from an older, multi-shortcut install removes the old Dicomtag Analytics / Dicom Anonymizer / Dicom Router / Dicom Cleaner shortcuts automatically.
2. Start **Dicommunication** from the Start menu (or the Desktop, if that shortcut was selected) — arnout.pro brand mark icon, same as the watermark. Dicomtag Analytics, Dicom Anonymizer, Dicom Router, and Dicom Cleaner are all reachable from this one window's own navigation, not separate shortcuts.
3. The UI opens in its own window (Edge WebView2, not a browser tab). Close that window to stop the server if it started it. Config lives in `%LOCALAPPDATA%\dicommunication`.

`ping.exe` is already on Windows. ICMP uses `-n` / `-w`; TCP to the DICOM port is still the useful check on clinical networks.

If a modality must C-FIND this workstation (local MWL SCP on port 11112), allow inbound TCP for `dicommunication.exe` in Windows Firewall.

## Build (Windows)

Needs Python 3.12+ and the .NET SDK (for `dotnet tool install`).

```powershell
.\packaging\windows\build.ps1
```

It installs WiX and the UI extension itself if they are not already there. CI does the same on `windows-latest` and uploads `dicommunication-*-win64.msi`. Local `build.ps1` also wraps the MSI as `dicommunication.msi.<version>.nupkg`.

A `v*` tag publishes that nupkg to GitHub Packages (`https://nuget.pkg.github.com/arnoutpro/index.json`, package id `dicommunication.msi`) and attaches it to the GitHub Release. GitHub has no native MSI feed; this is a NuGet package whose `tools/` folder holds the installer. Installing from GitHub Packages requires a PAT even when the repo is public — use the Release asset for anonymous downloads.

To publish an already-cut GitHub Release as a package without rebuilding, run **Windows MSI** with `nuget_from_release` set to the tag (for example `v0.2.0`).

Unsigned MSIs trigger SmartScreen. Code signing is a follow-up; keep the certificate out of git.

macOS also has a DMG (`packaging/macos`). Linux remains on Docker Compose.

## Service edition (runs in the background, opened in the browser)

A second installer, `dicommunication-service-<version>-win64.msi`, for a workstation that should keep working with nobody at the keyboard: the Dicom Router rules and the status dashboard run all the time, not only while a window is open.

It installs the same program, but as a Windows service named **Dicommunication**. It starts with Windows, has no window, and you open it in a browser.

**What the user does**

1. Run the MSI. After the folder page the wizard asks for the **port** (default 8080) and whether **other computers on the network may connect** (off by default).
2. Open **Dicommunication** from the Start menu, or go to `http://localhost:<port>/`.

Unattended: `msiexec /i dicommunication-service-<version>-win64.msi /qn PORT=8090 ALLOWNETWORK=1` (leave `ALLOWNETWORK` out for this computer only). To change the port later, run the MSI again with the new value.

**How it works.** `dicommunication-service.exe` is [WinSW](https://github.com/winsw/winsw) (MIT, see `WinSW-LICENSE.txt`), fetched from NuGet by `fetch_winsw.py` and checked against a pinned SHA-256. Windows starts it, it reads `dicommunication-service.xml` in the install folder and runs `dicommunication.exe --no-browser`. That XML is written at install time by `dicommunication.exe --configure-service` (`app/service_setup.py`), from the port chosen in the wizard; do not edit it by hand, it is rewritten on reinstall.

**Where things are**

| What | Where |
| --- | --- |
| The service | `services.msc`, name *Dicommunication*; runs as `NT AUTHORITY\LocalService`, restarts itself after a crash |
| Settings, history, logs | `C:\ProgramData\Dicommunication` (own folder, readable only by administrators, SYSTEM and the service account) |
| Service host logs | `C:\ProgramData\Dicommunication\logs` |
| Setup log (why an install failed) | `C:\ProgramData\Dicommunication\service-setup.log` |

**Good to know**

- It has the **same upgrade code as the desktop MSI**, so installing it replaces the desktop edition on that PC, and the other way round. The service has its own data folder, so remote nodes saved in the desktop edition must be entered again.
- Local only is the default. The app has no login (see `SECURITY.md`); with "other computers" ticked, anyone who can reach the port can use it, and the installer opens Windows Firewall for the web port. The DICOM listen ports (Storage and Worklist) are separate and are not opened for you.
- If the service does not start, the usual cause is that the port is taken. Check the logs above, then reinstall with another port.
- On uninstall the installer asks once whether to delete the data folder; the default is to keep it.

Build: `build.ps1` and the CI workflow build both MSIs; the service one is the `dicommunication-service-msi` artifact.

