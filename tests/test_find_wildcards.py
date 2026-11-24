"""A % in a text match value is sent as the DICOM wildcard *, as Vue's own search boxes use it."""

from __future__ import annotations

import socket

from fastapi.testclient import TestClient
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.main import create_app
from app.models import RemoteNode
from app.store import ConfigStore
from app.tools.find_keys import build_identifier, normalize_value


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_percent_becomes_star_in_text_values_only() -> None:
    assert normalize_value("PatientID", "%123%") == "*123*"
    assert normalize_value("PatientID", "12%") == "12*"
    assert normalize_value("PatientName", "DOE%") == "DOE*"
    assert normalize_value("AccessionNumber", "%9") == "*9"
    # Already a DICOM wildcard, a plain value, a date and a UID are left as they are.
    assert normalize_value("PatientID", "*123*") == "*123*" and normalize_value("PatientID", "1001") == "1001"
    assert normalize_value("StudyDate", "20260101") == "20260101"
    assert normalize_value("StudyInstanceUID", "1.2.3") == "1.2.3"
    assert build_identifier("STUDY", {"PatientID": "%123%"}, ["PatientID"]).PatientID == "*123*"


def test_the_query_that_reaches_the_pacs_carries_the_star(tmp_path) -> None:
    port = _free_port()
    asked: list[str] = []

    def handle_find(event):
        asked.append(str(event.identifier.PatientID))
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "9912399"
        ds.StudyInstanceUID = "1.2.3.4"
        yield 0xFF00, ds
        yield 0x0000, None

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = ae.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    store = ConfigStore(tmp_path)
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
    store.add_remote(remote)
    try:
        with TestClient(create_app(store)) as client:
            response = client.post(
                "/tools/c-find-advanced/run",
                data={
                    "remote_id": remote.id, "level": "STUDY", "include": ["PatientName", "PatientID", "StudyInstanceUID"],
                    "key_PatientID": "%123%", "key_StudyDate": "20260826",
                },
                headers={"HX-Request": "true"},
            )
        assert response.status_code == 200 and b"9912399" in response.content
        assert asked == ["*123*"]
    finally:
        server.shutdown()
