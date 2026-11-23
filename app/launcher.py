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
from app.paths import is_macos, runtime_os_name
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
    context = None
    if url.startswith("https://"):
        import ssl

        # Only ever used to ask this computer's own server whether it is up; its certificate is self-signed.
        context = ssl._create_unverified_context()  # noqa: S323
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
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


def _serve(app, host: str, port: int, **ssl_kwargs) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


def _hold_server() -> int:
    """Keep a background uvicorn thread alive after a window fallback."""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def _prepare_tls(app, use_https: bool) -> dict:
    """The certificate to serve with (made if needed), or nothing for plain HTTP."""
    if not use_https:
        app.state.tls_info = {"enabled": False}
        return {}
    from app.security import configured_hosts
    from app.tls import describe, ensure_certificate

    files = ensure_certificate(app.state.store.data_dir, sorted(configured_hosts()))
    app.state.tls_info = describe(files.cert)
    return {"ssl_certfile": str(files.cert), "ssl_keyfile": str(files.key)}


def create_admin_from_file(name: str, password_file: str | None) -> int:
    """``--create-admin NAME --password-file FILE``: for a server nobody can browse from, and for a lost password."""
    try:
        password = Path(password_file or "").read_text(encoding="utf-8").splitlines()[0] if password_file else ""
    except (OSError, IndexError):
        print("Could not read the first line of the password file.", flush=True)
        return 2
    return create_admin(name, password)


def create_admin(name: str, password: str, data_dir: Path | str | None = None) -> int:
    """Create an administrator, or reset that account to an enabled administrator with this password."""
    from app.auth import ADMIN, AuthError, UserStore

    if data_dir is None:
        from app.store import DEFAULT_DATA_DIR as data_dir  # noqa: N811
    users = UserStore(data_dir)
    try:
        existing = users.by_username(name)
        if existing is None:
            users.add(name, password, ADMIN)
            print(f"Created the administrator {name}.", flush=True)
        else:
            users.set_password(existing.id, password)
            users.set_role(existing.id, ADMIN)
            users.set_disabled(existing.id, False)
            print(f"Reset {existing.username}: new password, administrator, enabled.", flush=True)
    except AuthError as exc:
        print(f"Not done: {exc}", flush=True)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    if given[:1] == ["service"]:
        # `dicommunication service status|network|port|create-admin|uninstall`: managing the macOS background service.
        from app.macos_service import service_main

        return service_main(given[1:])
    parser = argparse.ArgumentParser(
        prog="dicommunication",
        description="Arnout.pro Dicommunication Tool — local DICOM workstation UI.",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default=os.environ.get("DICOMM_PROFILE", PROFILE_DICOMM),
        help=argparse.SUPPRESS,  # Accepted for old shortcuts/scripts; every profile opens the same window now.
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
    parser.add_argument(
        "--uninstall-cleanup",
        action="store_true",
        help=argparse.SUPPRESS,  # Windows MSI uninstall hook, not a user-facing flag.
    )
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--https", action="store_true", help="Serve over HTTPS with a self-signed certificate (automatic when the app listens beyond this computer).")
    tls.add_argument("--no-https", action="store_true", help="Serve plain HTTP even when listening beyond this computer, for a reverse proxy that handles HTTPS.")
    parser.add_argument("--create-admin", metavar="NAME", help="Create an administrator, or reset the password of that account, from --password-file.")
    parser.add_argument("--password-file", metavar="FILE", help="With --create-admin: a text file whose first line is the password. Delete it afterwards.")
    # Windows headless edition (packaging/windows/Headless.wxs): not user-facing flags.
    parser.add_argument("--configure-service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--service-data", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--listen", choices=("local", "network"), default="local", help=argparse.SUPPRESS)
    parser.add_argument("--from-saved", action="store_true", help=argparse.SUPPRESS)  # macOS installer: keep the saved port and network option
    parser.add_argument("--reload", action="store_true", help=argparse.SUPPRESS)  # macOS installer: (re)start the service afterwards
    args = parser.parse_args(argv)

    if args.configure_service:
        if is_macos():
            from app.macos_service import run_configure as run_configure_macos

            return run_configure_macos(port=args.port, listen=args.listen, from_saved=args.from_saved, reload=args.reload)
        from app.service_setup import run_configure

        return run_configure(args.port, args.listen)

    if args.create_admin:
        if args.service_data:
            from app.service_setup import service_data_dir

            os.environ["DICOMM_DATA_DIR"] = str(service_data_dir())
        apply_runtime_env()
        redirect_frozen_stdio()
        code = create_admin_from_file(args.create_admin, args.password_file)
        if args.service_data and is_macos():
            from app.macos_service import fix_ownership
            from app.service_setup import service_data_dir

            fix_ownership(service_data_dir())  # made by root; the service account must be able to use it
        return code

    if args.uninstall_cleanup:
        from app.uninstall import run_uninstall_cleanup

        return run_uninstall_cleanup(service=True) if args.service_data else run_uninstall_cleanup()

    apply_runtime_env()
    redirect_frozen_stdio()
    mode = resolve_ui_mode(
        no_browser=args.no_browser,
        browser=args.browser,
        window=args.window,
    )

    profile = args.profile
    title = profile_window_title(profile)
    from app.auth import LOOPBACK

    on_network = args.host not in LOOPBACK and args.host != "[::1]"
    use_https = (args.https or on_network) and not args.no_https and mode != UI_WINDOW
    scheme = "https" if use_https else "http"
    # The app reads this to decide that sign-in is required when other computers can reach it.
    os.environ["DICOMM_LISTEN_HOST"] = args.host
    ui_url = f"{scheme}://{args.host}:{args.port}{profile_start_path(profile)}"
    health = f"{scheme}://{args.host}:{args.port}/health"
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

    ssl_kwargs = _prepare_tls(app, use_https)
    print(f"{title} UI: {ui_url}", flush=True)
    print(keep_alive_hint(mode, title=title), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **ssl_kwargs)
    return 0


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
