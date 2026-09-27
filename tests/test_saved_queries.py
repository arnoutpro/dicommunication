from __future__ import annotations

import html
import json
import re
import socket
import time

import pytest
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.models import RemoteNode, SavedQuery
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def pacs(store: ConfigStore):
    port = _free_port()

    def handle_find(event):
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "1001"
        ds.StudyInstanceUID = "1.2.3"
        ds.StudyDate = "20260927"
        yield 0xFF00, ds
        yield 0x0000, None

    scp = AE(ae_title="QR_SCP")
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    time.sleep(0.05)
    remote = RemoteNode(name="Lab PACS", ae_title="QR_SCP", host="127.0.0.1", port=port)
    store.add_remote(remote)
    yield remote
    server.shutdown()


def _run(client, remote, **extra):
    data = {
        "remote_id": remote.id,
        "level": "STUDY",
        "include": ["PatientName", "PatientID", "StudyDate"],
        "key_StudyDate": "2026-09-21-2026-09-27",
        "key_ModalitiesInStudy": "CT",
        "date_preset": "7",
        "sr_include_findings": "on",
        **extra,
    }
    return client.post("/tools/c-find-advanced/run", data=data, headers={"HX-Request": "true"})


def _query_json(page: str) -> str:
    match = re.search(r'name="query_json" value="([^"]*)"', page)
    assert match, "no save form in the result"
    return html.unescape(match.group(1))


def test_save_is_offered_only_after_a_query_that_worked(client, store, pacs) -> None:
    fresh = client.get("/tools/c-find-advanced").text
    assert "Save this query" not in fresh
    assert "Run a query, then save it to list it here." in fresh

    result = _run(client, pacs).text
    assert "Save this query" in result
    fields = json.loads(_query_json(result))
    assert fields["remote_id"] == pacs.id
    assert fields["level"] == "STUDY"
    assert fields["include"] == ["PatientName", "PatientID", "StudyDate"]
    assert fields["values"] == {"StudyDate": "2026-09-21-2026-09-27", "ModalitiesInStudy": "CT"}
    assert fields["date_preset"] == "7"
    assert fields["sr_include_findings"] is True
    assert fields["sr_include_impression"] is False

    # A follow-up (List SR reports) continues from a result; it isn't saved.
    follow = _run(client, pacs, follow="sr_series").text
    assert "Save this query" not in follow

    failed = client.post(
        "/tools/c-find-advanced/run",
        data={"remote_id": pacs.id, "level": "STUDY", "include": ["PatientID"]},
        headers={"HX-Request": "true"},
    ).text
    assert "Save this query" not in failed


def test_save_lists_in_sidebar_and_reopens_filled_in(client, store, pacs) -> None:
    query_json = _query_json(_run(client, pacs).text)
    saved = client.post(
        "/tools/c-find-advanced/saved",
        data={"name": "  Weekly   CT ", "query_json": query_json},
        headers={"HX-Request": "true"},
    )
    assert saved.status_code == 200
    assert "Saved as <strong>Weekly CT</strong>" in saved.text
    # The sidebar list is refreshed in the same response.
    assert 'id="saved-queries-nav" hx-swap-oob="true"' in saved.text

    [query] = store.list_saved_queries()
    assert query.name == "Weekly CT"
    page = client.get(f"/tools/c-find-advanced?saved={query.id}").text
    assert f'href="/tools/c-find-advanced?saved={query.id}" class="active"' in page
    assert '<h2>Weekly CT</h2>' in page
    assert 'data-saved-date-preset="7"' in page
    assert 'name="key_ModalitiesInStudy"\n          value="CT"' in page
    assert re.search(r'name="include"\s+value="StudyDate"\s+checked', page)
    # Not a default return key, not saved: left unchecked.
    assert not re.search(r'name="include"\s+value="StudyInstanceUID"\s+checked', page)
    assert '<input type="checkbox" name="sr_include_impression">' in page
    assert 'name="saved_query_name" value="Weekly CT"' in page

    # Running it again offers to save under the same name, which replaces it.
    rerun = _run(client, pacs, saved_query_name="Weekly CT", date_preset="").text
    assert 'name="name"\n        required\n        maxlength="80"\n        value="Weekly CT"' in rerun
    client.post(
        "/tools/c-find-advanced/saved",
        data={"name": "weekly ct", "query_json": _query_json(rerun)},
        headers={"HX-Request": "true"},
    )
    [replaced] = store.list_saved_queries()
    assert replaced.id == query.id
    assert replaced.date_preset == ""


def test_save_without_a_name_keeps_the_form(client, store, pacs) -> None:
    query_json = _query_json(_run(client, pacs).text)
    response = client.post(
        "/tools/c-find-advanced/saved",
        data={"name": "  ", "query_json": query_json},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 400
    assert '<span class="field-error" id="find-save-error">Give the query a name</span>' in response.text
    assert '<details class="find-save" id="find-save" open>' in response.text
    assert store.list_saved_queries() == []


def test_delete_saved_query(client, store, pacs) -> None:
    query = store.save_query(SavedQuery(name="Old", remote_id=pacs.id))
    page = client.get(f"/tools/c-find-advanced?saved={query.id}").text
    assert f'action="/tools/c-find-advanced/saved/{query.id}/delete"' in page
    response = client.post(f"/tools/c-find-advanced/saved/{query.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert store.list_saved_queries() == []
    # An unknown id opens a blank form rather than an error.
    blank = client.get(f"/tools/c-find-advanced?saved={query.id}")
    assert blank.status_code == 200
    assert "<h2>Query</h2>" in blank.text


def test_unknown_date_preset_is_dropped() -> None:
    assert SavedQuery(name="x", date_preset="tomorrow").date_preset == ""
