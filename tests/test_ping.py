from __future__ import annotations

import socket
import subprocess

import pytest

from app.models import LocalAE, RemoteNode
from app.tools.ping import PingTool

# Sending an ICMP echo needs a raw socket. Containers without CAP_NET_RAW, and
# hosts where /bin/ping is neither setuid nor capability-granted, refuse it.
# That is a property of the sandbox, not of the tool, so the ICMP assertion is
# skipped rather than failed when the refusal is this specific.
ICMP_NOT_PERMITTED = (
    "operation not permitted",
    "permission denied",
    "cap_net_raw",
    "socket: address family not supported",
    "not installed",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _icmp_was_refused_by_the_host(step) -> bool:
    haystack = f"{step.message} {step.details.get('output', '')}".lower()
    return any(needle in haystack for needle in ICMP_NOT_PERMITTED)


def _ping_loopback(local: LocalAE, port: int):
    remote = RemoteNode(name="loopback", ae_title="LOCAL", host="127.0.0.1", port=port)
    result = PingTool().run(local, remote)
    return result, {step.name: step for step in result.steps}


def test_ping_resolves_and_reports_a_closed_port(store) -> None:
    result, steps = _ping_loopback(store.load().local, _free_port())

    assert steps["DNS resolve"].ok
    assert not steps["TCP port"].ok
    assert result.ok is False


def test_ping_reaches_loopback_over_icmp(store) -> None:
    _result, steps = _ping_loopback(store.load().local, _free_port())
    icmp = steps["ICMP ping"]

    if not icmp.ok and _icmp_was_refused_by_the_host(icmp):
        pytest.skip(f"this host does not allow ICMP echo: {icmp.details.get('output', '')}")

    assert icmp.ok


def test_icmp_ping_suppresses_console_window_on_windows(monkeypatch) -> None:
    """A frozen Windows build has no console of its own, so spawning ping.exe
    would otherwise flash an empty console window (its captured output never
    reaches that window — it goes straight into the pipe we already read and
    show inside the app). CREATE_NO_WINDOW stops that window from appearing.
    """
    monkeypatch.setattr("app.paths.runtime_os_name", lambda: "nt")
    monkeypatch.setattr("app.tools.ping.runtime_os_name", lambda: "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr("app.tools.ping.shutil.which", lambda name: "ping.exe")

    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="Reply from 1.2.3.4\n", stderr="")

    monkeypatch.setattr("app.tools.ping.subprocess.run", fake_run)

    remote = RemoteNode(name="pacs", ae_title="PACS", host="1.2.3.4", port=104)
    step = PingTool()._icmp(remote, timeout=2)

    assert captured.get("creationflags") == 0x08000000
    assert step.ok
    assert "Reply from 1.2.3.4" in step.details["output"]


def test_ping_open_tcp_port() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        remote = RemoteNode(name="open", ae_title="OPEN", host="127.0.0.1", port=port)
        result = PingTool().run(LocalAE(timeout_seconds=2), remote)
        steps = {step.name: step for step in result.steps}
        assert steps["TCP port"].ok
        assert result.ok
    finally:
        server.close()
