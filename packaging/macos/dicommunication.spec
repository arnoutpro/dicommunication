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

a = Analysis(
    [str(ROOT / "app" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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
    icon=None,
    bundle_identifier="pro.arnout.dicommunication",
    info_plist={
        "CFBundleName": "Dicommunication",
        "CFBundleDisplayName": "Dicommunication",
        "CFBundleGetInfoString": "Arnout.pro Dicommunication Tool",
        "CFBundleIdentifier": "pro.arnout.dicommunication",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
    },
)
