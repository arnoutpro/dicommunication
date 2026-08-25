from __future__ import annotations

import socket
import time

import pytest
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind

from app.models import LocalAE, RemoteNode, WorklistEntry, WorklistQuery
from app.mwl import _wildcard_match, query_local, query_remote, query_worklist
from app.mwl_scp import WorklistSCP
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _entry() -> WorklistEntry:
    return WorklistEntry(
        patient_name="DOE^JANE",
        patient_id="1001",
        accession_number="ACC1",
        modality="CT",
        station_ae_title="CT1",
        scheduled_date="2026-08-18",
        requested_procedure_description="CT CHEST",
        study_instance_uid="1.2.3.4.5",
    )


def test_local_worklist_query_filters(store: ConfigStore) -> None:
    store.add_worklist_entry(_entry())
    store.add_worklist_entry(
        WorklistEntry(
            patient_name="SMITH^JOHN",
            patient_id="1002",
            modality="MR",
            station_ae_title="MR1",
        )
    )
    all_items = query_local(store, WorklistQuery())
    assert all_items.ok
    assert len(all_items.entries) == 2
    ct_only = query_local(store, WorklistQuery(modality="CT"))
    assert [item.patient_id for item in ct_only.entries] == ["1001"]
    name = query_local(store, WorklistQuery(patient_name="DOE*"))
    assert len(name.entries) == 1


def test_worklist_page_and_local_crud(client) -> None:
    created = client.post(
        "/worklist/entries",
        data={
            "patient_name": "DOE^JANE",
            "patient_id": "1001",
            "modality": "CT",
            "accession_number": "A1",
            "scheduled_date": "2026-08-18",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"DOE^JANE" in created.content

    queried = client.post(
        "/worklist/query",
        data={"source": "local", "modality": "CT"},
        follow_redirects=True,
    )
    assert queried.status_code == 200
    assert b"1001" in queried.content


def test_remote_mwl_c_find_against_in_process_scp(store: ConfigStore) -> None:
    from app.mwl import entry_to_dataset

    port = _free_port()
    canned = entry_to_dataset(_entry())

    def handle_find(event):
        yield 0xFF00, canned
        yield 0x0000, None

    scp = AE(ae_title="RISMWL")
    scp.add_supported_context(ModalityWorklistInformationFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="RIS", ae_title="RISMWL", host="127.0.0.1", port=port, kind="mwl")
        result = query_remote(LocalAE(timeout_seconds=5), remote, WorklistQuery())
        assert result.ok, result.summary
        assert result.entries[0].patient_id == "1001"
        assert result.entries[0].modality == "CT"
        assert result.contexts
        assert any(ctx["accepted"] for ctx in result.contexts)
    finally:
        server.shutdown()


def test_local_mwl_scp_serves_web_entries(store: ConfigStore) -> None:
    port = _free_port()
    store.save_local(
        LocalAE(ae_title="DICOMM", host="127.0.0.1", port=port, timeout_seconds=5, mwl_scp_enabled=True)
    )
    store.add_worklist_entry(_entry())
    remote = RemoteNode(name="self", ae_title="DICOMM", host="127.0.0.1", port=port)
    store.add_remote(remote)
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        result = query_worklist(store, remote.id, WorklistQuery(modality="CT"))
        assert result.ok, result.summary
        assert len(result.entries) == 1
        assert result.entries[0].accession_number == "ACC1"
        missed = query_worklist(store, remote.id, WorklistQuery(modality="US"))
        assert missed.ok
        assert missed.entries == []
    finally:
        scp.stop()


def test_api_worklist_roundtrip(client) -> None:
    created = client.post(
        "/api/worklist",
        json={"patient_name": "DOE^JANE", "patient_id": "1001", "modality": "CT"},
    )
    assert created.status_code == 201
    listed = client.get("/api/worklist").json()
    assert listed[0]["patient_id"] == "1001"
    queried = client.post("/api/worklist/query", json={"source": "local", "modality": "CT"})
    assert queried.status_code == 200
    assert queried.json()["ok"] is True
    assert queried.json()["entries"][0]["patient_id"] == "1001"


@pytest.mark.parametrize(
    ("query", "value", "expected"),
    [
        ("", "DOE^JANE", True),
        ("DOE", "DOE^JANE", True),
        ("doe", "DOE^JANE", True),
        ("JANE", "DOE^JANE", True),
        ("SMITH", "DOE^JANE", False),
        # '*' is zero or more characters and anchors the whole value.
        ("D*", "DOE^JANE", True),
        ("D*", "SMITH^BOB", False),
        ("*JANE", "DOE^JANE", True),
        ("DOE*JANE", "DOE^JANE", True),
        ("*", "ANYTHING", True),
        # '?' is exactly one character, and used to be ignored unless the query
        # also contained a '*'.
        ("D?E*", "DOE^JANE", True),
        ("D?E*", "DE^JANE", False),
        ("D??^*", "DOE^JANE", True),
        ("?", "A", True),
        ("?", "AB", False),
        # DICOM defines no other metacharacters, so these are literal.
        ("[ABD]OE*", "DOE^JANE", False),
        ("[ABD]OE*", "[ABD]OE^X", True),
        ("D.E*", "DOE^JANE", False),
        ("D.E*", "D.E^JANE", True),
    ],
)
def test_wildcard_match_follows_dicom_semantics(query, value, expected) -> None:
    assert _wildcard_match(query, value) is expected


def test_wildcard_match_is_not_quadratic_on_a_hostile_pattern() -> None:
    # An MWL SCU picks this string. A translated regex backtracks on it for
    # effectively forever; the scan matcher must not.
    started = time.perf_counter()

    assert _wildcard_match("*A" * 40 + "B", "A" * 400) is False

    assert (time.perf_counter() - started) < 1.0


def test_question_mark_filters_the_local_worklist(store: ConfigStore) -> None:
    store.add_worklist_entry(WorklistEntry(patient_name="DOE^JANE", patient_id="1"))
    store.add_worklist_entry(WorklistEntry(patient_name="DE^JANE", patient_id="2"))

    result = query_local(store, WorklistQuery(patient_name="D?E*"))

    assert [entry.patient_id for entry in result.entries] == ["1"]
