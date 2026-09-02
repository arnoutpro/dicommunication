# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the macOS .app inside the DMG."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPECDIR = Path(SPECPATH).resolve()
ROOT = SPECDIR.parents[1]
sys.path.insert(0, str(ROOT))

from app import __version__ as APP_VERSION  # noqa: E402

VERSION = os.environ.get("DICOMM_DMG_VERSION") or APP_VERSION
ICON = str(ROOT / "packaging" / "icons" / "app.icns")


def _not_test_module(name: str) -> bool:
    skipped = (".tests", ".benchmarks", ".apps.tests")
    return not any(part in name for part in skipped)


hiddenimports = (
    collect_submodules("app")
    + collect_submodules("pynetdicom", filter=_not_test_module)
    + collect_submodules("pydicom", filter=_not_test_module)
    + collect_submodules("uvicorn")
    + [
        "anyio",
        "h11",
        "httptools",
        "jinja2",
        "multipart",
        "pydantic",
        "starlette",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
    ]
)

datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
]
datas += collect_data_files("pydicom")
datas += collect_data_files("pynetdicom")
datas += collect_data_files("webview")

hiddenimports = (
    list(hiddenimports)
    + collect_submodules("webview")
    + [
        "bottle",
        "proxy_tools",
        "webview.platforms.cocoa",
        "objc",
        "AppKit",
        "Foundation",
        "WebKit",
        "CoreFoundation",
        "PyObjCTools",
        "PyObjCTools.AppHelper",
    ]
)

a = Analysis(
    [str(ROOT / "app" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPECDIR / "rthook_cocoa.py")],
    excludes=["tkinter", "matplotlib", "numpy.tests", "pytest", "pygments"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dicommunication",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    # Must stay False. Argv emulation starts NSApplication in the bootloader and
    # fights pywebview: Dock icon + live process, no visible window.
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dicommunication",
)

app = BUNDLE(
    coll,
    name="Dicommunication.app",
    icon=ICON,
    bundle_identifier="pro.arnout.dicommunication",
    info_plist={
        "CFBundleName": "Dicommunication",
        "CFBundleDisplayName": "Dicommunication",
        "CFBundleGetInfoString": "Arnout.pro Dicommunication Tool",
        "CFBundleIdentifier": "pro.arnout.dicommunication",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSSupportsAutomaticGraphicsSwitching": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)

# A second, fully self-contained bundle sharing the same frozen `coll` — not
# a thin wrapper around Dicommunication.app, so either one can be dragged to
# Applications (or the Desktop) independently and moved or deleted without
# breaking the other. Same launcher.py entry point; LSEnvironment sets the
# profile it reads on a plain Finder double-click (`open -n ... --args
# --profile ...` still works too, and overrides this if given). Both share
# one backend: whichever launches first starts the server, the other just
# opens a second window against it (see main()'s "Already running" path).
app_analytics = BUNDLE(
    coll,
    name="Dicomtag Analytics.app",
    icon=ICON,
    bundle_identifier="pro.arnout.dicommunication.dicomtag-analytics",
    info_plist={
        "CFBundleName": "Dicomtag Analytics",
        "CFBundleDisplayName": "Dicomtag Analytics",
        "CFBundleGetInfoString": "Arnout.pro Dicomtag Analytics (Study Root C-FIND)",
        "CFBundleIdentifier": "pro.arnout.dicommunication.dicomtag-analytics",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSSupportsAutomaticGraphicsSwitching": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSEnvironment": {"DICOMM_PROFILE": "dicomtag-analytics"},
    },
)
