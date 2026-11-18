"""Tiles for the /lite dashboard, built from each rule's newest run.

The run history already holds what a tile needs (ok, error, counts, time), so
the dashboard needs no export folder and lists every rule."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import RouteRule, RouteRun

STALE_AFTER_INTERVALS = 2.5
DEFAULT_INTERVAL_MINUTES = 5
DAILY_INTERVAL_MINUTES = 25 * 60


def tile_for_rule(rule: RouteRule, run: RouteRun | None, now: datetime | None = None) -> dict:
    """One dashboard tile. ``state`` is ok / error / stale / waiting."""
    now = now or datetime.now(timezone.utc)
    base = {"rule_name": rule.name, "rule_status": rule.status}
    if run is None:
        return {**base, "state": "waiting"}
    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
    if rule.schedule_mode == "interval":
        interval = rule.interval_minutes
    else:
        interval = DAILY_INTERVAL_MINUTES
    age = (now - started).total_seconds()
    if age > interval * 60 * STALE_AFTER_INTERVALS:
        state = "stale"
    else:
        state = "ok" if run.ok else "error"
    if rule.status_key:
        by_status = dict(run.status_counts)
    else:
        by_status = {"Matched": run.matched_count}
    return {
        **base,
        "state": state,
        "checked_at": started.isoformat(),
        "age_seconds": int(age),
        "total": run.matched_count,
        "by_status": by_status,
        "error": run.error,
    }
