# Changelog

## Unreleased

- HL7 send: the UI can set ORC-1 to XO (change order) and put OBR-31 into the CE text component (`^text`). A new MSH-10 alone is not an order update. The Send result also repeats OBR-25 and adds a Hint when an ACK is likely not a PACS update.

## 0.3.0

HL7 send, native desktop window, Mac DMG, app icon, denser chrome, and MSI GitHub Packages.

- HL7 send: paste or save an HL7 v2 message and send it over TCP (MLLP by default) to a host:port. This is a sender, not a message analyzer; the result shows the raw ACK / MSA-1.
- HL7 send: the message box wraps long pipe-delimited lines. A Segments view shows one row per segment (MSH, PID, OBR, …); Raw is the full paste.
- HL7 send: the Send result shows MSH-10, ORC-1, and OBR-31 as they went on the wire. The UI can stamp a new Message Control ID so a resend is not treated as a duplicate. An ACK still only means the engine accepted the bytes.
- Frozen Mac and Windows builds open the UI in a native app window (WKWebView / Edge WebView2), not a Safari or Chrome tab. `--browser` still uses the system browser; `--no-browser` is server-only.
- Mac DMG: Finder launches show the native window. PyInstaller argv emulation was fighting Cocoa/pywebview (Dock icon and a live process, no window). Frozen launches also append `~/.dicommunication/launch.log`.
- Dock, Finder, the Windows Start menu, and the native window use the **Sansation Bold** capital A (same letter as the watermark), not a geometric stand-in and not PyInstaller’s floppy-disk icon.
- macOS Apple Silicon DMG: CI freezes `Dicommunication.app` and attaches `dicommunication-<version>-macos-arm64.dmg` to `v*` GitHub Releases. The already-cut `v0.2.0` tag can get a DMG without a new version: **Actions → macOS DMG → Run workflow** with `release_tag=v0.2.0`.
- Version tags also publish `dicommunication.msi` to GitHub Packages (NuGet). The already-cut `v0.2.0` MSI can be wrapped without rebuilding: **Actions → Windows MSI → Run workflow** with `nuget_from_release=v0.2.0`.
- Page chrome is denser across Dashboard, Configured nodes, Testbench, Worklist, and tool pages: tighter headers, panels, forms, tables, and buttons. Logs still uses leftover height for the live viewer.
- Log level and other `<select>` menus use the themed glass panel in Dark mode, so the OS does not paint a white popup.

## 0.2.0

Application logging and an in-app log viewer.

- Rotating `dicommunication.log` next to config (level, max file size, kept rotations)
- **Logs** page: settings, live tail, download, clear
- `INFO` for startup, configuration, tool runs, and MWL SCP; `DEBUG` for HTTP
- Windows MSI version follows `app.__version__`; a `v*` tag publishes the MSI on the GitHub Release

## 0.1.0

First packaged workstation: FastAPI + HTMX UI, DICOM tools, Docker image, and Windows MSI.
