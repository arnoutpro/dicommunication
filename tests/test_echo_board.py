from __future__ import annotations

import socket
import time

from pynetdicom import AE, evt
from pynetdicom.sop_class import Verification

from app.echo_board import run_all, snapshot
from app.models import LocalAE, RemoteNode
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_echo_board_page_empty(client) -> None:
    page = client.get("/echo-board")
    assert page.status_code == 200
    assert b"C-ECHO board" in page.content
    assert b"No remote nodes yet" in page.content


def test_snapshot_marks_unknown_until_echo_runs(store: ConfigStore) -> None:
    store.add_remote(RemoteNode(name="PACS", ae_title="PACS1", host="10.0.0.1", port=104))
    board = snapshot(store)
    assert board.total == 1
    assert board.unknown == 1
    assert board.rows[0].status == "unknown"


def test_run_all_mixed_success_and_failure(store: ConfigStore) -> None:
    port = _free_port()
    scp = AE(ae_title="TEST_SCP")
    scp.add_supported_context(Verification)
    server = scp.start_server(
        ("127.0.0.1", port),
        block=False,
        evt_handlers=[(evt.EVT_C_ECHO, lambda event: 0x0000)],
    )
    try:
        time.sleep(0.05)
        store.save_local(LocalAE(ae_title="DICOMM", timeout_seconds=1))
        up = RemoteNode(name="Up SCP", ae_title="TEST_SCP", host="127.0.0.1", port=port)
        down = RemoteNode(name="Down node", ae_title="MISSING", host="127.0.0.1", port=_free_port())
        store.add_remote(up)
        store.add_remote(down)

        board = run_all(store)
        by_name = {row.remote.name: row for row in board.rows}
        assert board.total == 2
        assert board.passed == 1
        assert board.failed == 1
        assert by_name["Up SCP"].status == "pass"
        assert by_name["Down node"].status == "fail"
        assert board.duration_ms is not None
        assert store.list_results(limit=10)
    finally:
        server.shutdown()


def test_api_echo_board_run(client, store: ConfigStore) -> None:
    store.save_local(LocalAE(timeout_seconds=1))
    remote = RemoteNode(name="offline", ae_title="OFFLINE", host="127.0.0.1", port=_free_port())
    store.add_remote(remote)

    listed = client.get("/api/echo-board")
    assert listed.status_code == 200
    assert listed.json()["unknown"] == 1

    ran = client.post("/api/echo-board/run")
    assert ran.status_code == 200
    body = ran.json()
    assert body["total"] == 1
    assert body["failed"] == 1
    assert body["rows"][0]["status"] == "fail"
