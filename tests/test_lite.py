from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.lite_status import tile_for_rule
from app.models import RemoteNode, RouteRule, RouteRun
from app.store import ConfigStore

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _rule(**kw) -> RouteRule:
    return RouteRule(name="r", source_remote_id="x", interval_minutes=1, **kw)


def _run(age_seconds: int = 30, **kw) -> RouteRun:
    return RouteRun(rule_id="x", started_at=NOW - timedelta(seconds=age_seconds), **kw)


def test_tile_states():
    assert tile_for_rule(_rule(), None, NOW)["state"] == "waiting"
    assert tile_for_rule(_rule(), _run(), NOW)["state"] == "ok"
    assert tile_for_rule(_rule(), _run(ok=False, error="boom"), NOW)["state"] == "error"
    assert tile_for_rule(_rule(), _run(age_seconds=600), NOW)["state"] == "stale"


def test_rule_without_status_key_shows_matched_count():
    tile = tile_for_rule(_rule(), _run(matched_count=3), NOW)
    assert tile["by_status"] == {"Matched": 3} and tile["total"] == 3


def test_rule_with_status_key_shows_status_counts():
    tile = tile_for_rule(_rule(status_key="TamarStudyStatus"), _run(matched_count=2, status_counts={"PENDING FINAL": 2}), NOW)
    assert tile["by_status"] == {"PENDING FINAL": 2}


def test_tiles_carry_the_modality_breakdown():
    keyed = tile_for_rule(
        _rule(status_key="TamarStudyStatus"),
        _run(matched_count=2, status_counts={"PENDING FINAL": 2}, modality_counts={"PENDING FINAL": {"CT": 1, "MR": 1}}),
        NOW,
    )
    assert keyed["modalities"] == {"PENDING FINAL": {"CT": 1, "MR": 1}}
    plain = tile_for_rule(_rule(), _run(matched_count=3, modality_counts={"": {"DX+SR": 3}}), NOW)
    assert plain["modalities"] == {"Matched": {"DX+SR": 3}}


def test_daily_rule_is_not_stale_after_a_few_hours():
    rule = RouteRule(name="r", source_remote_id="x", schedule_mode="daily", daily_times=["08:00"])
    assert tile_for_rule(rule, _run(age_seconds=3 * 3600), NOW)["state"] == "ok"


def test_lite_page_and_api_list_every_rule(client: TestClient, store: ConfigStore, remote: RemoteNode):
    page = client.get("/lite")
    assert page.status_code == 200
    assert "<script>" not in page.text  # the app's CSP only allows scripts from /static
    assert client.get("/lite/api/status").json() == {"tiles": []}

    plain = store.add_route_rule(RouteRule(name="Plain", source_remote_id=remote.id))
    keyed = store.add_route_rule(RouteRule(name="Keyed", source_remote_id=remote.id, status_key="TamarStudyStatus"))
    tiles = {t["rule_name"]: t for t in client.get("/lite/api/status").json()["tiles"]}
    assert set(tiles) == {"Plain", "Keyed"} and tiles["Plain"]["state"] == "waiting"

    store.add_route_run(RouteRun(rule_id=plain.id, matched_count=3))
    store.add_route_run(RouteRun(rule_id=keyed.id, matched_count=2, status_counts={"PENDING FINAL": 2}))
    tiles = {t["rule_name"]: t for t in client.get("/lite/api/status").json()["tiles"]}
    assert tiles["Plain"]["state"] == "ok" and tiles["Plain"]["by_status"] == {"Matched": 3}
    assert tiles["Keyed"]["by_status"] == {"PENDING FINAL": 2}
