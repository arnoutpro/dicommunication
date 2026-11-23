"""Windows headless edition: the setup its installer runs after copying files.

`packaging/windows/Headless.wxs` installs the program plus WinSW (a small
service host) and registers a Windows service that runs
`dicommunication.exe --no-browser` in the background. WinSW reads its
settings from `dicommunication-service.xml` next to it; this module writes
that file from the port the installer asked for, and prepares the data folder.
It runs as `dicommunication.exe --configure-service --port N --listen ...`,
an elevated custom action between "files copied" and "service started".

The service runs as LocalService, so its settings and history live in their own
folder (C:\\ProgramData\\Dicommunication), not in a user's profile. That folder
can hold patient data in cleartext (SECURITY.md), so access is limited to
administrators, SYSTEM and the service account.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from xml.sax.saxutils import escape, quoteattr

from app.paths import is_macos, no_console_kwargs, runtime_os_name

SERVICE_NAME = "Dicommunication"
SERVICE_XML_NAME = "dicommunication-service.xml"
SETUP_LOG_NAME = "service-setup.log"
DEFAULT_PORT = 8080
MIN_PORT = 1024
MAX_PORT = 65535
LISTEN_LOCAL = "local"
LISTEN_NETWORK = "network"
HOST_LOCAL = "127.0.0.1"
HOST_NETWORK = "0.0.0.0"  # nosec: opt-in, see SECURITY.md

# Well-known SIDs, so the permissions do not depend on the Windows language.
SID_SYSTEM = "*S-1-5-18"
SID_ADMINISTRATORS = "*S-1-5-32-544"
SID_LOCAL_SERVICE = "*S-1-5-19"


def service_data_dir() -> Path:
    """Where the service keeps its settings, history and logs."""
    if is_macos():
        return Path("/Library/Application Support/Dicommunication")
    base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / "Dicommunication"


def validate_port(value: object) -> int:
    """A usable TCP port for the web interface (1024 to 65535)."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"The port must be a number, not {value!r}.") from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(f"The port must be between {MIN_PORT} and {MAX_PORT}, not {port}.")
    return port


def listen_host(listen: str) -> str:
    if listen == LISTEN_LOCAL:
        return HOST_LOCAL
    if listen == LISTEN_NETWORK:
        return HOST_NETWORK
    raise ValueError(f"listen must be {LISTEN_LOCAL!r} or {LISTEN_NETWORK!r}, not {listen!r}.")


def computer_names() -> list[str]:
    """This computer's names, for the app's Host check when it listens on the network."""
    names: list[str] = []
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            name = getter().strip().lower()
        except OSError:
            continue
        if name and name not in names:
            names.append(name)
    return names


def render_service_xml(
    *,
    port: int,
    listen: str,
    data_dir: str | os.PathLike[str],
    hostnames: list[str] | None = None,
) -> str:
    """The WinSW settings file. ``%BASE%`` is the folder WinSW itself lives in."""
    data = str(PureWindowsPath(str(data_dir)))
    logs = str(PureWindowsPath(data) / "logs")
    arguments = f"--no-browser --host {listen_host(listen)} --port {validate_port(port)}"
    # The service always asks for sign-in, also when only this computer can reach it:
    # anyone logged in to the server (Remote Desktop) could otherwise use it.
    env = [("DICOMM_DATA_DIR", data), ("DICOMM_REQUIRE_LOGIN", "1")]
    if listen == LISTEN_NETWORK and hostnames:
        # The app only answers to localhost and IP addresses unless told a name.
        env.append(("DICOMM_ALLOWED_HOSTS", ",".join(hostnames)))
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Written by the Dicommunication headless setup. Changed on reinstall; use the installer to change the port. -->",
        "<service>",
        f"  <id>{escape(SERVICE_NAME)}</id>",
        f"  <name>{escape(SERVICE_NAME)}</name>",
        "  <description>Arnout.pro Dicommunication: web interface, Dicom Router and status dashboard.</description>",
        "  <executable>%BASE%\\dicommunication.exe</executable>",
        f"  <arguments>{escape(arguments)}</arguments>",
    ]
    lines += [f"  <env name={quoteattr(name)} value={quoteattr(value)} />" for name, value in env]
    lines += [
        f"  <logpath>{escape(logs)}</logpath>",
        '  <log mode="roll-by-size">',
        "    <sizeThreshold>5120</sizeThreshold>",
        "    <keepFiles>5</keepFiles>",
        "  </log>",
        "  <stoptimeout>15 sec</stoptimeout>",
        "</service>",
        "",
    ]
    return "\n".join(lines)


def _log(data_dir: Path, message: str) -> None:
    """Append to the setup log, the only place a failed install can be diagnosed."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(data_dir / SETUP_LOG_NAME, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def secure_data_dir(path: Path) -> bool:
    """Limit the folder to SYSTEM, administrators and the service account.

    False if it could not be done; the caller carries on, because a service
    that runs with default permissions is better than one that never installs.
    """
    if runtime_os_name() != "nt":
        return False
    try:
        completed = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{SID_SYSTEM}:(OI)(CI)F",
                f"{SID_ADMINISTRATORS}:(OI)(CI)F",
                f"{SID_LOCAL_SERVICE}:(OI)(CI)M",
            ],
            check=False,
            capture_output=True,
            timeout=60,
            **no_console_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def configure_service(
    *,
    port: object,
    listen: str,
    install_dir: Path,
    data_dir: Path | None = None,
) -> int:
    """Write the service settings and prepare the data folder. 0 on success."""
    data_dir = data_dir or service_data_dir()
    try:
        number = validate_port(port)
        listen_host(listen)
    except ValueError as exc:
        _log(data_dir, f"setup failed: {exc}")
        return 2
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(data_dir, f"setup failed: cannot create {data_dir}: {exc}")
        return 3
    if not secure_data_dir(data_dir):
        _log(data_dir, "warning: could not restrict the data folder's permissions; set them by hand")
    xml = render_service_xml(port=number, listen=listen, data_dir=data_dir, hostnames=computer_names())
    try:
        (install_dir / SERVICE_XML_NAME).write_text(xml, encoding="utf-8")
    except OSError as exc:
        _log(data_dir, f"setup failed: cannot write {install_dir / SERVICE_XML_NAME}: {exc}")
        return 4
    _log(data_dir, f"configured: port {number}, listen {listen}")
    return 0


def run_configure(port: object, listen: str) -> int:
    install_dir = Path(sys.executable).resolve().parent
    return configure_service(port=port, listen=listen, install_dir=install_dir)
