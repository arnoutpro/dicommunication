from __future__ import annotations

import socket
import time

from fastapi.testclient import TestClient
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.models import RemoteNode, RouteRule
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_idle(app, rule_id: str, timeout: float = 5.0) -> None:
    scheduler = app.state.router_scheduler
    deadline = time.time() + timeout
    while scheduler.is_running(rule_id) and time.time() < deadline:
        time.sleep(0.02)


def test_router_page_lists_rules(client: TestClient, store: ConfigStore, remote: RemoteNode) -> None:
    store.add_route_rule(RouteRule(name="Nightly CT", source_remote_id=remote.id, modality="CT"))
    response = client.get("/router")
    assert response.status_code == 200
    assert "Nightly CT" in response.text
    assert "every 15 min" in response.text


def test_add_edit_delete_route_rule(client: TestClient, store: ConfigStore, remote: RemoteNode) -> None:
    response = client.post(
        "/router",
        data={
            "name": "Nightly CT",
            "source_remote_id": remote.id,
            "level": "STUDY",
            "modality": "ct, mr",
            "date_scope": "today",
            "date_last_n_days": "1",
            "schedule_mode": "interval",
            "interval_minutes": "30",
            "daily_times": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/router?saved=rule"

    rules = store.list_route_rules()
    assert len(rules) == 1
    rule = rules[0]
    assert rule.modality == "CT,MR"
    assert rule.interval_minutes == 30
    assert rule.status == "active"

    edit_page = client.get(f"/router?edit={rule.id}")
    assert edit_page.status_code == 200
    assert 'value="Nightly CT"' in edit_page.text

    updated = client.post(
        "/router",
        data={
            "rule_id": rule.id,
            "name": "Nightly CT/MR",
            "source_remote_id": remote.id,
            "level": "STUDY",
            "modality": "CT",
            "date_scope": "today",
            "date_last_n_days": "1",
            "schedule_mode": "daily",
            "interval_minutes": "15",
            "daily_times": "08:00, 20:00",
            "days_of_week": ["0", "2"],
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    reloaded = store.get_route_rule(rule.id)
    assert reloaded.name == "Nightly CT/MR"
    assert reloaded.schedule_mode == "daily"
    assert reloaded.daily_times == ["08:00", "20:00"]
    assert reloaded.days_of_week == [0, 2]

    deleted = client.post(f"/router/{rule.id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert store.list_route_rules() == []


def test_add_route_rule_rejects_missing_source(client: TestClient, store: ConfigStore) -> None:
    response = client.post(
        "/router",
        data={"name": "No source", "source_remote_id": "", "schedule_mode": "interval", "interval_minutes": "15"},
    )
    assert response.status_code == 400
    assert store.list_route_rules() == []


def test_edit_does_not_reset_status(client: TestClient, store: ConfigStore, remote: RemoteNode, app) -> None:
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id))
    app.state.router_scheduler.request_pause(rule.id)
    assert store.get_route_rule(rule.id).status == "paused"

    client.post(
        "/router",
        data={
            "rule_id": rule.id,
            "name": "X renamed",
            "source_remote_id": remote.id,
            "schedule_mode": "interval",
            "interval_minutes": "15",
        },
    )
    assert store.get_route_rule(rule.id).status == "paused"


def test_route_rule_not_found_returns_404(client: TestClient) -> None:
    assert client.post("/router/missing/start").status_code == 404
    assert client.post("/router/missing/pause").status_code == 404
    assert client.post("/router/missing/stop").status_code == 404
    assert client.post("/router/missing/run-now").status_code == 404
    assert client.get("/router/missing/runs").status_code == 404


def test_run_now_runs_in_background_and_history_page_shows_it(
    client: TestClient, store: ConfigStore, app
) -> None:
    port = _free_port()

    def handle_find(event):
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = "1.2.3"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "1001"
        ds.AccessionNumber = "ACC1"
        ds.StudyDate = "20260101"
        ds.ModalitiesInStudy = "CT"
        yield 0xFF00, ds
        yield 0x0000, None

    scp = AE(ae_title="QR_SCP")
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        node = store.add_remote(
            RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        ).remotes[-1]
        rule = store.add_route_rule(
            RouteRule(name="Nightly CT", source_remote_id=node.id, modality="CT", date_scope="all")
        )

        response = client.post(f"/router/{rule.id}/run-now", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/router/{rule.id}/runs?saved=running"

        _wait_until_idle(app, rule.id)

        runs = store.list_route_runs(rule_id=rule.id)
        assert len(runs) == 1
        assert runs[0].matched_count == 1
        assert runs[0].new_count == 1

        history = client.get(f"/router/{rule.id}/runs")
        assert history.status_code == 200
        assert "DOE" in history.text
        assert "ACC1" in history.text

        again = client.post(f"/router/{rule.id}/run-now", follow_redirects=False)
        assert again.status_code == 303
    finally:
        server.shutdown()


def test_start_pause_stop_lifecycle(client: TestClient, store: ConfigStore, remote: RemoteNode, app) -> None:
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id, status="stopped"))

    started = client.post(f"/router/{rule.id}/start", follow_redirects=False)
    assert started.status_code == 303
    assert started.headers["location"] == "/router?saved=started"
    _wait_until_idle(app, rule.id)
    assert store.get_route_rule(rule.id).status == "active"

    paused = client.post(f"/router/{rule.id}/pause", follow_redirects=False)
    assert paused.status_code == 303
    assert paused.headers["location"] == "/router?saved=paused"
    assert store.get_route_rule(rule.id).status == "paused"

    stopped = client.post(f"/router/{rule.id}/stop", follow_redirects=False)
    assert stopped.status_code == 303
    assert stopped.headers["location"] == "/router?saved=stopped"
    assert store.get_route_rule(rule.id).status == "stopped"
    assert store.get_route_rule(rule.id).next_run_at is None

    page = client.get("/router")
    assert "Stopped" in page.text
