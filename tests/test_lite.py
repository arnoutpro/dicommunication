import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app_lite.main import create_app, read_statuses

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _write(folder, name, checked, **extra):
    data = {"rule_name": name, "checked_at": checked.isoformat(), "interval_minutes": 1,
            "ok": True, "total": 2, "by_status": {"UNREAD": 2}, **extra}
    (folder / f"{name}.status.json").write_text(json.dumps(data))


def test_states(tmp_path):
    _write(tmp_path, "fresh", NOW - timedelta(seconds=30))
    _write(tmp_path, "old", NOW - timedelta(minutes=10))
    _write(tmp_path, "failed", NOW - timedelta(seconds=30), ok=False, error="boom")
    (tmp_path / "bad.status.json").write_text("{nope")
    states = {t["rule_name"]: t["state"] for t in read_statuses(tmp_path, NOW)}
    assert states == {"fresh": "ok", "old": "stale", "failed": "error", "bad": "unreadable"}


def test_endpoints(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/").status_code == 200
    assert client.get("/api/status").json()["tiles"] == []
