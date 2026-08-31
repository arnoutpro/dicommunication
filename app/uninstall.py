"""Windows MSI uninstall hook: ask whether to delete the local data directory.

Wired into packaging/windows/Product.wxs as a deferred custom action that
runs `dicommunication.exe --uninstall-cleanup` before files are removed, on a
genuine uninstall only (the WiX condition excludes the RemoveExistingProducts
step of an upgrade, which also sets REMOVE=ALL for the old version).

The data directory holds config, recent tool results, and worklist entries —
SECURITY.md flags it as containing PHI in cleartext — so the default on any
failure to ask (PowerShell unavailable, dialog dismissed, timeout) is to keep
it, never to delete it.
"""

from __future__ import annotations

import shutil
import subprocess

from app.launcher import windows_data_dir
from app.paths import runtime_os_name

CONFIRM_TITLE = "Dicommunication"
CONFIRM_MESSAGE = (
    "Also delete saved configuration and history?\n\n"
    "This includes remote/identity settings, recent tool results, and the "
    "worklist, which can contain patient data from past runs. This cannot "
    "be undone."
)


def _confirm_via_powershell() -> bool | None:
    """Ask Yes/No with a native message box. None if it could not be shown."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$r = [System.Windows.Forms.MessageBox]::Show("
        f"'{CONFIRM_MESSAGE}', '{CONFIRM_TITLE}', "
        "[System.Windows.Forms.MessageBoxButtons]::YesNo, "
        "[System.Windows.Forms.MessageBoxIcon]::Warning, "
        "[System.Windows.Forms.MessageBoxDefaultButton]::Button2); "
        "if ($r -eq 'Yes') { exit 0 } else { exit 1 }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            check=False,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return completed.returncode == 0


def confirm_purge_data() -> bool:
    """Ask whether to delete the data directory. Defaults to keeping it."""
    if runtime_os_name() != "nt":
        return False
    return bool(_confirm_via_powershell())


def purge_data_dir() -> None:
    shutil.rmtree(windows_data_dir(), ignore_errors=True)


def run_uninstall_cleanup() -> int:
    if confirm_purge_data():
        purge_data_dir()
    return 0
