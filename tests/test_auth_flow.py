"""Login, roles, lockout and the audit trail, through the real app (app/auth_middleware.py)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import ADMIN, OPERATOR, VIEWER

PASSWORD = "correct horse battery"
HX = {"HX-Request": "true"}


def make_client(app, ip: str = "10.0.0.7") -> TestClient:
    return TestClient(app, client=(ip, 50000))


def add_user(app, name: str, role: str, password: str = PASSWORD):
    return app.state.auth.users.add(name, password, role)


def sign_in(client: TestClient, name: str, password: str = PASSWORD, next_url: str = "/"):
    return client.post("/login", data={"username": name, "password": password, "next": next_url}, follow_redirects=False)


@pytest.fixture
def secured(app):
    """An app with an administrator, an operator and a viewer."""
    add_user(app, "alice", ADMIN)
    add_user(app, "olga", OPERATOR)
    add_user(app, "vera", VIEWER)
    return app


def audit_rows(app) -> list[dict]:
    return app.state.audit.entries()


# --- nothing changes until login is on -----------------------------------------


def test_without_any_account_and_on_this_computer_the_app_stays_open(app) -> None:
    client = TestClient(app)
    assert client.get("/").status_code == 200 and client.get("/config").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"  # sign-in is not turned on


def test_actions_are_audited_even_without_a_login(app, client: TestClient) -> None:
    client.post("/logs/clear")
    row = audit_rows(app)[-1]
    assert row["user"] == "(no login)" and row["action"] == "Application log" and row["method"] == "POST" and row["path"] == "/logs/clear"


# --- the first administrator ---------------------------------------------------


def test_the_first_administrator_is_created_on_this_computer_and_login_turns_on(app) -> None:
    client = TestClient(app)
    assert client.post("/setup", data={"username": "alice", "password": PASSWORD, "confirm": "different"}).status_code == 400
    weak = client.post("/setup", data={"username": "alice", "password": "short", "confirm": "short"})
    assert weak.status_code == 400 and "at least 12" in weak.text
    done = client.post("/setup", data={"username": "alice", "password": PASSWORD, "confirm": PASSWORD}, follow_redirects=False)
    assert done.status_code == 303 and "dicomm_session" in done.headers["set-cookie"]
    assert client.get("/config").status_code == 200  # signed in as the new administrator
    assert make_client(app).get("/", follow_redirects=False).status_code == 303  # everyone else must sign in now
    assert any(r["action"] == "Created the first administrator" for r in audit_rows(app))
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"  # only possible once


def test_setup_is_refused_from_another_computer(app) -> None:
    app.state.auth.exposed = True
    remote = make_client(app, "10.0.0.99")
    assert remote.get("/setup").status_code == 403
    assert remote.post("/setup", data={"username": "mallory", "password": PASSWORD, "confirm": PASSWORD}).status_code == 403
    assert not app.state.auth.users.has_users()


def test_an_exposed_app_with_no_account_serves_only_the_setup_page(app) -> None:
    app.state.auth.exposed = True
    remote = make_client(app)
    for path in ("/", "/config", "/tools/dicom-import", "/lite", "/api/remotes"):
        response = remote.get(path)
        assert response.status_code == 503 and "Setup needed" in response.text, path
    assert remote.get("/health").status_code == 200 and remote.get("/static/css/app.css").status_code == 200


# --- signing in ----------------------------------------------------------------


def test_every_page_asks_for_a_sign_in_first(secured) -> None:
    client = make_client(secured)
    redirect = client.get("/tools/dicom-import?x=1", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/login?next=/tools/dicom-import?x=1"
    assert client.post("/router/abc/run-now", follow_redirects=False).status_code == 303
    htmx = client.get("/tools/dicom-import/abc/body", headers=HX)
    assert htmx.status_code == 401 and htmx.headers["hx-redirect"] == "/login"
    api = client.get("/api/tools")
    assert api.status_code == 401 and api.json() == {"detail": "Sign in required"}
    assert client.get("/health").status_code == 200 and client.get("/login").status_code == 200


def test_a_good_login_sets_a_locked_down_cookie_and_goes_where_you_were_headed(secured) -> None:
    client = make_client(secured)
    response = sign_in(client, "ALICE", next_url="/router")  # the name is not case sensitive
    assert response.status_code == 303 and response.headers["location"] == "/router"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie and "max-age" not in cookie
    assert client.get("/router").status_code == 200


def test_the_return_address_cannot_send_you_to_another_site(secured) -> None:
    for target in ("https://evil.example/", "//evil.example", "/\\evil.example"):
        response = sign_in(make_client(secured), "alice", next_url=target)
        assert response.headers["location"] == "/", target


def test_wrong_name_and_wrong_password_look_the_same(secured) -> None:
    client = make_client(secured)
    unknown = sign_in(client, "nobody", PASSWORD)
    wrong = sign_in(client, "alice", "not the password")
    assert unknown.status_code == wrong.status_code == 401
    assert "Wrong username or password." in unknown.text and "Wrong username or password." in wrong.text
    assert re.sub(r'value="[^"]*"', "", unknown.text).replace("nobody", "") == re.sub(r'value="[^"]*"', "", wrong.text).replace("alice", "")


def test_five_wrong_passwords_lock_the_account_for_a_while(secured) -> None:
    client = make_client(secured)
    for _ in range(5):
        assert sign_in(client, "alice", "wrong password!").status_code == 401
    locked = sign_in(client, "alice", PASSWORD)  # even the right password is refused while locked
    assert locked.status_code == 429 and "Try again in" in locked.text
    other_computer = sign_in(make_client(secured, "10.0.0.8"), "alice", PASSWORD)
    assert other_computer.status_code == 429  # the name is locked wherever it comes from
    rows = [r["action"] for r in audit_rows(secured)]
    assert rows.count("Sign in failed") == 5 and "Sign in blocked (too many attempts)" in rows


def test_a_disabled_account_cannot_sign_in_or_keep_using_a_session(secured) -> None:
    client = make_client(secured)
    sign_in(client, "olga")
    assert client.get("/").status_code == 200
    secured.state.auth.users.set_disabled(secured.state.auth.users.by_username("olga").id, True)
    assert client.get("/", follow_redirects=False).status_code == 303
    assert sign_in(make_client(secured), "olga").status_code == 401


def test_signing_out_ends_the_session(secured) -> None:
    client = make_client(secured)
    sign_in(client, "alice")
    assert client.get("/config").status_code == 200
    out = client.post("/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert client.get("/config", follow_redirects=False).status_code == 303
    assert "Sign out" in [r["action"] for r in audit_rows(secured)]


def test_a_session_ends_after_idle_time(secured) -> None:
    secured.state.auth.sessions.idle_seconds = 0
    client = make_client(secured)
    sign_in(client, "alice")
    import time

    time.sleep(0.02)
    assert client.get("/config", follow_redirects=False).status_code == 303


# --- roles ---------------------------------------------------------------------


def test_an_operator_uses_the_tools_but_not_the_configuration(secured) -> None:
    client = make_client(secured)
    sign_in(client, "olga")
    assert client.get("/").status_code == 200 and client.get("/tools/dicom-import").status_code == 200
    assert client.get("/router").status_code == 200 and client.get("/lite").status_code == 200
    for path in ("/config", "/config/remotes", "/config/transfer", "/logs", "/logs/download"):
        denied = client.get(path)
        assert denied.status_code == 403 and "Not allowed" in denied.text, path
    assert client.post("/config/transfer/export", data={"section": "remotes"}).status_code == 403
    assert client.post("/router", data={}).status_code == 403  # adding a rule is for administrators
    assert client.post("/logs/clear").status_code == 403
    assert client.get("/api/remotes").status_code == 403
    assert client.get("/api/tools").status_code == 200


def test_a_viewer_sees_only_the_status_dashboard(secured) -> None:
    client = make_client(secured)
    assert sign_in(client, "vera").headers["location"] == "/lite"
    assert client.get("/lite").status_code == 200 and client.get("/lite/api/status").status_code == 200
    assert client.get("/help").status_code == 200 and client.get("/account").status_code == 200
    for path in ("/", "/tools/dicom-import", "/router", "/worklist", "/testbench", "/config"):
        assert client.get(path).status_code == 403, path
    assert client.post("/tools/c-find-advanced/run", data={}).status_code == 403


def test_an_administrator_can_use_everything(secured) -> None:
    client = make_client(secured)
    sign_in(client, "alice")
    for path in ("/", "/config", "/config/remotes", "/config/transfer", "/logs", "/tools/dicom-import", "/router", "/lite"):
        assert client.get(path).status_code == 200, path


def test_denials_are_audited_with_who_tried_what(secured) -> None:
    client = make_client(secured)
    sign_in(client, "olga")
    client.get("/config")
    row = [r for r in audit_rows(secured) if r["action"].startswith("Denied")][-1]
    assert row["user"] == "olga" and row["role"] == "operator" and row["action"] == "Denied: Change configuration" and row["status"] == 403


# --- your own password ---------------------------------------------------------


def test_changing_your_password_ends_your_other_sign_ins(secured) -> None:
    here, elsewhere = make_client(secured), make_client(secured, "10.0.0.8")
    sign_in(here, "olga")
    sign_in(elsewhere, "olga")
    bad = here.post("/account/password", data={"current": "wrong", "new": "a brand new passphrase", "confirm": "a brand new passphrase"})
    assert bad.status_code == 400 and "not right" in bad.text
    assert here.post("/account/password", data={"current": PASSWORD, "new": "short", "confirm": "short"}).status_code == 400
    done = here.post("/account/password", data={"current": PASSWORD, "new": "a brand new passphrase", "confirm": "a brand new passphrase"})
    assert done.status_code == 200 and "password was changed" in done.text
    assert here.get("/", follow_redirects=False).status_code == 200  # this one stays
    assert elsewhere.get("/", follow_redirects=False).status_code == 303  # the other one is signed out
    assert sign_in(make_client(secured), "olga", PASSWORD).status_code == 401
    assert sign_in(make_client(secured), "olga", "a brand new passphrase").status_code == 303


# --- the audit trail and the cache ----------------------------------------------


def test_the_audit_trail_names_the_user_and_holds_no_form_values(secured) -> None:
    client = make_client(secured)
    sign_in(client, "olga")
    client.post("/tools/c-find-advanced/run", data={"key_PatientName": "DOE^JANE", "key_PatientID": "1234567"})
    text = secured.state.audit.path.read_text()
    assert "DOE^JANE" not in text and "1234567" not in text and PASSWORD not in text
    row = [r for r in audit_rows(secured) if r["action"] == "Run an Analytics query"][-1]
    assert row["user"] == "olga" and row["role"] == "operator" and row["ip"] == "10.0.0.7" and row["path"] == "/tools/c-find-advanced/run"
    assert secured.state.audit.verify() == (True, "")


def test_pages_with_patient_data_are_not_cached_by_the_browser(secured) -> None:
    client = make_client(secured)
    sign_in(client, "alice")
    assert client.get("/router").headers["cache-control"] == "no-store"
    assert "no-store" not in client.get("/static/css/app.css").headers.get("cache-control", "")


def test_the_user_and_a_sign_out_button_show_in_the_top_bar(secured) -> None:
    client = make_client(secured)
    sign_in(client, "olga")
    page = client.get("/").text
    assert ">olga</a>" in page and 'action="/logout"' in page and 'href="/config"' not in page.split("topbar-actions")[1].split("</header>")[0]
    sign_in(client, "alice")
    assert 'class="button topbar-config' in client.get("/").text


# --- every route has a deliberate rule -----------------------------------------


def _routes(app):
    def walk(routes, prefix=""):
        for route in routes:
            if hasattr(route, "original_router"):
                yield from walk(route.original_router.routes, prefix + (getattr(route.include_context, "prefix", "") or ""))
            else:
                path = prefix + getattr(route, "path", "")
                for method in getattr(route, "methods", None) or []:
                    if method not in ("HEAD", "OPTIONS"):
                        yield method, re.sub(r"\{[^}]+\}", "x", path)

    return sorted(set(walk(app.router.routes)))


def test_no_route_depends_on_the_default_rule(app) -> None:
    """A new route must be added to auth.POLICY on purpose, not fall through to 'administrators only' by accident."""
    routes = _routes(app)
    assert len(routes) > 80
    unplanned = [f"{m} {p}" for m, p in routes if auth.policy_for(m, p) is auth.DEFAULT_RULE]
    assert not unplanned, f"These routes have no rule in app/auth.py POLICY: {unplanned}"
