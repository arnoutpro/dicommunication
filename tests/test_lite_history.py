from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.lite_status import history_for_rule
from app.models import RouteRule, RouteRun
from app.router_scheduler import _iso_date
from app.store import ConfigStore

T0 = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _run(rule_id: str, days: dict, **kw) -> RouteRun:
    return RouteRun(rule_id=rule_id, started_at=T0, day_counts=days, **kw)


def test_iso_date_accepts_dicom_and_iso_and_rejects_the_rest() -> None:
    assert _iso_date("20260315") == "2026-03-15"
    assert _iso_date("2026-03-15") == "2026-03-15"
    assert _iso_date("") == "" and _iso_date("2026") == "" and _iso_date("20261340") == ""


def test_the_newest_count_for_a_day_wins_and_other_days_stay(store: ConfigStore) -> None:
    store.add_route_run(_run("r1", {"2026-03-14": {"": 5}, "2026-03-15": {"": 1}}))
    store.add_route_run(_run("r1", {"2026-03-15": {"": 4}}))
    assert store.route_daily("r1") == {"2026-03-14": {"": 5}, "2026-03-15": {"": 4}}


def test_a_failed_or_interrupted_run_does_not_change_the_days(store: ConfigStore) -> None:
    store.add_route_run(_run("r1", {"2026-03-15": {"": 4}}))
    store.add_route_run(_run("r1", {"2026-03-15": {"": 0}}, ok=False, error="down"))
    store.add_route_run(_run("r1", {"2026-03-15": {"": 1}}, status="interrupted"))
    assert store.route_daily("r1") == {"2026-03-15": {"": 4}}


def test_days_outlive_the_500_run_history(store: ConfigStore) -> None:
    store.add_route_run(_run("r1", {"2026-03-01": {"": 7}}))
    for _ in range(store._max_route_runs + 5):
        store.add_route_run(_run("r1", {}))
    assert store.route_daily("r1") == {"2026-03-01": {"": 7}}


def test_deleting_a_rule_removes_its_days_and_retention_prunes_old_ones(store: ConfigStore) -> None:
    rule = store.add_route_rule(RouteRule(name="r", source_remote_id="x"))
    store.add_route_run(_run(rule.id, {"2026-01-01": {"": 1}, "2026-03-10": {"": 2}}))
    store.scrub_patient_details(T0, delete_before=datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert store.route_daily(rule.id) == {"2026-03-10": {"": 2}}
    store.delete_route_rule(rule.id)
    assert store.route_daily(rule.id) == {}


def test_a_corrupt_daily_file_is_set_aside_not_fatal(store: ConfigStore) -> None:
    store.route_daily_path.write_text("{not json")
    assert store.route_daily("r1") == {}
    store.add_route_run(_run("r1", {"2026-03-15": {"": 1}}))
    assert store.route_daily("r1") == {"2026-03-15": {"": 1}}


def test_history_has_a_gap_for_a_day_with_no_data_and_stacks_statuses() -> None:
    today = date(2026, 3, 15)
    plain = RouteRule(name="p", source_remote_id="x")
    daily = {"2026-03-14": {"": 3}, "2026-03-15": {"": 5}}
    h = history_for_rule(plain, daily, 7, today)
    assert h["totals"] == [None] * 5 + [3, 5]
    assert h["series"] == {"Matched": [None] * 5 + [3, 5]} and h["keyed"] is False
    keyed = RouteRule(name="k", source_remote_id="x", status_key="TamarStudyStatus")
    h = history_for_rule(keyed, {"2026-03-15": {"UNREAD": 2, "DONE": 1}, "2026-03-14": {"UNREAD": 1}}, 7, today)
    assert h["series"]["UNREAD"][-2:] == [1, 2] and h["series"]["DONE"][-2:] == [0, 1]
    assert h["totals"][-2:] == [1, 3] and h["keyed"] is True


def test_history_api_returns_the_days_and_only_known_ranges(client: TestClient, store: ConfigStore) -> None:
    rule = store.add_route_rule(RouteRule(name="Open", source_remote_id="x"))
    today = date.today()
    store.add_route_run(RouteRun(rule_id=rule.id, started_at=T0, day_counts={today.isoformat(): {"": 4}}))
    body = client.get("/lite/api/history").json()
    assert len(body["days"]) == 7 and body["days"][-1] == today.isoformat() and body["ranges"] == [7, 14, 30]
    assert body["rules"][0]["rule_id"] == rule.id and body["rules"][0]["totals"][-1] == 4
    assert len(client.get("/lite/api/history?days=30").json()["days"]) == 30
    assert len(client.get("/lite/api/history?days=9999").json()["days"]) == 7  # unknown range falls back
    assert body["days"][0] == (today - timedelta(days=6)).isoformat()


def test_lite_page_has_the_range_buttons_and_no_inline_script(client: TestClient) -> None:
    page = client.get("/lite").text
    assert 'id="range"' in page and "<script>" not in page
    js = client.get("/static/js/lite.js").text
    assert "/lite/api/history" in js
