# Changelog

## Unreleased

- Version tags also publish `dicommunication.msi` to GitHub Packages (NuGet). The already-cut `v0.2.0` MSI can be wrapped without rebuilding: **Actions → Windows MSI → Run workflow** with `nuget_from_release=v0.2.0`.
- GitHub Actions use Node 24 runtimes (`actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-dotnet@v6`, `actions/upload-artifact@v7`) so the Node 20 deprecation warning on the MSI job goes away.

## 0.2.0

Application logging and an in-app log viewer.

- Rotating `dicommunication.log` next to config (level, max file size, kept rotations)
- **Logs** page: settings, live tail, download, clear
- `INFO` for startup, configuration, tool runs, and MWL SCP; `DEBUG` for HTTP
- Windows MSI version follows `app.__version__`; a `v*` tag publishes the MSI on the GitHub Release

## 0.1.0

First packaged workstation: FastAPI + HTMX UI, DICOM tools, Docker image, and Windows MSI.
