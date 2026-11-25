"""macOS: the background service (LaunchDaemon), like the Windows service.

The installer package (packaging/macos) puts the program in
``/usr/local/lib/dicommunication`` and its post-install script creates the
``_dicommunication`` account and runs ``dicommunication --configure-service
--from-saved --reload``, which is ``configure()`` below. That writes the launchd
job, prepares the data folder and (re)starts the service. Afterwards an
administrator manages it with ``sudo dicommunication service ...``
(``service_main()``): status, network on/off, port, create-admin, uninstall.

Like the Windows service it runs without a window, starts with the machine, runs
as its own low-privilege account, always asks for sign-in, and keeps its data in
one folder that can hold patient data in cleartext and is limited to that account.

Every path hangs off a ``Layout`` so tests can run all of this in a temporary
folder, and nothing here runs ``launchctl`` or ``dscl`` unless it is on a Mac and
told to.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.paths import is_macos
from app.service_setup import (
    DEFAULT_PORT,
    LISTEN_LOCAL,
    LISTEN_NETWORK,
    computer_names,
    listen_host,
    validate_port,
)

LABEL = "pro.arnout.dicommunication"
SERVICE_USER = "_dicommunication"
SETTINGS_NAME = "service.json"
SETUP_LOG_NAME = "service-setup.log"
FIREWALL_TOOL = "/usr/libexec/ApplicationFirewall/socketfilterfw"

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Layout:
    """Where everything lives. ``root`` is "/" on a real Mac."""

    root: Path = Path("/")

    def _p(self, *parts: str) -> Path:
        return self.root.joinpath(*[part.lstrip("/") for part in parts])

    @property
    def data_dir(self) -> Path:
        return self._p("Library/Application Support/Dicommunication")

    @property
    def log_dir(self) -> Path:
        return self._p("var/log/dicommunication")

    @property
    def service_log(self) -> Path:
        return self.log_dir / "service.log"

    @property
    def plist(self) -> Path:
        return self._p("Library/LaunchDaemons", f"{LABEL}.plist")

    @property
    def newsyslog(self) -> Path:
        return self._p("etc/newsyslog.d", f"{LABEL}.conf")

    @property
    def program_dir(self) -> Path:
        return self._p("usr/local/lib/dicommunication")

    @property
    def executable(self) -> Path:
        return self.program_dir / "dicommunication"

    @property
    def command_link(self) -> Path:
        return self._p("usr/local/bin/dicommunication")

    @property
    def shortcut(self) -> Path:
        return self._p("Applications/Dicommunication.webloc")

    @property
    def settings_file(self) -> Path:
        return self.data_dir / SETTINGS_NAME


@dataclass(frozen=True)
class Settings:
    port: int = DEFAULT_PORT
    listen: str = LISTEN_LOCAL

    @property
    def scheme(self) -> str:
        return "https" if self.listen == LISTEN_NETWORK else "http"

    @property
    def url(self) -> str:
        return f"{self.scheme}://localhost:{self.port}/"


def load_settings(layout: Layout) -> Settings:
    """The saved port and network option; the defaults if there are none or they are unreadable."""
    try:
        raw = json.loads(layout.settings_file.read_text(encoding="utf-8"))
        port = validate_port(raw.get("port", DEFAULT_PORT))
        listen = raw.get("listen", LISTEN_LOCAL)
        listen_host(listen)
        return Settings(port=port, listen=listen)
    except (OSError, ValueError, AttributeError):
        return Settings()


# --- what gets written -----------------------------------------------------------


def render_plist(
    settings: Settings,
    *,
    executable: Path | str,
    data_dir: Path | str,
    log_file: Path | str,
    hostnames: list[str] | None = None,
    user: str = SERVICE_USER,
) -> bytes:
    """The launchd job: runs at boot, restarts after a crash, runs as the service account."""
    environment = {
        "DICOMM_DATA_DIR": str(data_dir),
        # Always sign in, also when only this Mac can reach it: anyone logged in to it could otherwise use the app.
        "DICOMM_REQUIRE_LOGIN": "1",
    }
    if settings.listen == LISTEN_NETWORK and hostnames:
        environment["DICOMM_ALLOWED_HOSTS"] = ",".join(hostnames)  # the app only answers to localhost and IPs unless told a name
    job = {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "--no-browser", "--host", listen_host(settings.listen), "--port", str(validate_port(settings.port))],
        "EnvironmentVariables": environment,
        "UserName": user,
        "GroupName": user,
        "WorkingDirectory": str(data_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }
    return plistlib.dumps(job)


def render_newsyslog(log_file: Path | str, user: str = SERVICE_USER) -> str:
    """Keep five rotated copies of the service's own log, 5 MB each (the app rotates its log itself)."""
    return f"# logfilename owner:group mode count size(KB) when flags\n{log_file} {user}:{user} 640 5 5120 * NJ\n"


