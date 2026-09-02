from __future__ import annotations

import socket
import time

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    SecondaryCaptureImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.models import LocalAE, RemoteNode
from app.mwl_scp import WorklistSCP
from app.tools.tag_editor import (
    FINAL_SIGN_TIMESTAMP_TAG,
    LAST_COMPOSED_BY_TAG,
    TagEditorTool,
    to_dicom_dt,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_instance(
    *,
    study_uid: str = "1.2.3",
    final_sign_timestamp: str | None = "20260902084152.000000",
    last_composed_by: str | None = "467850@24",
) -> Dataset:
    ds = Dataset()
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = "1.2.3.99"
    ds.Modality = "CT"
    if final_sign_timestamp is not None:
        block = ds.private_block(FINAL_SIGN_TIMESTAMP_TAG[0], "ELSCINT1", create=True)
        block.add_new(FINAL_SIGN_TIMESTAMP_TAG[1] & 0xFF, "DT", final_sign_timestamp)
    if last_composed_by is not None:
        block = ds.private_block(LAST_COMPOSED_BY_TAG[0], "ELSCINT1", create=True)
        block.add_new(LAST_COMPOSED_BY_TAG[1] & 0xFF, "LO", last_composed_by)
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    return ds


def _start_move_scp(port: int, storage_port: int, instances: list[Dataset]):
    """A fake PACS: C-MOVEs the given instances back to us, and can accept C-STORE."""
    received: list[Dataset] = []

    def handle_move(event):
        yield "127.0.0.1", storage_port, {
            "contexts": [build_context(str(ds.SOPClassUID)) for ds in instances]
        }
        yield len(instances)
        for ds in instances:
            yield 0xFF00, ds

    def handle_store(event):
        received.append(event.dataset)
        return 0x0000

    move_ae = AE(ae_title="QR_SCP")
    move_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    move_ae.add_supported_context(SecondaryCaptureImageStorage)
    server = move_ae.start_server(
        ("127.0.0.1", port),
        block=False,
        evt_handlers=[(evt.EVT_C_MOVE, handle_move), (evt.EVT_C_STORE, handle_store)],
    )
    return server, received


def _local_ae(store, storage_port: int) -> LocalAE:
    local = LocalAE(
        ae_title="DICOMM",
        host="127.0.0.1",
        port=storage_port,
        timeout_seconds=5,
        storage_scp_enabled=True,
    )
    store.save_local(local)
    return local


def test_to_dicom_dt_converts_datetime_local_input() -> None:
    assert to_dicom_dt("2026-09-02T08:41:52") == "20260902084152.000000"
    assert to_dicom_dt("2026-09-02T08:41:52.5") == "20260902084152.500000"


def test_to_dicom_dt_rejects_empty() -> None:
    with pytest.raises(ValueError):
        to_dicom_dt("")


def test_fetch_requires_study_uid() -> None:
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = TagEditorTool().run(LocalAE(ae_title="DICOMM"), remote, {"action": "fetch"})
    assert result.ok is False
    assert "Study Instance UID is required" in result.summary


def test_push_requires_at_least_one_new_value(store) -> None:
    local = _local_ae(store, _free_port())
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = TagEditorTool().run(
        local,
        remote,
        {
            "action": "push",
            "study_uid": "1.2.3",
            "_listen_ae": "DICOMM",
            "_storage_enabled": True,
            "_storage_running": True,
        },
    )
    assert result.ok is False
    assert "Enter at least one new value" in result.summary


def test_gate_blocks_when_storage_scp_disabled() -> None:
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = TagEditorTool().run(
        LocalAE(ae_title="DICOMM", timeout_seconds=2),
        remote,
        {"action": "fetch", "study_uid": "1.2.3", "_listen_ae": "DICOMM"},
    )
    assert result.ok is False
    assert "Accept C-STORE is off" in result.summary


def test_fetch_reads_current_tag_values(store) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    instance = _make_instance()
    move_server, _received = _start_move_scp(move_port, storage_port, [instance])
    local = _local_ae(store, storage_port)
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        result = TagEditorTool().run(
            local,
            remote,
            {
                "action": "fetch",
                "study_uid": "1.2.3",
                "_listen_ae": "DICOMM",
                "_storage_enabled": True,
                "_storage_running": True,
            },
        )
        assert result.ok, result.summary
        assert len(result.records) == 1
        assert result.records[0]["final_sign_timestamp"] == "20260902084152.000000"
        assert result.records[0]["last_composed_by"] == "467850@24"
    finally:
        scp.stop()
        move_server.shutdown()


def test_push_overwrites_existing_tags_and_stores_back(store) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    instance = _make_instance()
    move_server, received = _start_move_scp(move_port, storage_port, [instance])
    local = _local_ae(store, storage_port)
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        result = TagEditorTool().run(
            local,
            remote,
            {
                "action": "push",
                "study_uid": "1.2.3",
                "final_sign_timestamp": "2026-09-02T10:15:30",
                "last_composed_by": "999999@24",
                "_listen_ae": "DICOMM",
                "_storage_enabled": True,
                "_storage_running": True,
            },
        )
        assert result.ok, result.summary
        assert result.records[0]["store"] == "stored"
        assert result.records[0]["edited"] == (
            "Tamar Report Final Sign Timestamp, Tamar Report Study Last Composed By"
        )
        time.sleep(0.05)
        assert len(received) == 1
        pushed = received[0]
        assert str(pushed[FINAL_SIGN_TIMESTAMP_TAG].value).strip() == "20260902101530.000000"
        assert str(pushed[LAST_COMPOSED_BY_TAG].value).strip() == "999999@24"
        # The rest of the instance travels through untouched.
        assert str(pushed.SOPInstanceUID) == str(instance.SOPInstanceUID)
    finally:
        scp.stop()
        move_server.shutdown()


def test_push_never_creates_a_tag_that_was_never_there(store) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    # This instance never had Last Composed By at all.
    instance = _make_instance(last_composed_by=None)
    move_server, received = _start_move_scp(move_port, storage_port, [instance])
    local = _local_ae(store, storage_port)
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        result = TagEditorTool().run(
            local,
            remote,
            {
                "action": "push",
                "study_uid": "1.2.3",
                "final_sign_timestamp": "2026-09-02T10:15:30",
                "last_composed_by": "999999@24",
                "_listen_ae": "DICOMM",
                "_storage_enabled": True,
                "_storage_running": True,
            },
        )
        assert result.ok, result.summary
        assert "Tamar Report Final Sign Timestamp" in result.records[0]["edited"]
        assert "Tamar Report Study Last Composed By" not in result.records[0]["edited"]
        assert "not present" in result.records[0]["skipped"]
        time.sleep(0.05)
        assert len(received) == 1
        pushed = received[0]
        assert str(pushed[FINAL_SIGN_TIMESTAMP_TAG].value).strip() == "20260902101530.000000"
        assert LAST_COMPOSED_BY_TAG not in pushed
    finally:
        scp.stop()
        move_server.shutdown()


def _start_find_scp(port: int, studies: list[dict[str, str]]):
    def handle_find(event):
        identifier = event.identifier
        accession = str(getattr(identifier, "AccessionNumber", "") or "")
        study_date = str(getattr(identifier, "StudyDate", "") or "")
        for study in studies:
            if study["AccessionNumber"] != accession or study["StudyDate"] != study_date:
                continue
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            for key, value in study.items():
                setattr(ds, key, value)
            yield 0xFF00, ds
        yield 0x0000, None

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    return ae.start_server(
        ("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)]
    )


def test_lookup_requires_accession_and_date() -> None:
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = TagEditorTool().run(
        LocalAE(ae_title="DICOMM", timeout_seconds=2),
        remote,
        {"action": "lookup", "accession_number": "7205719954"},
    )
    assert result.ok is False
    assert "Accession Number and Study Date are both required" in result.summary


def test_lookup_resolves_a_single_matching_study() -> None:
    port = _free_port()
    server = _start_find_scp(
        port,
        [
            {
                "AccessionNumber": "7205719954",
                "StudyDate": "20260902",
                "StudyInstanceUID": "1.2.3.99887766",
                "PatientName": "MADURO-MARTHA^E",
                "PatientID": "57055133",
                "StudyDescription": "CWK",
            }
        ],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        result = TagEditorTool().run(
            LocalAE(ae_title="DICOMM", timeout_seconds=5),
            remote,
            {
                "action": "lookup",
                "accession_number": "7205719954",
                "study_date": "2026-09-02",
            },
        )
        assert result.ok, result.summary
        assert len(result.records) == 1
        assert result.records[0]["StudyInstanceUID"] == "1.2.3.99887766"
        assert "1.2.3.99887766" in result.summary
    finally:
        server.shutdown()


def test_lookup_reports_no_match() -> None:
    port = _free_port()
    server = _start_find_scp(port, [])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        result = TagEditorTool().run(
            LocalAE(ae_title="DICOMM", timeout_seconds=5),
            remote,
            {
                "action": "lookup",
                "accession_number": "nope",
                "study_date": "2026-09-02",
            },
        )
        assert result.ok is False
        assert "No study matched" in result.summary
    finally:
        server.shutdown()


def test_lookup_does_not_need_storage_scp(store) -> None:
    # Unlike fetch/push, lookup is a plain C-FIND and needs no local Storage SCP.
    port = _free_port()
    server = _start_find_scp(
        port,
        [
            {
                "AccessionNumber": "ACC1",
                "StudyDate": "20260902",
                "StudyInstanceUID": "1.2.3.99887766",
            }
        ],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        local = LocalAE(ae_title="DICOMM", timeout_seconds=5, storage_scp_enabled=False)
        result = TagEditorTool().run(
            local,
            remote,
            {"action": "lookup", "accession_number": "ACC1", "study_date": "2026-09-02"},
        )
        assert result.ok, result.summary
    finally:
        server.shutdown()
