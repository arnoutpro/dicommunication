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


def _bring_app_to_front() -> None:
    """Make the native window key after Cocoa/PyInstaller already started NSApp."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyRegular

        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        for window in NSApp.windows():
            window.makeKeyAndOrderFront_(None)
    except Exception as exc:  # noqa: BLE001 — activation is best-effort
        print(f"Could not bring the Mac window forward ({exc})", flush=True)


def _macos_run_until_windows_close(webview) -> None:
    """Keep Cocoa alive if pywebview's start() returned while windows still exist.

    PyInstaller argv emulation can leave NSApplication already ``isRunning``, so
    pywebview skips ``NSApp.run()``. The process then either exits or sits in the
    Dock with no visible window. If windows are still open, show them and pump
    events ourselves.
    """
    if sys.platform != "darwin":
        return
    windows = list(getattr(webview, "windows", []) or [])
    if not windows:
        return
    _bring_app_to_front()
    for win in windows:
        show = getattr(win, "show", None)
        if callable(show):
            try:
                show()
            except Exception:
                continue
    try:
        from AppKit import NSApp
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop
        from PyObjCTools import AppHelper
    except Exception as exc:  # noqa: BLE001
        print(f"Cocoa event loop unavailable ({exc})", flush=True)
        return

    if not NSApp.isRunning():
        print("Cocoa was not in a run loop; starting one for the window.", flush=True)
        try:
            AppHelper.runEventLoop()
        except Exception as exc:  # noqa: BLE001
            print(f"Cocoa event loop unavailable ({exc})", flush=True)
        return

    print("Cocoa was already running; pumping events until the window closes.", flush=True)
    runloop = NSRunLoop.currentRunLoop()
    while list(getattr(webview, "windows", []) or []):
        _bring_app_to_front()
        runloop.runMode_beforeDate_(
            NSDefaultRunLoopMode,
            NSDate.dateWithTimeIntervalSinceNow_(0.25),
        )


def run_native_window(ui: str) -> bool:
    """Open ``ui`` in a native window. Return False if the WebView cannot start."""
    try:
        import webview
    except ImportError:
        print("pywebview is not installed; opening the system browser.", flush=True)
        return False

    gui = native_gui()
    window_kwargs = {
        "width": 1280,
        "height": 860,
        "min_size": (880, 600),
        "text_select": True,
        "background_color": "#020617",
        "hidden": False,
    }
    try:
        try:
            window = webview.create_window(
                WINDOW_TITLE, ui, zoomable=True, **window_kwargs
            )
        except TypeError:
            window = webview.create_window(WINDOW_TITLE, ui, **window_kwargs)
        shown = getattr(getattr(window, "events", None), "shown", None)
        if shown is not None:
            try:
                shown += _bring_app_to_front
            except TypeError:
                pass
        start_kwargs: dict = {
            "private_mode": False,
            "storage_path": str(webview_storage_dir()),
        }
        if gui:
            start_kwargs["gui"] = gui
        print(f"Opening native window ({gui or 'default'}) for {ui}", flush=True)
        webview.start(**start_kwargs)
        _macos_run_until_windows_close(webview)
        return True
    except Exception as exc:  # noqa: BLE001 — any GUI/runtime miss falls back
        print(f"Native window unavailable ({exc}). Opening the system browser.", flush=True)
        return False
