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


def test_c_echo_page_offers_manual_entry_without_any_configured_remote(client) -> None:
    page = client.get("/tools/c-echo")
    assert page.status_code == 200
    assert "manual_ae_title" in page.text
    assert "manual_host" in page.text
    assert "No remote node configured" not in page.text


def test_c_echo_run_against_a_manually_entered_node(client) -> None:
    port = _free_port()
    scp = AE(ae_title="AD_HOC_SCP")
    scp.add_supported_context(Verification)
    server = scp.start_server(
        ("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_ECHO, lambda event: 0x0000)]
    )
    try:
        time.sleep(0.05)
        response = client.post(
            "/tools/c-echo/run",
            data={
                "remote_mode": "manual",
                "manual_ae_title": "AD_HOC_SCP",
                "manual_host": "127.0.0.1",
                "manual_port": str(port),
            },
        )
        assert response.status_code == 200
        assert "0x0000" in response.text
        assert "Pass" in response.text
    finally:
        server.shutdown()


def test_c_echo_manual_entry_requires_an_ae_title(client) -> None:
    response = client.post(
        "/tools/c-echo/run",
        data={"remote_mode": "manual", "manual_ae_title": "", "manual_host": "127.0.0.1"},
    )
    assert response.status_code == 400
    assert "AE Title is required" in response.text


def test_c_echo_manual_entry_is_not_saved_to_config(client, store) -> None:
    port = _free_port()
    client.post(
        "/tools/c-echo/run",
        data={
            "remote_mode": "manual",
            "manual_ae_title": "THROWAWAY",
            "manual_host": "127.0.0.1",
            "manual_port": str(port),
        },
    )
    assert not any(node.ae_title == "THROWAWAY" for node in store.load().remotes)
