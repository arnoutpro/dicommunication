"""Desktop entry: start the local UI server and open a browser.

The Windows MSI, macOS DMG, and `python -m app` use this module. Python is
bundled inside those frozen builds; the browser does not need a system Python
install.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from multiprocessing import freeze_support
from pathlib import Path

from app.paths import runtime_os_name

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def windows_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "dicommunication"


def apply_runtime_env() -> None:
    """Set a Windows data dir before ConfigStore is imported.

    macOS and Linux keep the default `~/.dicommunication` from ConfigStore.
    """
    if "DICOMM_DATA_DIR" in os.environ:
        return
    if runtime_os_name() == "nt":
        os.environ["DICOMM_DATA_DIR"] = str(windows_data_dir())


def keep_alive_hint() -> str:
    """How to stop the frozen desktop app once the browser is open."""
    if sys.platform == "darwin":
        return "Quit Dicommunication from the Dock to stop the server."
    if runtime_os_name() == "nt":
        return "Leave this window open while you use the tool. Close it to stop the server."
    return "Leave this process running while you use the tool. Stop it to shut down the server."


def server_is_up(url: str, timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except (OSError, urllib.error.URLError, ValueError):
        return False


def open_browser_when_ready(url: str, attempts: int = 50) -> None:
    for _ in range(attempts):
        if server_is_up(url):
            webbrowser.open(url)
            return
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dicommunication",
        description="Arnout.pro Dicommunication Tool — local DICOM workstation UI.",
    )
    parser.add_argument("--host", default=os.environ.get("DICOMM_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DICOMM_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server without opening a browser.",
    )
    args = parser.parse_args(argv)

    apply_runtime_env()

    ui = f"http://{args.host}:{args.port}/"
    health = f"http://{args.host}:{args.port}/health"
    if server_is_up(health):
        if not args.no_browser:
            webbrowser.open(ui)
        print(f"Already running at {ui}", flush=True)
        return 0

    import uvicorn

    from app.main import app

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(ui,), daemon=True).start()

    print(f"Dicommunication UI: {ui}", flush=True)
    print(keep_alive_hint(), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
