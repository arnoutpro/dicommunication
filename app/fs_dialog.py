"""Native folder picker for the workstation that is running this process."""

from __future__ import annotations

import os
import subprocess
import sys

from app.paths import no_console_kwargs, runtime_os_name


# The dialog's title comes from this fixed list only: it is placed inside a
# script for PowerShell / AppleScript, so it must never be text from a request.
TITLES = {
    "pdf": "Select a folder of PDFs",
    "dicom": "Select a folder with DICOM files",
}
DEFAULT_PURPOSE = "pdf"


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
            **no_console_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    path = (completed.stdout or "").strip().splitlines()
    return path[-1].strip() if path else None


def _windows_folder(title: str = TITLES[DEFAULT_PURPOSE]) -> str | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        f"$d.Description = '{title}'; "
        "$d.ShowNewFolderButton = $false; "
        "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
    )
    return _run(["powershell", "-NoProfile", "-STA", "-Command", script])


def _macos_folder(title: str = TITLES[DEFAULT_PURPOSE]) -> str | None:
    script = (
        'try\n'
        f'  set theFolder to choose folder with prompt "{title}"\n'
        '  POSIX path of theFolder\n'
        'on error\n'
        '  return ""\n'
        'end try'
    )
    return _run(["osascript", "-e", script])


def _linux_folder(title: str = TITLES[DEFAULT_PURPOSE]) -> str | None:
    for argv in (
        ["zenity", "--file-selection", "--directory", f"--title={title}"],
        ["kdialog", "--getexistingdirectory", os.path.expanduser("~")],
    ):
        path = _run(argv)
        if path:
            return path
    return None


def _tk_folder(title: str = TITLES[DEFAULT_PURPOSE]) -> str | None:
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
        path = filedialog.askdirectory(title=title)
    except Exception:  # noqa: BLE001
        path = ""
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    return str(path).strip() or None


def pick_directory(purpose: str = DEFAULT_PURPOSE) -> str | None:
    """Open a native folder dialog on this workstation. None if cancelled or unavailable.

    ``purpose`` picks the dialog's title from TITLES; anything else gets the default.
    """
    if not dialogs_available():
        return None
    title = TITLES.get(purpose, TITLES[DEFAULT_PURPOSE])
    if runtime_os_name() == "nt":
        return _windows_folder(title) or _tk_folder(title)
    if sys.platform == "darwin":
        return _macos_folder(title) or _tk_folder(title)
    return _linux_folder(title) or _tk_folder(title)
