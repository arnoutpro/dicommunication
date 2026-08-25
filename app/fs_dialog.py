"""Native folder picker for the workstation that is running this process."""

from __future__ import annotations

import os
import subprocess
import sys

from app.paths import runtime_os_name


def dialogs_available() -> bool:
    if os.environ.get("DICOMM_NO_DIALOGS", "").strip() in {"1", "true", "yes"}:
        return False
    if runtime_os_name() == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run(argv: list[str], timeout: float = 180) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    path = (completed.stdout or "").strip().splitlines()
    return path[-1].strip() if path else None


def _windows_folder() -> str | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description = 'Select a folder of PDFs'; "
        "$d.ShowNewFolderButton = $false; "
        "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
    )
    return _run(["powershell", "-NoProfile", "-STA", "-Command", script])


def _macos_folder() -> str | None:
    script = (
        'try\n'
        '  set theFolder to choose folder with prompt "Select a folder of PDFs"\n'
        '  POSIX path of theFolder\n'
        'on error\n'
        '  return ""\n'
        'end try'
    )
    return _run(["osascript", "-e", script])


def _linux_folder() -> str | None:
    for argv in (
        ["zenity", "--file-selection", "--directory", "--title=Select a folder of PDFs"],
        ["kdialog", "--getexistingdirectory", os.path.expanduser("~")],
    ):
        path = _run(argv)
        if path:
            return path
    return None


def _tk_folder() -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:  # noqa: BLE001
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass
    try:
        path = filedialog.askdirectory(title="Select a folder of PDFs")
    except Exception:  # noqa: BLE001
        path = ""
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    return str(path).strip() or None


def pick_directory() -> str | None:
    """Open a native folder dialog on this workstation. None if cancelled or unavailable."""
    if not dialogs_available():
        return None
    if runtime_os_name() == "nt":
        return _windows_folder() or _tk_folder()
    if sys.platform == "darwin":
        return _macos_folder() or _tk_folder()
    return _linux_folder() or _tk_folder()
