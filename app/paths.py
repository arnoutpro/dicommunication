"""Resolve app resource directories for source installs and frozen Windows builds."""

from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """Directory that contains `templates/` and `static/`.

    PyInstaller onedir unpacks datas into `sys._MEIPASS`. Source installs use
    this package directory next to `main.py`.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        return Path(meipass) / "app"
    return Path(__file__).resolve().parent
