from __future__ import annotations

import socket

from app.models import LocalAE, RemoteNode
from app.tools.ping import PingTool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_ping_localhost_dns_and_icmp(store) -> None:
    closed = _free_port()
    remote = RemoteNode(name="loopback", ae_title="LOCAL", host="127.0.0.1", port=closed)
    result = PingTool().run(store.load().local, remote)
    steps = {step.name: step for step in result.steps}
    assert steps["DNS resolve"].ok
    assert steps["ICMP ping"].ok
    assert not steps["TCP port"].ok
    assert result.ok is False


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
