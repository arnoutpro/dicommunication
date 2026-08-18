from __future__ import annotations

import socket
import time

from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind, Verification

from app.models import LocalAE, RemoteNode, VirtualAE, WorklistEntry, WorklistQuery
from app.mwl import entry_to_dataset, query_worklist
from app.store import ConfigStore
from app.tools.echo import CEchoTool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ae_title(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace").strip()
    return str(value or "").strip()


def test_virtual_ae_is_calling_title_and_station_on_mwl(store: ConfigStore) -> None:
    port = _free_port()
    seen: dict[str, str] = {}
    canned = entry_to_dataset(
        WorklistEntry(
            patient_name="DOE^JANE",
            patient_id="1001",
            modality="CT",
            station_ae_title="CT1",
            accession_number="ACC1",
        )
    )

    def handle_find(event):
        seen["calling"] = _ae_title(event.assoc.requestor.ae_title)
        item = event.identifier.ScheduledProcedureStepSequence[0]
        seen["station"] = _ae_title(getattr(item, "ScheduledStationAETitle", ""))
        seen["modality"] = _ae_title(getattr(item, "Modality", ""))
        yield 0xFF00, canned
        yield 0x0000, None

    scp = AE(ae_title="RISMWL")
    scp.add_supported_context(ModalityWorklistInformationFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="RIS", ae_title="RISMWL", host="127.0.0.1", port=port, kind="mwl")
        store.add_remote(remote)
        identity = VirtualAE(name="CT scanner 1", ae_title="CT1", modality="CT")
        store.add_identity(identity)
        result = query_worklist(store, remote.id, WorklistQuery(), identity.id)
        assert result.ok, result.summary
        assert result.calling_ae == "CT1"
        assert seen["calling"] == "CT1"
        assert seen["station"] == "CT1"
        assert seen["modality"] == "CT"
        assert "as CT1" in result.summary
    finally:
        server.shutdown()


def test_local_worklist_filters_by_identity_station(store: ConfigStore) -> None:
    store.add_worklist_entry(
        WorklistEntry(patient_name="DOE^JANE", patient_id="1001", modality="CT", station_ae_title="CT1")
    )
    store.add_worklist_entry(
        WorklistEntry(patient_name="SMITH^JOHN", patient_id="1002", modality="MR", station_ae_title="MR1")
    )
    identity = VirtualAE(name="CT scanner 1", ae_title="CT1")
    store.add_identity(identity)
    result = query_worklist(store, "local", WorklistQuery(), identity.id)
    assert result.ok
    assert [item.patient_id for item in result.entries] == ["1001"]
    assert result.calling_ae == "CT1"


def test_c_echo_uses_virtual_calling_ae() -> None:
    port = _free_port()
    seen: list[str] = []

    def handle_echo(event):
        seen.append(_ae_title(event.assoc.requestor.ae_title))
        return 0x0000

    scp = AE(ae_title="TEST_SCP")
    scp.add_supported_context(Verification)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_ECHO, handle_echo)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="scp", ae_title="TEST_SCP", host="127.0.0.1", port=port)
        local = LocalAE(ae_title="DICOMM", timeout_seconds=5).model_copy(update={"ae_title": "CT1"})
        result = CEchoTool().run(local, remote)
        assert result.ok, result.summary
        assert seen == ["CT1"]
        assert "CT1" in result.summary
    finally:
        server.shutdown()


def test_api_identity_crud_and_tool_run(client, store: ConfigStore) -> None:
    created = client.post(
        "/api/identities",
        json={"name": "MR1", "ae_title": "MR1", "modality": "MR"},
    )
    assert created.status_code == 201
    identity_id = created.json()["id"]
    listed = client.get("/api/identities").json()
    assert listed[0]["ae_title"] == "MR1"

    port = _free_port()
    seen: list[str] = []

    def handle_echo(event):
        seen.append(_ae_title(event.assoc.requestor.ae_title))
        return 0x0000

    scp = AE(ae_title="TEST_SCP")
    scp.add_supported_context(Verification)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_ECHO, handle_echo)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="scp", ae_title="TEST_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)
        echo = client.post(
            "/api/tools/c-echo/run",
            json={"remote_id": remote.id, "identity_id": identity_id},
        )
        assert echo.status_code == 200
        body = echo.json()
        assert body["ok"] is True
        assert body["calling_ae"] == "MR1"
        assert seen == ["MR1"]
    finally:
        server.shutdown()

    deleted = client.delete(f"/api/identities/{identity_id}")
    assert deleted.status_code == 200
    assert client.get("/api/identities").json() == []
