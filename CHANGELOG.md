# Changelog

## Unreleased

- Sidebar is a two-level tree (Test tools → Connectivity / DIMSE / HL7). Click **Test tools** (or a category) to fold it out; groups start folded. **About** and **Help** sit on one compact row.
- PDF to DICOM: **…** browses a folder on this workstation, **Scan** lists how many PDFs are in it, required Patient Name / ID are marked, and Generate / unique-per-file identities cover bulk directory imports.
- Sidebar **About** and **Help** buttons: About shows the running version and this installation; Help is the in-app administrator guide (each screen and tool, including HL7 Advanced troubleshooting).
- **PDF to DICOM** (`pdf-store`): import PDF files, a ZIP of PDFs, or a local directory; wrap each as Encapsulated PDF Storage; optionally C-STORE to a PACS.
- App chrome matches the quiet marketing site: squared corners (no pills), opaque panels instead of glass, slate-grey light mode, near-black dark mode, and bright cyan / amber / teal / rose accents on metrics, nav, and primary actions.
- HL7 send: MSH-10, ORC-1, OBR-31, and OBR-25 stamps sit behind **Advanced troubleshooting**, with a short note on what each field actually changes (and that an ACK is still not a PACS update).
- HL7 send: the UI can set ORC-1 to XO (change order) and put OBR-31 into a CE `id^text`. A new MSH-10 alone is not an order update. The Send result also repeats OBR-25 and adds a Hint when an ACK is likely not a PACS update.
- HL7 send: XO also stamps ORC-9. OBR-31 CE now fills an empty identifier (`arnout.pro^arnout.pro SEH` instead of `^arnout.pro SEH`). A **Set OBR-25 to SC** checkbox tests the usual completed-exam skip; an ACK still does not rewrite images already in PACS.
- HL7 send: **ORC-1 on change** can stamp Vue / IS Link **SC** (update order). XO is not in the Vue order-control table; a Mirth ACK is not Vue applying the order.
- HL7 send: if Vue **IS Link is empty** after an ACK, Host/Port is not the IS Link listener. The ACK step shows **MSH-3** (who answered). Hints say to send to IS Link’s listen host:port from IS Link Configuration — no Mirth access required.
- HL7 send: Philips Vue **10010** can be the HL7 VIP **and** IS Link’s Listener Port Number. The Listeners page is settings, not the queue — look at **Queues & Notifications**. Control Port (often 2112) is not MLLP. The transcript also repeats **MSH-5 / MSH-6**.

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
