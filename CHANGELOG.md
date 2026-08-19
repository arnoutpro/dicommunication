# Changelog

## Unreleased

- Dock, Finder, and the Windows Start menu use the arnout.pro aurora “A” (same art as the browser tab), not PyInstaller’s default floppy-disk bootloader icon.
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
