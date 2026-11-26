from __future__ import annotations

import pytest
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


# --- "Present as" with a typed AE Title ----------------------------------------------------------


def test_a_typed_ae_title_is_the_calling_ae_without_being_saved(client, store: ConfigStore) -> None:
    config = store.load()
    assert config.calling_ae("ae:MYAE").ae_title == "MYAE"
    assert config.calling_ae("ae:MYAE").port == config.local.port  # everything else stays the workstation's
    assert config.get_identity("ae:MYAE") is None and not store.load().identities
    for bad in ("ae:", "ae: ", "ae:" + "X" * 17, "ae:A\\B"):
        with pytest.raises(KeyError):
            config.calling_ae(bad)

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
        echo = client.post("/api/tools/c-echo/run", json={"remote_id": remote.id, "identity_id": "ae:TYPED1"})
        assert echo.status_code == 200 and echo.json()["calling_ae"] == "TYPED1" and seen == ["TYPED1"]
        bad = client.post("/api/tools/c-echo/run", json={"remote_id": remote.id, "identity_id": "ae:"})
        assert bad.status_code == 400 and "AE Title is not valid" in bad.text
    finally:
        server.shutdown()


def test_every_present_as_dropdown_offers_the_typed_choice(client, store: ConfigStore, remote) -> None:
    checked = 0
    for path in ("/worklist", "/tools/c-echo", "/tools/c-find", "/tools/pdf-store", "/tools/send-image", "/tools/tag-editor", "/tools/anonymize", "/tools/dicom-cleaner", "/tools/dicom-import", "/testbench"):
        page = client.get(path)
        if page.status_code == 200 and 'name="identity_id"' in page.text:
            assert "data-identity-pick" in page.text and "data-typed" in page.text and "data-identity-ae" in page.text, path
            checked += 1
    assert checked >= 5


def test_a_router_pdf_rule_can_present_as_a_typed_title(client, store: ConfigStore, remote, tmp_path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    data = {
        "kind": "pdf_folder", "name": "Reports", "pdf_folder": str(folder), "pdf_destination_id": remote.id,
        "pdf_modality": "DOC", "pdf_patient_mode": "per_file", "pdf_generate_document_title": "on",
        "schedule_mode": "interval", "interval_minutes": "15", "pdf_identity_id": "ae:SCANNER1",
    }
    assert client.post("/router", data=data, follow_redirects=False).status_code == 303
    rule = store.list_route_rules()[0]
    assert rule.pdf_identity_id == "ae:SCANNER1" and store.load().calling_ae(rule.pdf_identity_id).ae_title == "SCANNER1"
    page = client.get(f"/router?edit={rule.id}").text
    assert 'value="SCANNER1"' in page and "data-typed" in page
    bad = client.post("/router", data={**data, "pdf_identity_id": "ae:"})
    assert bad.status_code == 400 and 'id="error-pdf_identity_id"' in bad.text