def render_shortcut(settings: Settings) -> bytes:
    """A .webloc: double-click it in Applications to open the app in the browser."""
    return plistlib.dumps({"URL": settings.url})


# --- helpers ---------------------------------------------------------------------


def _log(layout: Layout, message: str) -> None:
    """Append to the setup log, the only place a failed install can be diagnosed."""
    from datetime import datetime, timezone

    try:
        layout.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(layout.log_dir / SETUP_LOG_NAME, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _service_ids() -> tuple[int, int] | None:
    """(uid, gid) of the service account, or None if it does not exist (or this is not a Mac)."""
    try:
        import pwd

        entry = pwd.getpwnam(SERVICE_USER)
    except (ImportError, KeyError):
        return None
    return entry.pw_uid, entry.pw_gid


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _chown_mode(path: Path, mode: int, *, own: tuple[int, int] | None) -> None:
    os.chmod(path, mode)
    if own is not None and _is_root():
        os.chown(path, own[0], own[1])


def fix_ownership(path: Path) -> None:
    """Hand a folder (and what a root-run command just put in it) back to the service account."""
    own = _service_ids()
    if own is None or not _is_root():
        return
    for item in [path, *path.rglob("*")]:
        try:
            os.chown(item, own[0], own[1], follow_symlinks=False)
        except OSError:
            pass


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _run(runner: Runner, *command: str) -> subprocess.CompletedProcess:
    try:
        return runner(list(command), check=False, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return subprocess.CompletedProcess(list(command), 127, "", str(exc))


def reload_service(layout: Layout, runner: Runner = subprocess.run) -> bool:
    """Stop the job if it is loaded, then load it from the plist (which also starts it)."""
    _run(runner, "launchctl", "bootout", f"system/{LABEL}")
    loaded = _run(runner, "launchctl", "bootstrap", "system", str(layout.plist))
    if loaded.returncode != 0:
        _log(layout, f"launchctl bootstrap failed ({loaded.returncode}): {(loaded.stderr or '').strip()}")
        return False
    return True


def allow_through_firewall(layout: Layout, runner: Runner = subprocess.run) -> None:
    """Best effort: without an exception the macOS application firewall (if on) silently blocks the service."""
    exe = str(layout.executable)
    _run(runner, FIREWALL_TOOL, "--add", exe)
    _run(runner, FIREWALL_TOOL, "--unblockapp", exe)


# --- configure -------------------------------------------------------------------


def configure(
    layout: Layout = Layout(),
    *,
    port: object = None,
    listen: str | None = None,
    from_saved: bool = False,
    reload: bool = False,
    runner: Runner = subprocess.run,
    hostnames: list[str] | None = None,
) -> int:
    """Write the service settings and launchd job and prepare the folders. 0 on success.

    Without ``port``/``listen`` the saved values are kept (the defaults on a first install),
    so an upgrade does not change what an administrator chose.
    """
    saved = load_settings(layout)
    try:
        settings = saved if from_saved else Settings(
            port=validate_port(port) if port is not None else saved.port,
            listen=listen if listen is not None else saved.listen,
        )
        listen_host(settings.listen)
        validate_port(settings.port)
    except ValueError as exc:
        _log(layout, f"setup failed: {exc}")
        print(f"Not done: {exc}", flush=True)
        return 2
    own = _service_ids()
    try:
        for folder in (layout.data_dir, layout.log_dir):
            folder.mkdir(parents=True, exist_ok=True)
        (layout.data_dir / "logs").mkdir(exist_ok=True)
        _chown_mode(layout.data_dir, 0o700, own=own)  # patient data may end up in here: the service account only
        _chown_mode(layout.data_dir / "logs", 0o700, own=own)
        _chown_mode(layout.log_dir, 0o750, own=own)
    except OSError as exc:
        _log(layout, f"setup failed: cannot prepare {layout.data_dir}: {exc}")
        print(f"Not done: cannot prepare the data folder: {exc}", flush=True)
        return 3
    if own is None:
        _log(layout, f"warning: the account {SERVICE_USER} does not exist; folders were left to their current owner")
    try:
        _write_atomic(layout.settings_file, json.dumps({"port": settings.port, "listen": settings.listen}, indent=2).encode(), 0o600)
        fix_ownership(layout.settings_file)
        _write_atomic(layout.plist, render_plist(settings, executable=layout.executable, data_dir=layout.data_dir, log_file=layout.service_log, hostnames=hostnames if hostnames is not None else computer_names()), 0o644)
        if _is_root():
            os.chown(layout.plist, 0, 0)  # launchd refuses a job that is not owned by root
        _write_atomic(layout.newsyslog, render_newsyslog(layout.service_log).encode(), 0o644)
    except OSError as exc:
        _log(layout, f"setup failed: cannot write the service files: {exc}")
        print(f"Not done: cannot write the service files: {exc}", flush=True)
        return 4
    try:  # a shortcut is a convenience: never fail the install over it
        _write_atomic(layout.shortcut, render_shortcut(settings), 0o644)
    except OSError:
        pass
    if settings.listen == LISTEN_NETWORK:
        allow_through_firewall(layout, runner)
    _log(layout, f"configured: port {settings.port}, listen {settings.listen}")
    if reload and not reload_service(layout, runner):
        print("The service files are in place but launchd did not load the service; see /var/log/dicommunication.", flush=True)
        return 5
    return 0


def run_configure(*, port: object, listen: str, from_saved: bool, reload: bool) -> int:
    """``dicommunication --configure-service``: what the installer's post-install script runs."""
    return configure(port=port, listen=listen, from_saved=from_saved, reload=reload)


# --- status / uninstall ----------------------------------------------------------


def service_state(runner: Runner = subprocess.run) -> str:
    """``running``, ``loaded`` (not running) or ``not loaded``."""
    printed = _run(runner, "launchctl", "print", f"system/{LABEL}")
    if printed.returncode != 0:
        return "not loaded"
    return "running" if "state = running" in (printed.stdout or "") else "loaded"


def status(layout: Layout = Layout(), runner: Runner = subprocess.run) -> str:
    settings = load_settings(layout)
    network = "other computers on the network may connect (HTTPS)" if settings.listen == LISTEN_NETWORK else "this computer only"
    return "\n".join(
        [
            f"Service:  {service_state(runner)}",
            f"Address:  {settings.url}  ({network})",
            f"Data:     {layout.data_dir}",
            f"Logs:     {layout.data_dir / 'logs'}  and  {layout.log_dir}",
        ]
    )


def uninstall(
    layout: Layout = Layout(),
    *,
    delete_data: bool,
    runner: Runner = subprocess.run,
    say: Callable[[str], None] = print,
) -> int:
    """Stop the service and remove what the installer put on the Mac. The data is kept unless ``delete_data``."""
    _run(runner, "launchctl", "bootout", f"system/{LABEL}")
    for path in (layout.plist, layout.newsyslog, layout.shortcut, layout.command_link):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            say(f"Could not remove {path}: {exc}")
    shutil.rmtree(layout.program_dir, ignore_errors=True)
    if delete_data:
        shutil.rmtree(layout.data_dir, ignore_errors=True)
        shutil.rmtree(layout.log_dir, ignore_errors=True)
        for record in (f"/Users/{SERVICE_USER}", f"/Groups/{SERVICE_USER}"):
            _run(runner, "dscl", ".", "-delete", record)
        say("Removed Dicommunication, its data, its logs and its service account.")
    else:
        say(f"Removed Dicommunication. Your data is still in {layout.data_dir} (it can contain patient data); delete it by hand, or run this again with --delete-data.")
    _run(runner, "pkgutil", "--forget", LABEL)
    return 0


# --- the command: dicommunication service ... -------------------------------------


def _need_root(action: str) -> bool:
    if _is_root():
        return True
    print(f"{action} needs administrator rights. Run it again with sudo.", flush=True)
    return False


def service_main(argv: list[str], *, layout: Layout | None = None, runner: Runner = subprocess.run, ask: Callable[[str], str] = input, password: Callable[[str], str] = getpass.getpass) -> int:
    layout = layout or Layout()
    parser = argparse.ArgumentParser(prog="dicommunication service", description="Manage the Dicommunication background service (macOS).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show whether the service runs, and its address")
    net = sub.add_parser("network", help="Let other computers connect (on) or this Mac only (off)")
    net.add_argument("state", choices=("on", "off"))
    prt = sub.add_parser("port", help="Change the web port")
    prt.add_argument("number")
    adm = sub.add_parser("create-admin", help="Create an administrator, or reset that account's password")
    adm.add_argument("name")
    adm.add_argument("--password-file", metavar="FILE", help="Read the password from the first line of this file instead of asking")
    uni = sub.add_parser("uninstall", help="Stop the service and remove the program")
    group = uni.add_mutually_exclusive_group()
    group.add_argument("--delete-data", action="store_true", help="Also delete the data, logs and service account")
    group.add_argument("--keep-data", action="store_true", help="Keep the data (the default)")
    args = parser.parse_args(argv)

    if not is_macos() and layout.root == Path("/"):
        print("The background service is only available on macOS. On Windows use the installer; elsewhere use Docker.", flush=True)
        return 2

    if args.command == "status":
        print(status(layout, runner), flush=True)
        return 0
    if args.command == "network":
        if not _need_root("Changing the network option"):
            return 1
        code = configure(layout, listen=LISTEN_NETWORK if args.state == "on" else LISTEN_LOCAL, reload=True, runner=runner)
        if code == 0:
            print(status(layout, runner), flush=True)
        return code
    if args.command == "port":
        if not _need_root("Changing the port"):
            return 1
        code = configure(layout, port=args.number, reload=True, runner=runner)
        if code == 0:
            print(status(layout, runner), flush=True)
        return code
    if args.command == "create-admin":
        if not _need_root("Creating an account"):
            return 1
        return _create_admin(layout, args.name, args.password_file, password)
    if args.command == "uninstall":
        if not _need_root("Uninstalling"):
            return 1
        delete = args.delete_data
        if not delete and not args.keep_data and sys.stdin.isatty():
            delete = ask(f"Also delete the data in {layout.data_dir}? It can contain patient data. [y/N] ").strip().lower() in ("y", "yes")
        return uninstall(layout, delete_data=delete, runner=runner)
    return 2


def _create_admin(layout: Layout, name: str, password_file: str | None, password: Callable[[str], str]) -> int:
    from app.launcher import create_admin

    if password_file:
        try:
            secret = Path(password_file).read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            print("Could not read the first line of the password file.", flush=True)
            return 2
    else:
        secret = password(f"Password for {name}: ")
        if secret != password("Again: "):
            print("The two passwords are not the same.", flush=True)
            return 2
    code = create_admin(name, secret, data_dir=layout.data_dir)
    fix_ownership(layout.data_dir)  # created as root; the service must be able to read and change it
    return code
