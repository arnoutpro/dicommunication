from __future__ import annotations

import socket
import time

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    SecondaryCaptureImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
)

from app.dicom_image import IMAGE_SIZE, build_black_image_dataset
from app.models import RemoteNode


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# build_black_image_dataset
# ---------------------------------------------------------------------------


def test_build_black_image_dataset_carries_order_identifiers() -> None:
    ds = build_black_image_dataset(
        patient_name="DOE^JANE",
        patient_id="1001",
        modality="CT",
        accession_number="ACC1",
        study_instance_uid="1.2.3.4.5",
        study_description="Chest study",
        body_part_examined="chest",
        series_description="Synthetic series",
    )
    assert str(ds.PatientName) == "DOE^JANE"
    assert ds.PatientID == "1001"
    assert ds.AccessionNumber == "ACC1"
    assert ds.StudyInstanceUID == "1.2.3.4.5"
    assert ds.Modality == "CT"
    assert ds.StudyDescription == "Chest study"
    assert ds.BodyPartExamined == "CHEST"
    assert ds.SeriesDescription == "Synthetic series"
    assert ds.SOPClassUID == SecondaryCaptureImageStorage
    assert ds.ConversionType == "SYN"
    assert ds.BurnedInAnnotation == "NO"
    assert ds.Rows == IMAGE_SIZE
    assert ds.Columns == IMAGE_SIZE
    array = ds.pixel_array
    assert array.shape == (IMAGE_SIZE, IMAGE_SIZE)
    assert not array.any()  # solid black: every pixel is 0


def test_build_black_image_dataset_generates_study_uid_when_order_has_none() -> None:
    first = build_black_image_dataset(patient_name="DOE^JANE", patient_id="1001", modality="OT")
    second = build_black_image_dataset(patient_name="DOE^JANE", patient_id="1001", modality="OT")
    assert first.StudyInstanceUID
    assert first.StudyInstanceUID != second.StudyInstanceUID
    assert first.SOPInstanceUID != second.SOPInstanceUID


def test_build_black_image_dataset_defaults_modality_to_ot_when_blank() -> None:
    ds = build_black_image_dataset(patient_name="DOE^JANE", patient_id="1001", modality="")
    assert ds.Modality == "OT"


# ---------------------------------------------------------------------------
# HTTP: page, query, and the full query -> run flow against a real (fake) SCP
# ---------------------------------------------------------------------------


def test_send_image_has_its_own_page(client, remote) -> None:
    response = client.get("/tools/send-image")
    assert response.status_code == 200
    assert b"Send Image to PACS" in response.content
    assert b"Find studies" in response.content


def _canned_study() -> Dataset:
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "1001"
    ds.AccessionNumber = "ACC1"
    ds.StudyDate = "20260818"
    ds.StudyDescription = "CT CHEST"
    ds.ModalitiesInStudy = "CT"
    ds.StudyInstanceUID = "1.2.3.4.5"
    return ds


def _start_find_and_storage_scp(port: int):
    canned = _canned_study()
    received: list[Dataset] = []

    def handle_find(event):
        yield 0xFF00, canned
        yield 0x0000, None

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        received.append(ds)
        return 0x0000

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_supported_context(SecondaryCaptureImageStorage)
    server = ae.start_server(
        ("127.0.0.1", port),
        block=False,
        evt_handlers=[
            (evt.EVT_C_FIND, handle_find),
            (evt.EVT_C_STORE, handle_store),
        ],
    )
    return server, received


def test_send_image_query_lists_the_study(client, store) -> None:
    port = _free_port()
    server, _received = _start_find_and_storage_scp(port)
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)

        queried = client.post(
            "/tools/send-image/run",
            data={"remote_id": remote.id, "action": "query"},
        )
        assert queried.status_code == 200
        assert b"DOE^JANE" in queried.content
        assert b"ACC1" in queried.content
        assert b"Pick the study" in queried.content
        assert b'name="orders_json"' in queried.content
    finally:
        server.shutdown()


def test_send_image_full_flow_stores_a_black_image_with_study_identifiers(client, store) -> None:
    port = _free_port()
    server, received = _start_find_and_storage_scp(port)
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)

        queried = client.post(
            "/tools/send-image/run",
            data={"remote_id": remote.id, "action": "query"},
        )
        assert queried.status_code == 200

        sent = client.post(
            "/tools/send-image/run",
            data={
                "remote_id": remote.id,
                "action": "run",
                "order_index": "0",
                "orders_json": (
                    '[{"PatientName":"DOE^JANE","PatientID":"1001",'
                    '"AccessionNumber":"ACC1","StudyInstanceUID":"1.2.3.4.5",'
                    '"StudyDescription":"CT CHEST"}]'
                ),
                "modality": "CT",
                "study_description": "",
                "body_part_examined": "CHEST",
                "series_description": "",
            },
        )
        assert sent.status_code == 200
        assert b"Stored 1 CT image" in sent.content
        assert len(received) == 1
        stored = received[0]
        assert str(stored.PatientName) == "DOE^JANE"
        assert stored.PatientID == "1001"
        assert stored.AccessionNumber == "ACC1"
        assert stored.StudyInstanceUID == "1.2.3.4.5"
        assert stored.Modality == "CT"
        # No typed Study Description: falls back to the study's own description.
        assert stored.StudyDescription == "CT CHEST"
        assert stored.BodyPartExamined == "CHEST"
        assert not stored.pixel_array.any()
    finally:
        server.shutdown()


def test_send_image_run_without_picking_a_study_fails(client, store) -> None:
    port = _free_port()
    server, received = _start_find_and_storage_scp(port)
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)

        sent = client.post(
            "/tools/send-image/run",
            data={
                "remote_id": remote.id,
                "action": "run",
                "order_index": "",
                "orders_json": "[]",
                "modality": "CT",
            },
        )
        assert sent.status_code == 200
        assert b"Pick a study first" in sent.content
        assert not received
    finally:
        server.shutdown()


def test_send_image_run_without_modality_fails(client, store) -> None:
    port = _free_port()
    server, received = _start_find_and_storage_scp(port)
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)

        sent = client.post(
            "/tools/send-image/run",
            data={
                "remote_id": remote.id,
                "action": "run",
                "order_index": "0",
                "orders_json": '[{"PatientName":"DOE^JANE","PatientID":"1001"}]',
                "modality": "",
            },
        )
        assert sent.status_code == 200
        assert b"Choose a modality" in sent.content
        assert not received
    finally:
        server.shutdown()


def test_send_image_run_can_target_a_different_destination(client, store) -> None:
    source_port = _free_port()
    dest_port = _free_port()
    source_server, source_received = _start_find_and_storage_scp(source_port)
    dest_server, dest_received = _start_find_and_storage_scp(dest_port)
    try:
        time.sleep(0.05)
        source = RemoteNode(name="source", ae_title="QR_SCP", host="127.0.0.1", port=source_port)
        dest = RemoteNode(name="dest", ae_title="QR_SCP", host="127.0.0.1", port=dest_port)
        store.add_remote(source)
        store.add_remote(dest)

        sent = client.post(
            "/tools/send-image/run",
            data={
                "remote_id": source.id,
                "action": "run",
                "order_index": "0",
                "orders_json": (
                    '[{"PatientName":"DOE^JANE","PatientID":"1001",'
                    '"AccessionNumber":"ACC1"}]'
                ),
                "modality": "US",
                "destination_remote_id": dest.id,
            },
        )
        assert sent.status_code == 200
        assert b"Stored 1 US image" in sent.content
        assert len(dest_received) == 1
        assert not source_received
    finally:
        source_server.shutdown()
        dest_server.shutdown()
