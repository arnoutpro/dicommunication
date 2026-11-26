"""Tiles for the /lite dashboard, built from each rule's newest run.

The run history already holds what a tile needs (ok, error, counts, time), so
the dashboard needs no export folder and lists every rule."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.models import RouteRule, RouteRun

STALE_AFTER_INTERVALS = 2.5
DEFAULT_INTERVAL_MINUTES = 5
DAILY_INTERVAL_MINUTES = 25 * 60


def tile_for_rule(rule: RouteRule, run: RouteRun | None, now: datetime | None = None) -> dict:
    """One dashboard tile. ``state`` is ok / error / stale / waiting."""
    now = now or datetime.now(timezone.utc)
    base = {"rule_id": rule.id, "rule_name": rule.name, "rule_status": rule.status}
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
    unit = "exams"
    if rule.is_folder_watch:
        # A folder watch: what was sent and what failed on the last run, and files still waiting.
        unit = rule.watch_unit
        by_status = dict(run.status_counts)
        modalities = {}
    elif rule.status_key:
        by_status = dict(run.status_counts)
        modalities = dict(run.modality_counts)
    else:
        by_status = {"Matched": run.matched_count}
        modalities = {"Matched": run.modality_counts.get("", {})}
    return {
        **base,
        "state": state,
        "checked_at": started.isoformat(),
        "age_seconds": int(age),
        "total": run.matched_count,
        "unit": unit,
        "by_status": by_status,
        "modalities": modalities,
        "error": run.error,
    }


HISTORY_RANGES = (7, 14, 30)


def history_for_rule(rule: RouteRule, daily: dict[str, dict[str, int]], days: int, today: date) -> dict:
    """Exams per day for the last ``days`` days, ending today, stacked by status.

    ``daily`` is what the rule last saw for each study date. A day the rule never saw is
    ``None`` (no data), not ``0``, so a gap in the chart means the rule had no check for it."""
    dates = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    labels: list[str] = []
    for day in dates:
        for label in daily.get(day, {}):
            if label not in labels:
                labels.append(label)
    labels.sort()
    keyed = bool(rule.status_key) or rule.is_folder_watch
    if keyed:
        series = {label: [daily[day].get(label, 0) if day in daily else None for day in dates] for label in labels}
    else:
        series = {"Matched": [sum(daily[day].values()) if day in daily else None for day in dates]}
    totals = [sum(daily[day].values()) if day in daily else None for day in dates]
    return {
        "rule_id": rule.id, "rule_name": rule.name, "keyed": keyed, "series": series, "totals": totals,
        "unit": rule.watch_unit if rule.is_folder_watch else "exams",
    }
