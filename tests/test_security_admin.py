"""Users, Security and Audit pages (app/routes/security_admin.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import tls
from app.auth import ADMIN, OPERATOR, VIEWER

PASSWORD = "correct horse battery"


def client_for(app, ip: str = "10.0.0.7") -> TestClient:
    return TestClient(app, client=(ip, 50000))


def sign_in(client: TestClient, name: str, password: str = PASSWORD) -> None:
    assert client.post("/login", data={"username": name, "password": password}, follow_redirects=False).status_code == 303


@pytest.fixture
def admin(app):
    app.state.auth.users.add("alice", PASSWORD, ADMIN)
    client = client_for(app)
    sign_in(client, "alice")
    return client


def user_id(app, name: str) -> str:
    return app.state.auth.users.by_username(name).id


# --- users ---------------------------------------------------------------------


def test_an_administrator_adds_a_user_and_the_audit_names_it_without_the_password(app, admin: TestClient) -> None:
    done = admin.post("/config/users", data={"username": "olga", "password": PASSWORD + "!", "role": OPERATOR})
    assert done.status_code == 200 and "olga was created" in done.text.replace("The account ", "") or "olga" in done.text
    assert app.state.auth.users.by_username("olga").role == OPERATOR
    rows = [r for r in app.state.audit.entries() if r["action"] == "User created"]
    assert rows[-1]["detail"] == "olga (operator)" and rows[-1]["user"] == "alice"
    assert (PASSWORD + "!") not in app.state.audit.path.read_text()
    assert client_for(app, "10.0.0.9").post("/login", data={"username": "olga", "password": PASSWORD + "!"}, follow_redirects=False).status_code == 303


def test_a_taken_name_and_a_weak_password_are_refused_on_the_page(app, admin: TestClient) -> None:
    assert "already taken" in admin.post("/config/users", data={"username": "ALICE", "password": PASSWORD, "role": OPERATOR}).text
    assert "at least 12" in admin.post("/config/users", data={"username": "bob", "password": "short", "role": OPERATOR}).text
    assert app.state.auth.users.by_username("bob") is None


def test_changing_a_role_or_disabling_signs_that_person_out(app, admin: TestClient) -> None:
    app.state.auth.users.add("olga", PASSWORD, OPERATOR)
    olga = client_for(app, "10.0.0.8")
    sign_in(olga, "olga")
    assert olga.get("/").status_code == 200
    admin.post(f"/config/users/{user_id(app, 'olga')}/update", data={"role": VIEWER})
    assert app.state.auth.users.by_username("olga").role == VIEWER
    assert olga.get("/", follow_redirects=False).status_code == 303  # signed out; signing in again gives the new role
    sign_in(olga, "olga")
    assert olga.get("/").status_code == 403 and olga.get("/lite").status_code == 200
    admin.post(f"/config/users/{user_id(app, 'olga')}/update", data={"role": VIEWER, "disabled": "on"})
    assert olga.get("/lite", follow_redirects=False).status_code == 303
    assert app.state.auth.users.by_username("olga").disabled


def test_resetting_a_password_signs_the_person_out_and_sets_the_new_one(app, admin: TestClient) -> None:
    app.state.auth.users.add("olga", PASSWORD, OPERATOR)
    olga = client_for(app, "10.0.0.8")
    sign_in(olga, "olga")
    done = admin.post(f"/config/users/{user_id(app, 'olga')}/password", data={"password": "a different long passphrase"})
    assert "was changed" in done.text
    assert olga.get("/", follow_redirects=False).status_code == 303
    assert client_for(app).post("/login", data={"username": "olga", "password": PASSWORD}).status_code == 401
    assert client_for(app).post("/login", data={"username": "olga", "password": "a different long passphrase"}, follow_redirects=False).status_code == 303
    assert "at least 12" in admin.post(f"/config/users/{user_id(app, 'olga')}/password", data={"password": "short"}).text


def test_the_last_administrator_and_your_own_account_are_protected(app, admin: TestClient) -> None:
    me = user_id(app, "alice")
    assert "at least one active administrator" in admin.post(f"/config/users/{me}/update", data={"role": OPERATOR}).text
    assert "cannot delete your own account" in admin.post(f"/config/users/{me}/delete").text
    app.state.auth.users.add("bob", PASSWORD, ADMIN)
    assert "cannot disable your own account" in admin.post(f"/config/users/{me}/update", data={"role": ADMIN, "disabled": "on"}).text
    assert app.state.auth.users.by_username("alice") and not app.state.auth.users.by_username("alice").disabled
    admin.post(f"/config/users/{user_id(app, 'bob')}/delete")
    assert app.state.auth.users.by_username("bob") is None


def test_the_users_page_lists_everyone(app, admin: TestClient) -> None:
    app.state.auth.users.add("olga", PASSWORD, OPERATOR)
    page = admin.get("/config/users").text
    assert "alice" in page and "olga" in page and "(you)" in page and "Add a user" in page


# --- security settings ---------------------------------------------------------


def test_the_security_page_says_whether_sign_in_is_on_and_why(app, client: TestClient) -> None:
    off = client.get("/config/security").text
    assert 'class="badge">Off' in off and "Only this computer can connect" in off
    app.state.auth.exposed = True
    app.state.auth.users.add("alice", PASSWORD, ADMIN)
    admin = client_for(app)
    sign_in(admin, "alice")
    on = admin.get("/config/security").text
    assert 'class="badge ok">On' in on and "other computers can reach this app" in on and "1 account." in on


def test_retention_and_folders_are_saved_and_checked(app, admin: TestClient, tmp_path: Path) -> None:
    saved = admin.post("/config/security", data={"retention_minutes": "45", "history_days": "14", "import_roots": f"{tmp_path}\n\n{tmp_path}\n/data/imports"})
    assert saved.status_code == 200 and "Saved." in saved.text
    settings = json.loads((app.state.store.data_dir / "security.json").read_text())
    assert settings["retention_minutes"] == 45 and settings["history_days"] == 14 and settings["import_roots"] == [str(tmp_path), "/data/imports"]
    for bad_minutes in ("", "soon", "-5", "1441"):
        assert admin.post("/config/security", data={"retention_minutes": bad_minutes, "history_days": "30", "import_roots": ""}).status_code == 400, bad_minutes
    for bad_days in ("", "x", "-1", "3651"):
        assert admin.post("/config/security", data={"retention_minutes": "30", "history_days": bad_days, "import_roots": ""}).status_code == 400, bad_days
    relative = admin.post("/config/security", data={"retention_minutes": "30", "history_days": "30", "import_roots": "imports/here"})
    assert relative.status_code == 400 and "not a full path" in relative.text and "imports/here" in relative.text  # what was typed is kept
    assert json.loads((app.state.store.data_dir / "security.json").read_text())["retention_minutes"] == 45
    assert any(r["action"] == "Security settings changed" for r in app.state.audit.entries())


def test_the_defaults_are_thirty_minutes_and_thirty_days(app) -> None:
    assert app.state.security_settings.load().retention_minutes == 30 and app.state.security_settings.load().history_days == 30 and app.state.security_settings.load().import_roots == []


# --- HTTPS certificate ---------------------------------------------------------


def test_the_certificate_is_shown_and_offered_for_download(app, admin: TestClient, tmp_path: Path) -> None:
    assert "HTTPS" not in admin.get("/config/security").text  # not running under the launcher: nothing to show
    assert admin.get("/config/security/certificate", follow_redirects=False).status_code == 303
    files = tls.ensure_certificate(tmp_path)
    app.state.tls_info = tls.describe(files.cert)
    page = admin.get("/config/security").text
    assert "HTTPS" in page and app.state.tls_info["fingerprint"] in page and "self-signed" in page
    download = admin.get("/config/security/certificate")
    assert download.status_code == 200 and download.content.startswith(b"-----BEGIN CERTIFICATE-----")
    assert b"PRIVATE KEY" not in download.content  # only the public certificate
    app.state.tls_info = {"enabled": False}
    assert "the traffic never leaves it" in admin.get("/config/security").text


# --- audit page ----------------------------------------------------------------


def test_the_audit_page_lists_entries_filters_and_checks_the_chain(app, admin: TestClient) -> None:
    app.state.auth.users.add("olga", PASSWORD, OPERATOR)
    olga = client_for(app, "10.0.0.8")
    sign_in(olga, "olga")
    olga.get("/config")  # denied
    page = admin.get("/config/audit").text
    assert "chain of entries is intact" in page and "Denied: Change configuration" in page and "olga" in page and "10.0.0.8" in page
    only = admin.get("/config/audit", params={"user": "olga"}).text
    assert "olga" in only and "alice" not in re.sub(r'value="[^"]*"', "", only).split("<tbody>")[1]
    assert "Denied" in admin.get("/config/audit", params={"action": "denied"}).text
    assert admin.get("/config/audit/download").text.count("\n") >= 3
    lines = app.state.audit.path.read_text().splitlines()
    edited = json.loads(lines[0])
    edited["user"] = "someone-else"
    app.state.audit.path.write_text("\n".join([json.dumps(edited), *lines[1:]]) + "\n")
    assert "chain is broken" in admin.get("/config/audit").text


def test_reading_the_audit_page_is_itself_recorded(app, admin: TestClient) -> None:
    admin.get("/config/audit")
    assert any(r["action"] == "View the audit log" and r["user"] == "alice" for r in app.state.audit.entries())


def test_a_viewer_can_sign_out_and_is_not_offered_pages_they_cannot_open(app) -> None:
    app.state.auth.users.add("alice", PASSWORD, ADMIN)
    app.state.auth.users.add("vera", PASSWORD, VIEWER)
    viewer = client_for(app)
    sign_in(viewer, "vera")
    page = viewer.get("/lite").text
    assert 'action="/logout"' in page and 'href="/account"' in page and 'href="/router"' not in page
    admin = client_for(app, "10.0.0.9")
    sign_in(admin, "alice")
    assert 'href="/router"' in admin.get("/lite").text
