# macOS installer (DMG with a service package)

Hospital Macs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller into a command-line program, wraps it in an installer package (`.pkg`) that registers a **launchd background service**, and puts that package in a DMG. It behaves like the Windows headless installer: the service starts with the Mac, needs nobody logged in, has no window, always asks for sign-in, and you open it in a browser.

The package **bundles a private Python runtime**. Nobody installs Python from python.org.

The installer cannot live *inside* this app's webpage. That page is served by the already-running Python server. Ship the DMG through IT (or a USB stick), not through a button on localhost.

## What the user does

1. Open `dicommunication-<version>-macos-arm64.dmg` and run `Dicommunication.pkg`. It asks for an administrator password. The build is unsigned: if macOS refuses it, right-click the package and choose **Open**, or allow it under System Settings > Privacy & Security.
2. Open `http://localhost:8080/` on that Mac (or the **Dicommunication** shortcut in Applications). The first visit goes to **Setup**, where you create the first administrator. Or: `sudo dicommunication service create-admin NAME`.
3. Manage the service in Terminal:

```bash
sudo dicommunication service status
sudo dicommunication service network on      # other computers may connect (HTTPS); "off" for this Mac only
sudo dicommunication service port 8090
sudo dicommunication service create-admin NAME
sudo dicommunication service uninstall       # asks about the data; --delete-data / --keep-data skip the question
```

Unattended: put `{"port": 8090, "listen": "network"}` in `/Library/Application Support/Dicommunication/service.json` first, then `sudo installer -pkg Dicommunication.pkg -target /`. An upgrade keeps whatever port and network option are saved there. Nothing is asked in the installer window; everything is changed afterwards with the commands above.

## What gets installed

| What | Where |
| --- | --- |
| The program | `/usr/local/lib/dicommunication/` and a link `/usr/local/bin/dicommunication` |
| The launchd job | `/Library/LaunchDaemons/pro.arnout.dicommunication.plist` (runs at boot, restarts after a crash) |
| The account | `_dicommunication`, hidden, no login shell, a user id from 300 to 399 |
| Settings, history, app log | `/Library/Application Support/Dicommunication` (mode 0700, owned by the account; can hold patient data) |
| Service log (rotated by newsyslog, 5 x 5 MB) | `/var/log/dicommunication/service.log` |
| Installer log | `/var/log/dicommunication-install.log` |
| Shortcut | `/Applications/Dicommunication.webloc` (opens the browser) |

The old drag-to-Applications app (up to 0.12) is removed by the installer's pre-install step (only if it is ours: the bundle identifier is checked) because it would start a second server on the same port. Its settings in `~/.dicommunication` are not carried over: use Configuration > Import / export first.

`ping` is already on macOS. ICMP uses `-c` / `-W`; TCP to the DICOM port is still the useful check on clinical networks. If a modality must C-FIND this Mac (local MWL SCP on port 11112), allow incoming connections for that port in the macOS firewall. With `network on` the installer adds an application firewall exception for the program, but macOS cannot limit it to the local subnet the way the Windows installer does.

## How it fits together

- `app/macos_service.py` writes the launchd job, the data folder, the settings file (`service.json`) and the shortcut, and implements `dicommunication service ...`. The post-install script only creates the account and runs `dicommunication --configure-service --from-saved --reload`.
- `scripts/preinstall` stops a running service (an upgrade) and removes the old app. `scripts/postinstall` does the account and the configure step. `resources/` holds the installer's welcome and conclusion pages.
- `make_pkg.py` stages the payload and runs `pkgbuild` and `productbuild`; `make_dmg.py` wraps the package with a Read Me. `build.sh` does all of it.
- `smoke_test.sh` installs the DMG for real, checks the service, signs in, upgrades, and uninstalls. CI runs it after uploading the DMG.

## Architectures

CI builds on `macos-latest` (Apple Silicon, `arm64`). Intel Macs should keep using Docker Compose until an `x86_64` DMG exists.

## Build (macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
./packaging/macos/build.sh
```

CI does the same and uploads `dicommunication-*-macos-arm64.dmg`. A `v*` tag attaches that DMG to the GitHub Release next to the Windows MSI.

To attach a DMG to an already-cut tag without making a new version, run **macOS DMG** with `release_tag` set to the tag (for example `v0.2.0`).

The package is not signed or notarized, and will not be. Keep any certificate out of git.

Linux remains on Docker Compose. Windows uses the MSI in `packaging/windows`.
