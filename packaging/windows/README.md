# Windows MSI

Hospital PACS PCs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller and wraps the onedir payload in a per-machine WiX MSI.

There is **one Windows installer**: `dicommunication-headless-<version>-win64.msi`. It installs the program as a Windows service (see below). Up to 0.11.2 there was also a desktop installer with its own window; it is no longer built. Installing the current MSI over an old desktop install replaces it (same upgrade code). The old desktop data in `%LOCALAPPDATA%\dicommunication` is not carried over: use **Configuration > Import / export** on the old version to export the settings, and import them in the new one.

The MSI **bundles a private Python runtime**. Nobody installs Python from python.org.

The installer cannot live *inside* this app's webpage. That page is served by the already-running Python server. Ship the MSI through IT (Intune / GPO / a USB stick), not through a button on localhost.

`ping.exe` is already on Windows. ICMP uses `-n` / `-w`; TCP to the DICOM port is still the useful check on clinical networks.

If a modality must C-FIND this workstation (local MWL SCP on port 11112), allow inbound TCP for `dicommunication.exe` in Windows Firewall. The installer only opens the web port (see below).

## Build (Windows)

Needs Python 3.12+ and the .NET SDK (for `dotnet tool install`).

```powershell
.\packaging\windows\build.ps1
```

It installs WiX and its extensions itself if they are not already there. CI does the same on `windows-latest` and uploads `dicommunication-headless-*-win64.msi` (artifact `dicommunication-headless-msi`). `build.ps1` also wraps the MSI as `dicommunication.msi.<version>.nupkg`.

A `v*` tag publishes that nupkg to GitHub Packages (`https://nuget.pkg.github.com/arnoutpro/index.json`, package id `dicommunication.msi`) and attaches the MSI and the nupkg to the GitHub Release. GitHub has no native MSI feed; this is a NuGet package whose `tools/` folder holds the installer. Installing from GitHub Packages requires a PAT even when the repo is public, so use the Release asset for anonymous downloads.

To publish an already-cut GitHub Release as a package without rebuilding, run **Windows MSI** with `nuget_from_release` set to the tag. Releases up to 0.11.2 hold the old desktop MSI under another file name, so this only works for a release that has a `dicommunication-headless-*-win64.msi`.

The MSI is **not code-signed, and will not be**: a signing certificate costs money every year and this is a hobby project. Windows SmartScreen therefore warns about a downloaded MSI. Choose **More info > Run anyway**, or right-click the file > Properties > **Unblock**. For a rollout, push it with Intune or GPO (a file installed that way is not flagged as downloaded) or allow that exact file by hash (`Get-FileHash`) in Defender or AppLocker; allowing by publisher needs a signature. Trust comes from where the file is downloaded (the GitHub Release of this repository) and from the open source and the CI workflow that builds it.

macOS also has a DMG (`packaging/macos`). Linux remains on Docker Compose.

## The installer (a Windows service, opened in the browser)

`dicommunication-headless-<version>-win64.msi` is for a workstation that should keep working with nobody at the keyboard: the Dicom Router rules and the status dashboard run all the time, not only while a window is open.

It installs the same program, but as a Windows service named **Dicommunication**. It starts with Windows, has no window, and you open it in a browser.

**What the user does**

1. Run the MSI. After the folder page the wizard asks for the **port** (default 8080) and whether **other computers on the same network may connect** (off by default).
2. Open **Dicommunication** from the Start menu, or go to `http://localhost:<port>/` (`https://` when the network option is ticked).
3. Create the first administrator before anyone signs in (sign-in is always on in this edition, also without the network option): the first visit to the page on the server itself goes to *Setup*, or run, as an administrator, `dicommunication.exe --create-admin NAME --password-file C:\path\password.txt --service-data` and delete the file.

Unattended: `msiexec /i dicommunication-headless-<version>-win64.msi /qn PORT=8090 ALLOWNETWORK=1` (leave `ALLOWNETWORK` out for this computer only). To change the port later, run the MSI again with the new value.

**How it works.** `dicommunication-service.exe` is [WinSW](https://github.com/winsw/winsw) (MIT, see `WinSW-LICENSE.txt`), fetched from NuGet by `fetch_winsw.py` and checked against a pinned SHA-256. Windows starts it, it reads `dicommunication-service.xml` in the install folder and runs `dicommunication.exe --no-browser`. That XML is written at install time by `dicommunication.exe --configure-service` (`app/service_setup.py`), from the port chosen in the wizard; do not edit it by hand, it is rewritten on reinstall.

**Where things are**

| What | Where |
| --- | --- |
| The service | `services.msc`, name *Dicommunication*; runs as `NT AUTHORITY\LocalService`, restarts itself after a crash |
| Settings, history, logs | `C:\ProgramData\Dicommunication` (own folder, readable only by administrators, SYSTEM and the service account) |
| Service host logs | `C:\ProgramData\Dicommunication\logs` |
| Setup log (why an install failed) | `C:\ProgramData\Dicommunication\service-setup.log` |

**Good to know**

- It has the **same upgrade code as the old desktop MSI**, so installing it replaces a desktop install on that PC. The service has its own data folder (`C:\ProgramData\Dicommunication`), so export the settings from the old version first and import them here.
- Sign-in (users with viewer / operator / administrator roles) is always on in this edition. Local only is the default. With "other computers" ticked the app also turns on HTTPS with a self-made certificate and the audit log (see `SECURITY.md`), and the installer opens Windows Firewall for the web port **to the local subnet only**. The DICOM listen ports (Storage and Worklist) are separate and are not opened for you.
- If the service does not start, the usual cause is that the port is taken. Check the logs above, then reinstall with another port.
- On uninstall the installer asks once whether to delete the data folder; the default is to keep it.

Build: `build.ps1` and the CI workflow build both MSIs; the headless one is the `dicommunication-headless-msi` artifact.

