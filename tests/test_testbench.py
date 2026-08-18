from __future__ import annotations

import socket
import time

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    SecondaryCaptureImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    Verification,
)

from app.models import LocalAE, RemoteNode, WorklistQuery
from app.mwl import query_remote
from app.tools.find import CFindTool
from app.tools.store import CStoreTool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_scp(ae_title: str, port: int, contexts, handlers):
    scp = AE(ae_title=ae_title)
    for context in contexts:
        scp.add_supported_context(context)
    return scp.start_server(("127.0.0.1", port), block=False, evt_handlers=handlers)


def test_c_store_against_in_process_storage_scp() -> None:
    port = _free_port()
    received: list[Dataset] = []

    def handle_store(event):
        received.append(event.dataset)
        return 0x0000

    server = _start_scp(
        "STORE_SCP",
        port,
        [SecondaryCaptureImageStorage],
        [(evt.EVT_C_STORE, handle_store)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="storage", ae_title="STORE_SCP", host="127.0.0.1", port=port)
        result = CStoreTool().run(LocalAE(timeout_seconds=5), remote)
        assert result.ok, result.summary
        assert received
        assert str(received[0].PatientID) == "ARNPRO-TEST"
        assert any(ctx["accepted"] for ctx in result.contexts)
        assert result.records[0]["patient_id"] == "ARNPRO-TEST"
    finally:
        server.shutdown()


def test_c_store_rejected_when_peer_is_verification_only() -> None:
    port = _free_port()
    server = _start_scp(
        "ECHO_ONLY",
        port,
        [Verification],
        [(evt.EVT_C_ECHO, lambda event: 0x0000)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="echo only", ae_title="ECHO_ONLY", host="127.0.0.1", port=port)
        result = CStoreTool().run(LocalAE(timeout_seconds=5), remote)
        assert result.ok is False
        assert result.contexts
        assert "Storage SCP" in result.summary
    finally:
        server.shutdown()


def test_study_root_c_find_against_in_process_scp() -> None:
    port = _free_port()

    def handle_find(event):
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "1001"
        ds.AccessionNumber = "ACC1"
        ds.StudyDate = "20260818"
        ds.StudyDescription = "CT CHEST"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.ModalitiesInStudy = "CT"
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp(
        "QR_SCP",
        port,
        [StudyRootQueryRetrieveInformationModelFind],
        [(evt.EVT_C_FIND, handle_find)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        result = CFindTool().run(
            LocalAE(timeout_seconds=5),
            remote,
            {"patient_id": "1001", "modality": "CT"},
        )
        assert result.ok, result.summary
        assert result.records[0]["patient_id"] == "1001"
        assert result.records[0]["study_instance_uid"] == "1.2.3.4.5"
        assert any(ctx["accepted"] for ctx in result.contexts)
        assert "Study Root" in next(ctx["name"] for ctx in result.contexts if ctx["accepted"])
    finally:
        server.shutdown()


def test_orthanc_like_peer_accepts_store_and_qr_but_rejects_mwl() -> None:
    port = _free_port()

    def handle_store(event):
        return 0x0000

    def handle_find(event):
        yield 0x0000, None

    server = _start_scp(
        "ORTHANC",
        port,
        [Verification, SecondaryCaptureImageStorage, StudyRootQueryRetrieveInformationModelFind],
        [(evt.EVT_C_STORE, handle_store), (evt.EVT_C_FIND, handle_find)],
    )
    try:
        time.sleep(0.05)
        local = LocalAE(timeout_seconds=5)
        remote = RemoteNode(name="Orthanc LAN", ae_title="ORTHANC", host="127.0.0.1", port=port)
        store = CStoreTool().run(local, remote)
        assert store.ok, store.summary
        find = CFindTool().run(local, remote)
        assert find.ok, find.summary
        mwl = query_remote(local, remote, WorklistQuery())
        assert mwl.ok is False
        assert mwl.contexts
        assert any(not ctx["accepted"] for ctx in mwl.contexts)
        assert "not an MWL" in mwl.summary
    finally:
        server.shutdown()


def test_testbench_page_and_c_store_form(client, store) -> None:
    port = _free_port()

    def handle_store(event):
        return 0x0000

    server = _start_scp(
        "STORE_SCP",
        port,
        [SecondaryCaptureImageStorage],
        [(evt.EVT_C_STORE, handle_store)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="storage", ae_title="STORE_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)
        page = client.get("/testbench")
        assert page.status_code == 200
        assert b"Testbench" in page.content
        assert b"Secondary Capture" in page.content
        response = client.post(
            "/testbench/run",
            data={"remote_id": remote.id, "service": "c-store"},
        )
        assert response.status_code == 200
        assert b"Pass" in response.content
        assert b"Accepted" in response.content
    finally:
        server.shutdown()


def test_api_c_find_accepts_options(client, store) -> None:
    port = _free_port()
    seen: list[str] = []

    def handle_find(event):
        seen.append(str(getattr(event.identifier, "PatientID", "")))
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "1001"
        ds.StudyInstanceUID = "1.2.3"
        ds.AccessionNumber = ""
        ds.StudyDate = ""
        ds.StudyDescription = ""
        ds.ModalitiesInStudy = ""
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp(
        "QR_SCP",
        port,
        [StudyRootQueryRetrieveInformationModelFind],
        [(evt.EVT_C_FIND, handle_find)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)
        response = client.post(
            "/api/tools/c-find/run",
            json={"remote_id": remote.id, "options": {"patient_id": "1001"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["records"][0]["patient_id"] == "1001"
        assert seen == ["1001"]
    finally:
        server.shutdown()
