from __future__ import annotations

import socket
import time

from pynetdicom import AE, evt
from pynetdicom.sop_class import Verification

from app.models import LocalAE, RemoteNode
from app.tools.echo import CEchoTool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_c_echo_against_in_process_scp() -> None:
    port = _free_port()
    scp = AE(ae_title="TEST_SCP")
    scp.add_supported_context(Verification)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_ECHO, lambda event: 0x0000)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="test scp", ae_title="TEST_SCP", host="127.0.0.1", port=port)
        result = CEchoTool().run(LocalAE(ae_title="DICOMM", timeout_seconds=5), remote)
        assert result.ok, result.summary
        names = [step.name for step in result.steps]
        assert names == ["Association", "C-ECHO", "Release"]
        assert all(step.ok for step in result.steps)
        assert "0x0000" in result.steps[1].message
        assert result.contexts
        assert any(ctx["accepted"] for ctx in result.contexts)
    finally:
        server.shutdown()


def test_c_echo_fails_when_nothing_listens() -> None:
    port = _free_port()
    remote = RemoteNode(name="down", ae_title="MISSING", host="127.0.0.1", port=port)
    result = CEchoTool().run(LocalAE(timeout_seconds=1), remote)
    assert result.ok is False
    assert result.steps
    assert result.steps[0].ok is False
