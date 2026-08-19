"""Native desktop window for frozen Mac/Windows builds.

The MSI and DMG used to call ``webbrowser.open``, so the OS opened a Safari
or Chrome *tab*. That is default-browser behaviour, not something macOS
requires. Frozen builds instead host the local UI in a dedicated WebView
window (WKWebView on macOS, Edge WebView2 on Windows): no tabs, no URL bar.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.paths import runtime_os_name

UI_WINDOW = "window"
UI_BROWSER = "browser"
UI_NONE = "none"

WINDOW_TITLE = "Dicommunication"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_ui_mode(*, no_browser: bool, browser: bool, window: bool) -> str:
    """Pick window, browser, or none.

    Frozen desktop builds default to a native window. ``python -m app`` and
    Docker keep the browser (or no UI) unless ``--window`` / ``DICOMM_UI`` say
    otherwise.
    """
    if no_browser:
        return UI_NONE
    if browser:
        return UI_BROWSER
    if window:
        return UI_WINDOW
    env = os.environ.get("DICOMM_UI", "").strip().lower()
    if env in {UI_WINDOW, UI_BROWSER, UI_NONE}:
        return env
    return UI_WINDOW if is_frozen() else UI_BROWSER


def webview_storage_dir() -> Path:
    """Persist WebView localStorage (theme) next to config."""
    if os.environ.get("DICOMM_DATA_DIR"):
        base = Path(os.environ["DICOMM_DATA_DIR"])
    elif runtime_os_name() == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "dicommunication"
    else:
        base = Path.home() / ".dicommunication"
    path = base / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def native_gui() -> str | None:
    if sys.platform == "darwin":
        return "cocoa"
    if runtime_os_name() == "nt":
        return "edgechromium"
    return None


def run_native_window(ui: str) -> bool:
    """Open ``ui`` in a native window. Return False if the WebView cannot start."""
    try:
        import webview
    except ImportError:
        print("pywebview is not installed; opening the system browser.", flush=True)
        return False

    gui = native_gui()
    try:
        webview.create_window(
            WINDOW_TITLE,
            ui,
            width=1280,
            height=860,
            min_size=(880, 600),
            text_select=True,
            background_color="#020617",
            zoomable=True,
        )
        start_kwargs: dict = {
            "private_mode": False,
            "storage_path": str(webview_storage_dir()),
        }
        if gui:
            start_kwargs["gui"] = gui
        webview.start(**start_kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 — any GUI/runtime miss falls back
        print(f"Native window unavailable ({exc}). Opening the system browser.", flush=True)
        return False
