"""Dicom Send: find studies on one archive and send them to another registered archive."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pynetdicom import AE, evt

from app.main import create_app
from app.models import LocalAE, RemoteNode
from app.store import ConfigStore
from test_cleaner import _free_port, _make_instance, _start_find_move_store_scp

STUDY = "1.2.826.0.1.3680043.8.498.41223344"
SERIES = "1.2.826.0.1.3680043.8.498.41223345"
SOP = "1.2.826.0.1.3680043.8.498.41223346"


def _archive(instance, port: int, known_aet: str | None = None):
    """A second archive that only stores what it is sent."""
    received: list = []

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        received.append(ds)
        return 0x0000

    ae = AE(ae_title="ARCHIVE_B")
    ae.add_supported_context(str(instance.SOPClassUID))
    if known_aet:
        ae.require_calling_aet = [known_aet]  # an archive that only knows one calling AE Title
    return ae.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_STORE, handle_store)]), received


def _setup(tmp_path, known_aet: str | None = None):
    instance = _make_instance(STUDY, SERIES, SOP, rows=4, cols=4)
    find_port, local_port, dest_port = _free_port(), _free_port(), _free_port()
    source_server, source_received = _start_find_move_store_scp(find_port, local_port, STUDY, instance)
    dest_server, dest_received = _archive(instance, dest_port, known_aet)
    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    source = RemoteNode(name="archive a", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    destination = RemoteNode(name="archive b", ae_title="ARCHIVE_B", host="127.0.0.1", port=dest_port)
    store.add_remote(source)
    store.add_remote(destination)
    return store, source, destination, [source_server, dest_server], dest_received


def test_find_studies_then_send_them_to_another_archive(tmp_path) -> None:
    store, source, destination, servers, dest_received = _setup(tmp_path)
    try:
        with TestClient(create_app(store)) as client:
            page = client.get("/tools/dicom-send")
            assert page.status_code == 200
            for text in ("Choose the archives", "Send from", "Send to", "Find studies", "Dicom Send"):
                assert text in page.text, text
            query = client.post(
                "/tools/dicom-send/run",
                data={
                    "action": "query", "remote_id": source.id, "destination_remote_id": destination.id,
                    "key_StudyDate": "20260101", "key_StudyDescription": "US*", "include": ["PatientName", "PatientID", "StudyDate", "StudyDescription"],
                },
            )
            assert query.status_code == 200 and "DOE^JANE" in query.text
            assert "Patient Name</th>" in query.text and 'value="US*"' in query.text  # labels, and what was typed comes back
            assert "Choose studies" in query.text and "Send selected" in query.text
            assert f'value="{destination.id}" selected' in query.text  # the choice survives the query
            assert "archive b" in query.text  # the confirm names where it goes

            run = client.post(
                "/tools/dicom-send/run",
                data={"action": "run", "remote_id": source.id, "level": "STUDY", "study_uid": [STUDY], "destination_remote_id": destination.id},
            )
            assert run.status_code == 200 and "Sent 1 instance(s) from archive a to archive b" in run.text, run.text
            assert len(dest_received) == 1
            assert str(dest_received[0].SOPInstanceUID) == SOP and str(dest_received[0].StudyInstanceUID) == STUDY  # unchanged
    finally:
        for server in servers:
            server.shutdown()


def test_a_destination_is_required_and_must_differ_from_the_source(tmp_path) -> None:
    store, source, destination, servers, dest_received = _setup(tmp_path)
    try:
        with TestClient(create_app(store)) as client:
            data = {"action": "run", "remote_id": source.id, "level": "STUDY", "study_uid": [STUDY]}
            none = client.post("/tools/dicom-send/run", data=data)
            assert "Choose the archive to send to." in none.text and not dest_received
            same = client.post("/tools/dicom-send/run", data={**data, "destination_remote_id": source.id})
            assert "same archive" in same.text and not dest_received
            nothing = client.post("/tools/dicom-send/run", data={**data, "study_uid": [], "destination_remote_id": destination.id})
            assert "Select at least one study" in nothing.text
    finally:
        for server in servers:
            server.shutdown()


def test_send_needs_two_registered_nodes(client, store, remote) -> None:
    page = client.get("/tools/dicom-send")
    assert page.status_code == 200 and "Two remote nodes are needed" in page.text


def test_dicom_send_has_its_own_tab_and_the_dashboard_does_not_list_it_twice(client) -> None:
    body = client.get("/tools/dicom-send").text
    assert '<a href="/tools/dicom-send" class="tab-item active">' in body and "</svg>Dicom Send</a>" in body
    assert body.index("</svg>Dicom Router</a>") < body.index("</svg>Dicom Send</a>") < body.index("</svg>Dicom Cleaner</a>")


def test_the_find_form_is_the_analytics_one_at_study_level(client, store, remote) -> None:
    store.add_remote(RemoteNode(name="second", ae_title="SECOND", host="127.0.0.1", port=11113))
    page = client.get("/tools/dicom-send").text
    assert "data-find-advanced" in page and "Return · Field" in page and "Match value" in page
    for keyword in ("StudyDescription", "ReferringPhysicianName", "TamarStudyBodyPart", "TamarPatientClass", "TamarStudyReason"):
        assert f'name="key_{keyword}"' in page, keyword
    assert "data-find-level" not in page and 'name="combine_series"' not in page  # no Series or Image level here


def test_a_filter_that_is_not_valid_is_refused_with_its_name(tmp_path) -> None:
    store, source, destination, servers, _received = _setup(tmp_path)
    try:
        with TestClient(create_app(store)) as client:
            def query(**extra):
                return client.post("/tools/dicom-send/run", data={"action": "query", "remote_id": source.id, "destination_remote_id": destination.id, **extra}).text

            assert "Tamar Study Reason: remove the backslash" in query(key_TamarStudyReason="fall\\chest")
            assert "Tamar Study Reason: remove the line breaks" in query(key_TamarStudyReason="fall\nchest")
            assert "Tamar Study Reason: at most 1024 characters" in query(key_TamarStudyReason="x" * 1025)
            assert "Tamar Patient Class: at most 16 characters" in query(key_TamarPatientClass="I" * 17)
            assert "only Latin-1" in query(key_StudyDescription="\u4e2d\u6587")
            assert "Send selected" in query(key_TamarStudyReason="*chest pain*", key_StudyDescription="US*")
    finally:
        for server in servers:
            server.shutdown()


def test_match_values_are_validated_and_sent_as_one_value(tmp_path) -> None:
    from app.tools.find_keys import build_identifier, validate_study_values

    values = validate_study_values({"TamarStudyReason": "%chest%", "TamarPatientClass": "i", "ReferringPhysicianName": "SMITH*", "StudyDate": "2026-01-01"})
    assert values == {"TamarStudyReason": "*chest*", "TamarPatientClass": "i", "ReferringPhysicianName": "SMITH*", "StudyDate": "20260101"}
    identifier = build_identifier("STUDY", {"StudyDescription": "CAF\u00c9*"}, ["StudyInstanceUID"])
    assert str(identifier.SpecificCharacterSet) == "ISO_IR 100"  # a character outside ASCII is declared
    assert "SpecificCharacterSet" not in build_identifier("STUDY", {"StudyDescription": "CAFE*"}, ["StudyInstanceUID"])
    block = build_identifier("STUDY", {"TamarStudyReason": "*fall*"}, []).private_block(0x07A3, "ELSCINT1")
    assert block[0x18].value == "*fall*"


def test_the_anonymizer_and_the_cleaner_got_the_extra_study_filters(client, store, remote) -> None:
    for path in ("/tools/anonymize", "/tools/dicom-cleaner"):
        page = client.get(path).text
        for name in ("study_description", "referring_physician", "body_part", "patient_class", "study_reason"):
            assert f'name="{name}"' in page, (path, name)
    bad = client.post("/tools/dicom-cleaner/run", data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01", "study_reason": "a\\b"})
    assert "Tamar Study Reason: remove the backslash" in bad.text
    kept = client.post("/tools/dicom-cleaner/run", data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01", "study_description": "*CHEST*", "study_reason": "a\\b"})
    assert 'value="*CHEST*"' in kept.text  # what was typed is put back


def test_the_page_and_the_result_say_the_receiving_archive_must_know_the_calling_ae(tmp_path) -> None:
    store, source, destination, servers, dest_received = _setup(tmp_path, known_aet="SOMEONE_ELSE")
    try:
        with TestClient(create_app(store)) as client:
            page = client.get("/tools/dicom-send").text
            assert "The receiving archive must know this AE." in page and "known AE on the <em>Send to</em> archive" in page
            run = client.post(
                "/tools/dicom-send/run",
                data={"action": "run", "remote_id": source.id, "level": "STUDY", "study_uid": [STUDY], "destination_remote_id": destination.id},
            )
            assert not dest_received
            assert "archive b must know the calling AE Title DICOMM as an allowed AE" in run.text, run.text
            # Presenting as an AE Title the archive does know makes the same send work.
            ok = client.post(
                "/tools/dicom-send/run",
                data={"action": "run", "remote_id": source.id, "identity_id": "ae:SOMEONE_ELSE", "level": "STUDY", "study_uid": [STUDY], "destination_remote_id": destination.id},
            )
            assert len(dest_received) == 1 and "Sent 1 instance(s)" in ok.text, ok.text
    finally:
        for server in servers:
            server.shutdown()
