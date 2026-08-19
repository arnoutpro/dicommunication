# macOS DMG

Hospital Macs often cannot run Docker. This folder freezes the FastAPI app with PyInstaller into `Dicommunication.app` and wraps it in a drag-to-Applications DMG.

The DMG **bundles a private Python runtime**. The person who opens the UI only needs a browser. They do not install Python from python.org.

The installer cannot live *inside* this app’s webpage. That page is served by the already-running Python server. Ship the DMG through IT (or a USB stick), not through a button on localhost.

## What the user does

1. Open `dicommunication-<version>-macos-arm64.dmg`.
2. Drag **Dicommunication** onto **Applications**.
3. The first time, right-click the app and choose **Open** (unsigned builds trip Gatekeeper). The Dock icon is the aurora **A** from the browser tab, not PyInstaller’s floppy disk.
4. The default browser opens [http://127.0.0.1:8080](http://127.0.0.1:8080).
5. Quit **Dicommunication** from the Dock to stop the server.

Config stays in `~/.dicommunication` across upgrades.

`ping` is already on macOS. ICMP uses `-c` / `-W`; TCP to the DICOM port is still the useful check on clinical networks.

If a modality must C-FIND this workstation (local MWL SCP on port 11112), allow incoming TCP for **Dicommunication** when macOS asks.

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

Unsigned apps are blocked by Gatekeeper until a Developer ID certificate is used. Keep the certificate out of git.

Linux remains on Docker Compose. Windows uses the MSI in `packaging/windows`.
