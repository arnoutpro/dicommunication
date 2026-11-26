"""The Dicom Import page: scan, review, edit, send (app/routes/dicom_import.py)."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydicom.uid import generate_uid

from app.import_session import ImportSessions
from app.models import RemoteNode
from app.store import ConfigStore
from test_dicom_import import Pacs, make_file

HX = {"HX-Request": "true"}


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    study = str(generate_uid())
    make_file(tmp_path / "a", "IM1", study_uid=study, number=1)
    make_file(tmp_path / "a", "IM2", study_uid=study, number=2)
    make_file(tmp_path / "b", "IM3", patient_id="P2", patient_name="ROE^RICHARD", modality="MR")
    return tmp_path


@pytest.fixture
def pacs(store: ConfigStore):
    server = Pacs()
    node = server.node()
    store.add_remote(node)
    server.remote = node
    yield server
    server.stop()


def _start(client: TestClient, folder: Path) -> str:
    response = client.post("/tools/dicom-import/scan", data={"folder": str(folder)}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[1]


def _wait(client: TestClient, sid: str, needle: str, seconds: float = 8.0) -> str:
    deadline = time.time() + seconds
    body = ""
    while time.time() < deadline:
        body = client.get(f"/tools/dicom-import/{sid}/body", headers=HX).text
        if needle in body:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} never appeared; last body:\n{body[:1500]}")


def _form(body: str) -> dict[str, str]:
    """The text fields of the review form, as the browser would post them."""
    return {m.group(1): m.group(2) for m in re.finditer(r'<input [^>]*?name="([ps]\d+_[a-z_]+)" value="([^"]*)"', body)}


def test_the_tool_is_in_the_sidebar_and_the_start_page_renders(client: TestClient) -> None:
    page = client.get("/tools/dicom-import")
    assert page.status_code == 200
    assert "Choose the folder" in page.text and 'name="folder"' in page.text and "Dicom Import" in page.text
    assert 'href="/tools/dicom-import"' in client.get("/").text


def test_a_missing_or_empty_folder_is_refused_with_a_reason(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/tools/dicom-import/scan", data={"folder": ""}).status_code == 400
    sid = _start(client, tmp_path / "nope")
    body = _wait(client, sid, "is not a folder")
    assert "Choose another folder" in body


def test_scan_then_review_shows_what_was_found(client: TestClient, folder: Path) -> None:
    sid = _start(client, folder)
    body = _wait(client, sid, "What was found")
    assert "DOE^JANE" in body and "ROE^RICHARD" in body
    assert "<strong>2</strong> patients" in body and "<strong>3</strong> images" in body
    assert "no DICOMDIR" in body
    # The review form is prefilled with what the files say.
    fields = _form(body)
    assert fields["p0_name"] == "DOE^JANE" and fields["p0_patient_id"] == "P1" and fields["p0_birth_date"] == "1980-01-01"
    assert fields["s0_accession"] == "ACC1" and fields["s0_date"] == "2026-11-01" and fields["s0_time"] == "10:15"
    # The full page works too, for a refresh or a bookmark.
    assert "DOE^JANE" in client.get(f"/tools/dicom-import/{sid}").text


def test_check_says_how_many_fields_change_and_catches_bad_values(client: TestClient, folder: Path) -> None:
    sid = _start(client, folder)
    fields = _form(_wait(client, sid, "What was found"))
    fields.update({"p0_patient_id": "NEW1", "s0_accession": "ACC9"})
    body = client.post(f"/tools/dicom-import/{sid}/check", data=fields, headers=HX).text
    assert "2 fields will change" in body and "Nothing has been sent" in body
    fields["p0_birth_date"] = "not a date"
    fields["p0_sex"] = "X"
    body = client.post(f"/tools/dicom-import/{sid}/check", data=fields, headers=HX).text
    assert "Some values need a correction" in body and "use a date like" in body
    assert 'value="not a date"' in body  # what was typed is kept for correction


def test_send_applies_the_edits_and_reports_the_result(client: TestClient, folder: Path, pacs: Pacs) -> None:
    sid = _start(client, folder)
    fields = _form(_wait(client, sid, "What was found"))
    fields.update({"p0_patient_id": "NEW1", "p0_name": "NEW^NAME", "s0_accession": "ACC9"})
    data = {**fields, "remote_id": pacs.remote.id, "decompress": "on"}
    body = client.post(f"/tools/dicom-import/{sid}/send", data={**data, "send_study": [s for s in re.findall(r'name="send_study" value="([^"]+)"', client.get(f"/tools/dicom-import/{sid}/body", headers=HX).text)]}, headers=HX).text
    assert "Sending to" in body or "Result" in body
    done = _wait(client, sid, "were stored")
    assert "All 3 images were stored" in done and "3 detail changes applied" in done
    assert sorted(str(d.PatientID) for d in pacs.received) == ["NEW1", "NEW1", "P2"]
    assert {str(d.PatientName) for d in pacs.received if str(d.PatientID) == "NEW1"} == {"NEW^NAME"}
    assert {d.AccessionNumber for d in pacs.received if str(d.PatientID) == "NEW1"} == {"ACC9"}
    # "Change and send again" returns to the review, with the scan still there.
    again = client.post(f"/tools/dicom-import/{sid}/again", headers=HX).text
    assert "Review and change details" in again and "Send to the PACS" in again


def test_send_checks_before_it_starts(client: TestClient, folder: Path, pacs: Pacs) -> None:
    sid = _start(client, folder)
    body = _wait(client, sid, "What was found")
    fields = _form(body)
    studies = re.findall(r'name="send_study" value="([^"]+)"', body)
    send = f"/tools/dicom-import/{sid}/send"
    assert "Choose the PACS" in client.post(send, data={**fields, "send_study": studies}, headers=HX).text
    assert "Tick at least one study" in client.post(send, data={**fields, "remote_id": pacs.remote.id}, headers=HX).text
    assert pacs.received == []


def test_only_ticked_studies_are_sent(client: TestClient, folder: Path, pacs: Pacs) -> None:
    sid = _start(client, folder)
    body = _wait(client, sid, "What was found")
    studies = re.findall(r'name="send_study" value="([^"]+)"', body)
    client.post(f"/tools/dicom-import/{sid}/send", data={**_form(body), "remote_id": pacs.remote.id, "send_study": studies[:1]}, headers=HX)
    _wait(client, sid, "were stored")
    assert len(pacs.received) == 2 and {str(d.PatientID) for d in pacs.received} == {"P1"}


def test_start_over_drops_the_session(client: TestClient, folder: Path) -> None:
    sid = _start(client, folder)
    _wait(client, sid, "What was found")
    response = client.post(f"/tools/dicom-import/{sid}/discard", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tools/dicom-import"
    gone = client.get(f"/tools/dicom-import/{sid}")
    assert gone.status_code == 404 and "expired" in gone.text


def test_sessions_hold_no_more_than_the_limit_and_expire_when_idle(tmp_path: Path) -> None:
    make_file(tmp_path, "IM1")
    sessions = ImportSessions(idle_seconds=0.6, max_sessions=2)
    first = sessions.start_scan(str(tmp_path))
    second = sessions.start_scan(str(tmp_path))
    for _ in range(100):
        if not (first.busy or second.busy):
            break
        time.sleep(0.02)
    third = sessions.start_scan(str(tmp_path))  # room is made by dropping the longest-idle one
    survivors = [sessions.get(item.id) is not None for item in (first, second)]
    assert survivors.count(True) == 1 and sessions.get(third.id) is not None
    time.sleep(0.8)
    assert sessions.get(first.id) is None and sessions.get(second.id) is None  # idle too long


def test_folder_dialog_title_is_chosen_from_a_fixed_list(client: TestClient, monkeypatch) -> None:
    from app import fs_dialog

    seen: list[str] = []
    monkeypatch.setattr(fs_dialog, "dialogs_available", lambda: True)
    monkeypatch.setattr(fs_dialog, "runtime_os_name", lambda: "nt")
    monkeypatch.setattr(fs_dialog, "_windows_folder", lambda title: seen.append(title) or r"C:\dicom")
    assert fs_dialog.pick_directory("dicom") == r"C:\dicom"
    assert fs_dialog.pick_directory("x'; Remove-Item *; '") == r"C:\dicom"  # not trusted: falls back to a known title
    assert seen == [fs_dialog.TITLES["dicom"], fs_dialog.TITLES["pdf"]]


def test_the_zip_file_dialog_has_its_own_button_and_endpoint(client, monkeypatch) -> None:
    from app import fs_dialog
    from app.routes import tools

    page = client.get("/tools/dicom-import").text
    assert 'data-import-browse="zip"' in page and "Archive…" in page and "is-path" in page
    monkeypatch.setattr(tools, "dialogs_available", lambda: True)
    monkeypatch.setattr(tools, "pick_zip_file", lambda: "/data/export.zip")
    assert client.post("/api/fs/pick-zip").json() == {"path": "/data/export.zip"}
    monkeypatch.setattr(tools, "pick_zip_file", lambda: None)
    assert client.post("/api/fs/pick-zip").status_code == 400
    monkeypatch.setattr(tools, "dialogs_available", lambda: False)
    assert client.post("/api/fs/pick-zip").status_code == 503
    # The fixed title is the only text that reaches the dialog script.
    seen: list[str] = []
    monkeypatch.setattr(fs_dialog, "dialogs_available", lambda: True)
    monkeypatch.setattr(fs_dialog, "runtime_os_name", lambda: "nt")
    monkeypatch.setattr(fs_dialog, "_windows_zip", lambda title: seen.append(title) or r"C:\x.zip")
    assert fs_dialog.pick_zip_file() == r"C:\x.zip" and seen == [fs_dialog.ZIP_TITLE]
