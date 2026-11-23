"""Keeping patient details only as long as they are needed.

The Dicom Router's run history lists each matched study with the patient's name,
ID and accession number, and the recent tool results keep the rows they fetched.
The status dashboard needs only the counts. So after the retention time set under
Configuration > Security (30 minutes unless changed) those lists are emptied and
the counts stay. A background sweeper does it every minute; a setting of 0 keeps
nothing from the start.
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
        minutes = self.settings.load().retention_minutes
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
        emptied = self.store.scrub_patient_details(cutoff)
        if any(emptied.values()):
            log.info("Retention: emptied patient details of %s run(s) and %s result(s)", emptied["runs"], emptied["results"])  # counts only
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
