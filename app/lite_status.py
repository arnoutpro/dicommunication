"""Read the status files a Router rule writes (see router_scheduler.write_status_snapshot)
for the /lite dashboard. Nothing is stored; every request re-reads the files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STALE_AFTER_INTERVALS = 2.5
DEFAULT_INTERVAL_MINUTES = 5
DAILY_INTERVAL_MINUTES = 25 * 60


def read_status_file(path: Path, rule_name: str, rule_status: str = "active",
                     daily: bool = False, now: datetime | None = None) -> dict:
    """One dashboard tile. ``state`` is ok / error / stale / waiting / unreadable."""
    now = now or datetime.now(timezone.utc)
    base = {"rule_name": rule_name, "rule_status": rule_status}
    if not path.is_file():
        return {**base, "state": "waiting"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(data["checked_at"])
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return {**base, "state": "unreadable"}
    interval = data.get("interval_minutes") or (DAILY_INTERVAL_MINUTES if daily else DEFAULT_INTERVAL_MINUTES)
    age = (now - checked).total_seconds()
    if age > interval * 60 * STALE_AFTER_INTERVALS:
        state = "stale"
    else:
        state = "ok" if data.get("ok") else "error"
    return {
        **base,
        "state": state,
        "checked_at": data["checked_at"],
        "age_seconds": int(age),
        "total": data.get("total", 0),
        "by_status": data.get("by_status", {}),
        "error": data.get("error", ""),
    }
