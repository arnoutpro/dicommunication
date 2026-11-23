"""Retention of patient details and the limits placed on operators."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import launcher
from app.auth import ADMIN, OPERATOR, UserStore
from app.models import RemoteNode, RouteMatch, RouteRun, ToolResult
from app.retention import RetentionSweeper
from app.security_settings import SecuritySettings, path_is_allowed

PASSWORD = "correct horse battery"
NOW = datetime.now(timezone.utc)


def match(name: str = "DOE^JOHN") -> RouteMatch:
    return RouteMatch(study_instance_uid="1.2.3", patient_name=name, patient_id="P1", accession_number="A1")


def run_at(when: datetime, matches: int = 1) -> RouteRun:
    return RouteRun(rule_id="r1", started_at=when, matched_count=matches, new_count=matches, matches=[match() for _ in range(matches)])


def result_at(when: datetime) -> ToolResult:
    return ToolResult(tool_id="c-find-advanced", tool_name="C-FIND", ok=True, summary="1 record", started_at=when, records=[{"PatientName": "DOE^JOHN"}])


# --- retention -----------------------------------------------------------------


def stored(app, name: str) -> str:
    return getattr(app.state.store, name).read_text()


def test_the_router_history_keeps_counts_only(app) -> None:
    store = app.state.store
    run = RouteRun(rule_id="r1", matched_count=3, new_count=3, matches=[match(), match("ROE^JANE"), match("POE^EDGAR")])
    run.matches[0].status, run.matches[1].status, run.matches[2].status = "forwarded", "failed", "failed"
    run.matches[1].error = run.matches[2].error = "Storage SCP refused"
    returned = store.add_route_run(run)
    assert len(returned.matches) == 3  # still there for the export file while the run is in memory
    on_disk = stored(app, "route_runs_path")
    assert "DOE" not in on_disk and "ROE" not in on_disk and "P1" not in on_disk and "1.2.3" not in on_disk
    saved = store.list_route_runs()[0]
    assert saved.matches == [] and saved.matched_count == 3
    assert saved.outcome_counts == {"forwarded": 1, "failed": 2} and saved.error_counts == {"Storage SCP refused": 2}


def test_old_tool_rows_are_emptied_and_the_summaries_stay(app) -> None:
    store = app.state.store
    store.add_result(result_at(NOW - timedelta(minutes=45)))
    store.add_result(result_at(NOW))
    assert app.state.retention.sweep_once()["results"] == 1
    results = store.list_results()
    assert [len(r.records) for r in results] == [1, 0] and results[1].summary == "1 record"  # newest first
    assert app.state.retention.sweep_once()["results"] == 0  # nothing left to do


def test_a_history_file_from_0_11_0_loses_its_patient_rows_at_the_next_sweep(app) -> None:
    legacy = [{"id": "x", "rule_id": "r1", "started_at": NOW.isoformat(), "matched_count": 2, "new_count": 2,
               "matches": [{"study_instance_uid": "1.2.3", "patient_name": "DOE^JOHN", "patient_id": "P1"}]}]
    app.state.store.route_runs_path.write_text(json.dumps(legacy))
    assert app.state.retention.sweep_once()["runs"] == 1
    assert "DOE" not in stored(app, "route_runs_path") and app.state.store.list_route_runs()[0].matched_count == 2


def test_runs_and_results_are_deleted_after_the_history_days(app) -> None:
    store = app.state.store
    store.add_route_run(run_at(NOW - timedelta(days=40)))
    store.add_route_run(run_at(NOW - timedelta(days=2)))
    store.add_result(result_at(NOW - timedelta(days=40)))
    store.add_result(result_at(NOW))
    assert app.state.retention.sweep_once()["deleted"] == 2
    assert len(store.list_route_runs()) == 1 and len(store.list_results()) == 1
    app.state.security_settings.save(SecuritySettings(history_days=1))
    assert app.state.retention.sweep_once()["deleted"] == 1 and store.list_route_runs() == []
    store.add_route_run(run_at(NOW - timedelta(days=900)))
    app.state.security_settings.save(SecuritySettings(history_days=0))  # 0 = keep until the fixed limits
    assert app.state.retention.sweep_once()["deleted"] == 0 and len(store.list_route_runs()) == 1


def test_retention_follows_the_setting(app) -> None:
    app.state.security_settings.save(SecuritySettings(retention_minutes=120, import_roots=[]))
    app.state.store.add_result(result_at(NOW - timedelta(minutes=45)))
    assert app.state.retention.sweep_once()["results"] == 0
    app.state.security_settings.save(SecuritySettings(retention_minutes=10, import_roots=[]))
    assert app.state.retention.sweep_once()["results"] == 1


def test_keep_nothing_never_stores_tool_rows(app) -> None:
    app.state.security_settings.save(SecuritySettings(retention_minutes=0, import_roots=[]))
    app.state.store.add_route_run(run_at(NOW, 3))
    app.state.store.add_result(result_at(NOW))
    run, result = app.state.store.list_route_runs()[0], app.state.store.list_results()[0]
    assert run.matches == [] and run.matched_count == 3 and result.records == [] and result.summary == "1 record"
    assert "DOE" not in stored(app, "route_runs_path") + stored(app, "results_path")


def test_saving_the_settings_sweeps_at_once_and_the_echo_board_still_works(app, client: TestClient, remote: RemoteNode) -> None:
    app.state.store.add_result(ToolResult(tool_id="c-echo", tool_name="C-ECHO", ok=True, summary="ok", remote_id=remote.id, started_at=NOW - timedelta(days=3)))
    app.state.store.add_result(result_at(NOW - timedelta(minutes=45)))
    assert client.post("/config/security", data={"retention_minutes": "10", "history_days": "1", "import_roots": ""}).status_code == 200
    assert [len(r.records) for r in app.state.store.list_results()] == [0]  # the 3-day-old echo is gone, the other lost its rows
    assert client.get("/echo-board").status_code == 200


def test_the_sweeper_runs_on_its_own_and_stops(app) -> None:
    app.state.store.add_result(result_at(NOW - timedelta(minutes=45)))
    sweeper = RetentionSweeper(app.state.store, app.state.security_settings, interval_seconds=0.05)
    sweeper.start()
    try:
        assert app.state.store.list_results()[0].records == []  # start() sweeps what piled up while the app was off
        app.state.store.add_result(result_at(NOW - timedelta(minutes=46)))
        for _ in range(100):
            if not app.state.store.list_results()[0].records:
                break
            import time

            time.sleep(0.05)
        assert app.state.store.list_results()[0].records == []
    finally:
        sweeper.stop()
    assert sweeper._thread is None
    sweeper.stop()  # stopping twice is harmless


# --- path_is_allowed -----------------------------------------------------------


def test_only_folders_inside_the_allowed_roots_pass(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    (root / "case1").mkdir(parents=True)
    outside = tmp_path / "secrets"
    outside.mkdir()
    assert path_is_allowed(str(root), [str(root)]) and path_is_allowed(str(root / "case1"), [str(root)])
    assert not path_is_allowed(str(outside), [str(root)]) and not path_is_allowed(str(root), [])
    assert not path_is_allowed(str(root / ".." / "secrets"), [str(root)])
    assert not path_is_allowed(str(tmp_path / "imports-evil"), [str(root)])  # a sibling that shares the prefix
    link = root / "shortcut"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    assert not path_is_allowed(str(link), [str(root)])  # a link out of the folder does not count


# --- operator limits -----------------------------------------------------------


@pytest.fixture
def operator(app, remote: RemoteNode, tmp_path: Path):
    app.state.auth.users.add("alice", PASSWORD, ADMIN)
    app.state.auth.users.add("olga", PASSWORD, OPERATOR)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    app.state.security_settings.save(SecuritySettings(retention_minutes=30, import_roots=[str(allowed)]))
    clients = {}
    for name in ("alice", "olga"):
        client = TestClient(app, client=("10.0.0.7", 50000))
        assert client.post("/login", data={"username": name, "password": PASSWORD}, follow_redirects=False).status_code == 303
        clients[name] = client
    return clients["olga"], clients["alice"], allowed


def test_an_operator_cannot_echo_an_address_that_is_not_a_saved_node(operator) -> None:
    olga, alice, _ = operator
    form = {"remote_mode": "manual", "manual_ae_title": "ANY", "manual_host": "192.0.2.1", "manual_port": "104"}
    denied = olga.post("/tools/c-echo/run", data=form)
    assert denied.status_code == 403 and "Only an administrator" in denied.text
    assert alice.post("/tools/c-echo/run", data=form).status_code != 403


def test_an_operator_cannot_send_hl7_to_an_unknown_host(operator, remote: RemoteNode) -> None:
    olga, alice, _ = operator
    assert olga.post("/tools/hl7-send/run", data={"host": "192.0.2.1", "port": "2575", "message": "MSH|^~\\&|"}).status_code == 403
    assert olga.post("/tools/hl7-send/run", data={"host": "127.0.0.1", "port": "99999", "message": "MSH|^~\\&|"}).status_code == 403  # right host, wrong port
    assert olga.post("/tools/hl7-send/run", data={"host": "127.0.0.1", "port": str(remote.port), "message": "MSH|^~\\&|"}).status_code != 403
    assert alice.post("/tools/hl7-send/run", data={"host": "192.0.2.1", "port": "2575", "message": "MSH|^~\\&|"}).status_code != 403


def test_operators_may_only_read_the_listed_folders_for_dicom_import(operator, tmp_path: Path) -> None:
    olga, alice, allowed = operator
    other = tmp_path / "other"
    other.mkdir()
    denied = olga.post("/tools/dicom-import/scan", data={"folder": str(other)})
    assert denied.status_code == 403 and "not on the list of folders" in denied.text
    assert olga.post("/tools/dicom-import/scan", data={"folder": str(allowed)}, follow_redirects=False).status_code == 303
    assert alice.post("/tools/dicom-import/scan", data={"folder": str(other)}, follow_redirects=False).status_code == 303


def test_operators_cannot_aim_the_pdf_or_anonymizer_tools_at_other_folders(operator, tmp_path: Path, remote: RemoteNode) -> None:
    olga, _alice, allowed = operator
    other = tmp_path / "other"
    other.mkdir()
    pdf = olga.post("/tools/pdf-store/run", data={"remote_id": remote.id, "directory": str(other)})
    assert pdf.status_code == 403 and "not on the list of folders" in pdf.text
    from app.routes._shared import _check_operator_limits

    request = type("R", (), {"state": type("S", (), {"role": OPERATOR})(), "app": olga.app})()
    with pytest.raises(Exception) as caught:
        _check_operator_limits(request, "anonymize", {"output_dir": str(other)}, None)
    assert getattr(caught.value, "status_code", None) == 403
    _check_operator_limits(request, "anonymize", {"output_dir": str(allowed)}, None)
    _check_operator_limits(request, "anonymize", {}, None)  # no folder given: nothing to check


def test_without_a_login_nothing_is_limited(app, client: TestClient, tmp_path: Path) -> None:
    assert client.post("/tools/dicom-import/scan", data={"folder": str(tmp_path)}, follow_redirects=False).status_code == 303


# --- launcher ------------------------------------------------------------------


def test_create_admin_from_a_password_file_creates_then_resets(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.store.DEFAULT_DATA_DIR", tmp_path / "data")
    password = tmp_path / "pw.txt"
    password.write_text(PASSWORD + "\nignored second line\n", encoding="utf-8")
    assert launcher.create_admin_from_file("root", str(password)) == 0
    users = UserStore(tmp_path / "data")
    root = users.by_username("root")
    assert root.role == ADMIN and "Created" in capsys.readouterr().out
    users.add("second", PASSWORD, ADMIN)  # the store never lets the last active administrator be disabled
    users.set_disabled(root.id, True)
    password.write_text("a brand new passphrase\n", encoding="utf-8")
    assert launcher.create_admin_from_file("ROOT", str(password)) == 0
    again = UserStore(tmp_path / "data").by_username("root")
    assert not again.disabled and again.role == ADMIN and "Reset" in capsys.readouterr().out
    from app.auth import verify_password

    assert verify_password("a brand new passphrase", again.password_hash)


def test_create_admin_reports_problems_without_a_traceback(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.store.DEFAULT_DATA_DIR", tmp_path / "data")
    assert launcher.create_admin_from_file("root", str(tmp_path / "missing.txt")) == 2
    assert launcher.create_admin_from_file("root", None) == 1  # no file: an empty password, which the rules refuse
    weak = tmp_path / "weak.txt"
    weak.write_text("short\n", encoding="utf-8")
    assert launcher.create_admin_from_file("root", str(weak)) == 1 and "Not done" in capsys.readouterr().out


def test_prepare_tls_makes_a_certificate_only_for_https(app) -> None:
    assert launcher._prepare_tls(app, False) == {} and app.state.tls_info == {"enabled": False}
    kwargs = launcher._prepare_tls(app, True)
    assert Path(kwargs["ssl_certfile"]).is_file() and Path(kwargs["ssl_keyfile"]).is_file()
    assert app.state.tls_info["enabled"] and app.state.tls_info["fingerprint"]
    if os.name == "posix":
        assert (Path(kwargs["ssl_keyfile"]).stat().st_mode & 0o077) == 0  # the private key is for the owner only


def test_server_is_up_answers_for_https_with_a_self_signed_certificate(tmp_path: Path) -> None:
    import socket
    import threading
    import time

    import uvicorn

    from app.main import create_app
    from app.store import ConfigStore

    app = create_app(ConfigStore(tmp_path))
    files = launcher._prepare_tls(app, True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", **files))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert launcher.server_is_up(f"https://127.0.0.1:{port}/health", timeout=2)
        assert not launcher.server_is_up(f"http://127.0.0.1:{port}/health", timeout=1)  # plain HTTP to an HTTPS port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_the_installer_limits_the_firewall_to_the_local_subnet_and_opens_https_shortcuts() -> None:
    wxs = (Path(__file__).resolve().parent.parent / "packaging" / "windows" / "Headless.wxs").read_text(encoding="utf-8")
    assert 'Scope="localSubnet"' in wxs and 'Scope="any"' not in wxs
    assert "[SHORTCUTSCHEME]://localhost:[PORT]/" in wxs and 'Property Id="SHORTCUTSCHEME"' in wxs
