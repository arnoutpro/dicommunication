"""The Configuration > Import / export page (app/routes/settings_transfer.py)."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import settings_transfer as st
from app.models import RemoteNode, RouteRule, SavedQuery
from app.store import ConfigStore


@pytest.fixture
def filled(store: ConfigStore) -> ConfigStore:
    remote = store.add_remote(RemoteNode(name="PACS prod", ae_title="PACS", host="10.0.0.5", port=104)).remotes[-1]
    store.save_query(SavedQuery(name="Open exams", remote_id=remote.id, values={"PatientID": "123456"}))
    store.add_route_rule(RouteRule(name="Nightly", source_remote_id=remote.id, status="active", seen_study_uids=["1.2.3"]))
    return store


def _settings_file(filled: ConfigStore, sections=st.DEFAULT_IMPORT) -> bytes:
    return st.export_bytes(filled, sections)


def test_the_page_renders_with_both_sections_and_a_sidebar_entry(client: TestClient, filled: ConfigStore) -> None:
    page = client.get("/config/transfer")
    assert page.status_code == 200
    assert "Download settings file" in page.text and "Check the file" in page.text
    assert 'href="/config/transfer" class="active">Import / export' in page.text
    assert 'class="tab-item active"' not in page.text  # it is under Configuration, not a product tab
    # Defaults: local AE and saved queries are not ticked.
    boxes = dict(re.findall(r'name="section" value="(\w+)"( checked)?', page.text))
    assert boxes["remotes"] and boxes["rules"] and boxes["identities"] and not boxes["local"] and not boxes["queries"]


def test_export_downloads_json_with_the_chosen_sections_only(client: TestClient, filled: ConfigStore) -> None:
    response = client.post("/config/transfer/export", data={"section": ["remotes", "queries"]})
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    assert re.fullmatch(r'attachment; filename="dicommunication-settings-\d{4}-\d{2}-\d{2}\.json"', response.headers["content-disposition"])
    assert response.headers["cache-control"] == "no-store"
    document = json.loads(response.content)
    assert set(document["sections"]) == {"remotes", "queries"} and document["format"] == st.FORMAT
    assert document["sections"]["remotes"][0]["name"] == "PACS prod"


def test_export_needs_at_least_one_section(client: TestClient) -> None:
    response = client.post("/config/transfer/export", data={})
    assert response.status_code == 400 and "Tick at least one" in response.text


def test_a_bad_file_is_refused_with_a_reason(client: TestClient) -> None:
    def upload(data: bytes, name: str = "x.json"):
        return client.post("/config/transfer/preview", files={"file": (name, data, "application/json")})

    assert "not readable JSON" in upload(b"nope").text
    assert "not a Dicommunication settings file" in upload(b'{"a": 1}').text
    assert "nothing to import" in upload(json.dumps({"format": st.FORMAT, "version": 1, "sections": {}}).encode()).text
    assert "Choose a settings file first" in client.post("/config/transfer/preview").text


def test_preview_then_import_into_another_computer(client: TestClient, filled: ConfigStore, tmp_path) -> None:
    raw = _settings_file(filled)
    target = ConfigStore(tmp_path / "other")
    client.app.state.store = target  # this "computer" starts empty
    preview = client.post("/config/transfer/preview", files={"file": ("settings.json", raw, "application/json")})
    assert preview.status_code == 200
    assert "settings.json" in preview.text and "PACS prod" in preview.text and "Nightly" in preview.text
    assert 'class="badge ok">New' in preview.text and "new rules start stopped" in preview.text
    assert target.load().remotes == [] and target.list_route_rules() == []  # a preview writes nothing
    token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)

    done = client.post("/config/transfer/apply", data={"token": token, "section": ["remotes", "rules", "queries"]})
    assert done.status_code == 200 and "Imported: 3 added, 0 updated, 0 already the same." in done.text
    assert "added stopped" in done.text
    assert [r.name for r in target.load().remotes] == ["PACS prod"]
    (rule,) = target.list_route_rules()
    assert rule.status == "stopped" and rule.seen_study_uids == []
    # The held file is used up.
    again = client.post("/config/transfer/apply", data={"token": token, "section": ["remotes"]})
    assert again.status_code == 404 and "no longer held" in again.text


def test_only_ticked_parts_are_imported_and_none_is_an_error(client: TestClient, filled: ConfigStore, tmp_path) -> None:
    raw = _settings_file(filled)
    target = ConfigStore(tmp_path / "other")
    client.app.state.store = target
    preview = client.post("/config/transfer/preview", files={"file": ("s.json", raw, "application/json")})
    token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)
    assert "Tick at least one part" in client.post("/config/transfer/apply", data={"token": token}).text
    client.post("/config/transfer/apply", data={"token": token, "section": ["remotes"]})
    assert len(target.load().remotes) == 1 and target.list_route_rules() == [] and target.list_saved_queries() == []


def test_cancel_drops_the_held_file(client: TestClient, filled: ConfigStore, tmp_path) -> None:
    client.app.state.store = ConfigStore(tmp_path / "other")
    preview = client.post("/config/transfer/preview", files={"file": ("s.json", _settings_file(filled), "application/json")})
    token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)
    response = client.post("/config/transfer/cancel", data={"token": token}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/config/transfer"
    assert client.post("/config/transfer/apply", data={"token": token, "section": ["remotes"]}).status_code == 404


def test_importing_into_the_same_computer_changes_nothing(client: TestClient, filled: ConfigStore) -> None:
    preview = client.post("/config/transfer/preview", files={"file": ("s.json", _settings_file(filled), "application/json")})
    assert preview.text.count("Already the same") >= 3 and 'class="badge ok">New' not in preview.text
    token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)
    done = client.post("/config/transfer/apply", data={"token": token, "section": ["remotes", "rules", "queries"]})
    assert "0 added, 0 updated, 3 already the same" in done.text


def test_held_files_are_limited_and_expire(client: TestClient, filled: ConfigStore, monkeypatch) -> None:
    from app.routes import settings_transfer as route

    raw = _settings_file(filled)
    tokens = [
        re.search(r'name="token" value="([^"]+)"', client.post("/config/transfer/preview", files={"file": ("s.json", raw, "application/json")}).text).group(1)
        for _ in range(route.MAX_PENDING + 2)
    ]
    assert len(client.app.state.settings_pending) == route.MAX_PENDING
    assert tokens[0] not in client.app.state.settings_pending and tokens[-1] in client.app.state.settings_pending
    monkeypatch.setattr(route, "PENDING_SECONDS", -1)
    assert client.post("/config/transfer/apply", data={"token": tokens[-1], "section": ["remotes"]}).status_code == 404
