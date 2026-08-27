"""Desktop entry: start the local UI server and show the workstation.

The Windows MSI, macOS DMG, and `python -m app` use this module. Frozen
builds open a native app window. `python -m app` still opens the system
browser unless you pass ``--window``.
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
from datetime import datetime, timezone
from multiprocessing import freeze_support
from pathlib import Path

from app.desktop import (
    UI_BROWSER,
    UI_NONE,
    UI_WINDOW,
    is_frozen,
    resolve_ui_mode,
    run_native_window,
)
from app.paths import runtime_os_name
from app.shell import (
    PROFILE_DICOMM,
    PROFILES,
    profile_start_path,
    profile_window_title,
)

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


def _launch_log_path() -> Path:
    if os.environ.get("DICOMM_DATA_DIR"):
        base = Path(os.environ["DICOMM_DATA_DIR"])
    elif runtime_os_name() == "nt":
        base = windows_data_dir()
    else:
        base = Path.home() / ".dicommunication"
    base.mkdir(parents=True, exist_ok=True)
    return base / "launch.log"


def redirect_frozen_stdio() -> None:
    """Windowed .app / .exe builds have no console; keep a launch log instead."""
    if not is_frozen():
        return
    handle = open(_launch_log_path(), "a", encoding="utf-8", buffering=1)
    sys.stdout = handle
    sys.stderr = handle
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"--- dicommunication launch {stamp} ---", flush=True)


def keep_alive_hint(mode: str | None = None, *, title: str = "Dicommunication") -> str:
    """How to stop the desktop app once the UI is open."""
    if mode == UI_WINDOW:
        return f"Close the {title} window to stop the server."
    if sys.platform == "darwin":
        return f"Quit {title} from the Dock to stop the server."
    if runtime_os_name() == "nt":
        return "Leave this window open while you use the tool. Close it to stop the server."
    return "Leave this process running while you use the tool. Stop it to shut down the server."


def server_is_up(url: str, timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except (OSError, urllib.error.URLError, ValueError):
        return False


def wait_until_up(url: str, attempts: int = 75) -> bool:
    for _ in range(attempts):
        if server_is_up(url):
            return True
        time.sleep(0.2)
    return False


def open_browser_when_ready(url: str, attempts: int = 50) -> None:
    if wait_until_up(url, attempts=attempts):
        webbrowser.open(url)


def _serve(app, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


def _hold_server() -> int:
    """Keep a background uvicorn thread alive after a window fallback."""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dicommunication",
        description="Arnout.pro Dicommunication Tool — local DICOM workstation UI.",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default=os.environ.get("DICOMM_PROFILE", PROFILE_DICOMM),
        help=(
            "dicommunication is the workstation. dicomtag-analytics opens "
            "Dicomtag Analytics (Study Root C-FIND). vue-analytics is the "
            "previous name for that profile."
        ),
    )
    parser.add_argument("--host", default=os.environ.get("DICOMM_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DICOMM_PORT", str(DEFAULT_PORT))),
    )
    ui = parser.add_mutually_exclusive_group()
    ui.add_argument(
        "--window",
        action="store_true",
        help="Open the UI in a native app window (default for the Mac/Windows desktop builds).",
    )
    ui.add_argument(
        "--browser",
        action="store_true",
        help="Open the UI in the default web browser.",
    )
    ui.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server without opening a window or browser.",
    )
    args = parser.parse_args(argv)

    apply_runtime_env()
    redirect_frozen_stdio()
    mode = resolve_ui_mode(
        no_browser=args.no_browser,
        browser=args.browser,
        window=args.window,
    )

    profile = args.profile
    title = profile_window_title(profile)
    ui_url = f"http://{args.host}:{args.port}{profile_start_path(profile)}"
    health = f"http://{args.host}:{args.port}/health"
    if server_is_up(health):
        print(f"Already running at {ui_url}", flush=True)
        if mode == UI_NONE:
            return 0
        if mode == UI_WINDOW and run_native_window(ui_url, title=title):
            return 0
        if mode != UI_NONE:
            webbrowser.open(ui_url)
        return 0

    import uvicorn

    from app.main import app

    if mode == UI_WINDOW:
        threading.Thread(
            target=_serve,
            args=(app, args.host, args.port),
            daemon=True,
            name="dicommunication-uvicorn",
        ).start()
        if not wait_until_up(health):
            print(f"Server did not start at {ui_url}", flush=True)
            return 1
        print(f"{title} UI: {ui_url}", flush=True)
        print(keep_alive_hint(UI_WINDOW, title=title), flush=True)
        if run_native_window(ui_url, title=title):
            return 0
        webbrowser.open(ui_url)
        print(keep_alive_hint(UI_BROWSER, title=title), flush=True)
        return _hold_server()

    if mode == UI_BROWSER:
        threading.Thread(target=open_browser_when_ready, args=(ui_url,), daemon=True).start()

    print(f"{title} UI: {ui_url}", flush=True)
    print(keep_alive_hint(mode, title=title), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
