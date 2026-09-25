"""Resolve app resource directories for source installs and frozen PyInstaller builds."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def runtime_os_name() -> str:
    """`os.name` wrapper so tests can fake Windows without breaking pathlib."""
    return os.name


def no_console_kwargs() -> dict:
    """`subprocess.run`/`Popen` kwargs that stop a console app from flashing its
    own window when this process itself has no console (the frozen desktop
    build). A no-op everywhere but Windows.
    """
    if runtime_os_name() != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def package_dir() -> Path:
    """Directory that contains `templates/` and `static/`.

    PyInstaller onedir unpacks datas into `sys._MEIPASS`. Source installs use
    this package directory next to `main.py`.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        return Path(meipass) / "app"
    return Path(__file__).resolve().parent
