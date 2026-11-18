from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.lite_status import read_status_file
from app.models import RemoteNode, RouteRule
from app.router_scheduler import status_file_name
from app.store import ConfigStore

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _write(path, checked, **extra):
    data = {"rule_name": "r", "checked_at": checked.isoformat(), "interval_minutes": 1,
            "ok": True, "total": 2, "by_status": {"UNREAD": 2}, **extra}
    path.write_text(json.dumps(data))


def test_tile_states(tmp_path):
    fresh, old, failed, bad = (tmp_path / n for n in ("a", "b", "c", "d"))
    _write(fresh, NOW - timedelta(seconds=30))
    _write(old, NOW - timedelta(minutes=10))
    _write(failed, NOW - timedelta(seconds=30), ok=False, error="boom")
    bad.write_text("{nope")
    state = lambda p: read_status_file(p, "r", now=NOW)["state"]  # noqa: E731
    assert [state(p) for p in (fresh, old, failed, bad, tmp_path / "missing")] == [
        "ok", "stale", "error", "unreadable", "waiting"]


def test_daily_rule_is_not_stale_after_a_few_minutes(tmp_path):
    path = tmp_path / "a"
    _write(path, NOW - timedelta(hours=3), interval_minutes=None)
    assert read_status_file(path, "r", daily=True, now=NOW)["state"] == "ok"
    assert read_status_file(path, "r", daily=False, now=NOW)["state"] == "stale"


def test_lite_page_and_api(client: TestClient, store: ConfigStore, remote: RemoteNode, tmp_path):
    page = client.get("/lite")
    assert page.status_code == 200
    assert "<script>" not in page.text  # the app's CSP only allows scripts from /static
    assert client.get("/lite/api/status").json() == {"tiles": []}

    rule = store.add_route_rule(
        RouteRule(name="Pending Final", source_remote_id=remote.id, export_dir=str(tmp_path),
                  status_key="TamarStudyStatus"))
    store.add_route_rule(RouteRule(name="No status key", source_remote_id=remote.id, export_dir=str(tmp_path)))
    (tiles,) = client.get("/lite/api/status").json()["tiles"]
    assert tiles["rule_name"] == "Pending Final" and tiles["state"] == "waiting"

    _write(tmp_path / status_file_name(rule), datetime.now(timezone.utc), by_status={"PENDING FINAL": 2})
    (tiles,) = client.get("/lite/api/status").json()["tiles"]
    assert tiles["state"] == "ok" and tiles["by_status"] == {"PENDING FINAL": 2}
