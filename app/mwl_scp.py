"""Optional local MWL SCP and Structured Report C-STORE on the listen port."""

from __future__ import annotations

import threading

from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind, Verification

from app.applog import log
from app.mwl import entry_to_dataset, matches_entry, query_from_identifier
from app.storage_sop_classes import ALL_STORAGE_SOP_CLASSES
from app.store import ConfigStore


class StorageInbox:
    """Collect C-STORE datasets for one in-flight C-MOVE retrieve."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capturing = False
        self._datasets: list = []

    def begin(self) -> None:
        with self._lock:
            self._datasets = []
            self._capturing = True

    def add(self, dataset) -> None:
        with self._lock:
            if not self._capturing:
                return
            try:
                self._datasets.append(dataset.copy())
            except Exception:  # noqa: BLE001
                self._datasets.append(dataset)

    def finish(self) -> list:
        with self._lock:
            self._capturing = False
            items = list(self._datasets)
            self._datasets = []
            return items


STORAGE_INBOX = StorageInbox()


class WorklistSCP:
    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self.server = None
        self.last_error: str | None = None

    def start(self) -> None:
        self.stop()
        config = self.store.load()
        mwl = config.local.mwl_scp_enabled
        storage_enabled = config.local.storage_scp_enabled
        if not mwl and not storage_enabled:
            return
        bind_host = "0.0.0.0" if config.local.host in {"0.0.0.0", ""} else config.local.host
        try:
            ae = AE(ae_title=config.local.ae_title)
            ae.add_supported_context(Verification)
            handlers = [(evt.EVT_C_ECHO, lambda event: 0x0000)]
            if mwl:
                ae.add_supported_context(ModalityWorklistInformationFind)
                handlers.append((evt.EVT_C_FIND, self._on_find))
            if storage_enabled:
                for sop in ALL_STORAGE_SOP_CLASSES:
                    ae.add_supported_context(sop)
                handlers.append((evt.EVT_C_STORE, self._on_store))
            self.server = ae.start_server(
                (bind_host, config.local.port),
                block=False,
                evt_handlers=handlers,
            )
            self.last_error = None
            log.info(
                "Local DICOM SCP listening on %s:%s as %s (MWL %s, C-STORE %s)",
                bind_host,
                config.local.port,
                config.local.ae_title,
                "on" if mwl else "off",
                "on" if storage_enabled else "off",
            )
        except OSError as exc:
            self.server = None
            self.last_error = f"Could not start local DICOM SCP on {bind_host}:{config.local.port}: {exc}"
            log.warning("%s", self.last_error)

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.server = None
            log.info("Local DICOM SCP stopped")

    def restart(self) -> None:
        self.start()

    @property
    def running(self) -> bool:
        return self.server is not None

    def _on_find(self, event):
        query = query_from_identifier(event.identifier)
        for entry in self.store.list_worklist():
            if matches_entry(entry, query):
                yield 0xFF00, entry_to_dataset(entry)
        yield 0x0000, None

    def _on_store(self, event):
        try:
            dataset = event.dataset
            dataset.file_meta = event.file_meta
            STORAGE_INBOX.add(dataset)
            return 0x0000
        except Exception as exc:  # noqa: BLE001
            log.warning("C-STORE failed: %s", exc)
            return 0xC211
