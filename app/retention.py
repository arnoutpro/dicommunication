"""Keeping patient details only as long as they are needed.

The Dicom Router's run history keeps counts only (the matched studies with their
names and IDs are never written), and the recent tool results keep the rows they
fetched. After the retention time set under Configuration > Security (30 minutes
unless changed) those rows are emptied and the counts stay. Runs and results older
than the history days (30 unless changed) are deleted. A background sweeper does it
every minute; a setting of 0 minutes keeps no rows from the start.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from app.applog import log
from app.security_settings import SecuritySettingsStore
from app.store import ConfigStore


class RetentionSweeper:
    def __init__(self, store: ConfigStore, settings: SecuritySettingsStore, interval_seconds: float = 60.0) -> None:
        self.store = store
        self.settings = settings
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        store.retention_provider = lambda: settings.load().retention_minutes

    def sweep_once(self, now: datetime | None = None) -> dict[str, int]:
        settings = self.settings.load()
        now = now or datetime.now(timezone.utc)
        delete_before = now - timedelta(days=settings.history_days) if settings.history_days else None
        emptied = self.store.scrub_patient_details(now - timedelta(minutes=settings.retention_minutes), delete_before)
        if any(emptied.values()):
            log.info("Retention: emptied %s run(s) and %s result(s), deleted %s old entr(ies)", emptied["runs"], emptied["results"], emptied["deleted"])  # counts only
        return emptied

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep_once()
            except Exception:  # noqa: BLE001
                log.exception("Retention sweep failed")

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self.sweep_once()  # whatever piled up while the app was not running
        except Exception:  # noqa: BLE001
            log.exception("Retention sweep failed")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="retention-sweeper")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
