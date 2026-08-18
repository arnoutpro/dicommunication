"""Optional local Modality Worklist SCP backed by the web worklist."""

from __future__ import annotations

from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind, Verification

from app.mwl import entry_to_dataset, matches_entry, query_from_identifier
from app.store import ConfigStore


class WorklistSCP:
    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self.server = None
        self.last_error: str | None = None

    def start(self) -> None:
        self.stop()
        config = self.store.load()
        if not config.local.mwl_scp_enabled:
            return
        bind_host = "0.0.0.0" if config.local.host in {"0.0.0.0", ""} else config.local.host
        try:
            ae = AE(ae_title=config.local.ae_title)
            ae.add_supported_context(Verification)
            ae.add_supported_context(ModalityWorklistInformationFind)
            self.server = ae.start_server(
                (bind_host, config.local.port),
                block=False,
                evt_handlers=[
                    (evt.EVT_C_ECHO, lambda event: 0x0000),
                    (evt.EVT_C_FIND, self._on_find),
                ],
            )
            self.last_error = None
        except OSError as exc:
            self.server = None
            self.last_error = f"Could not start MWL SCP on {bind_host}:{config.local.port}: {exc}"

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.server = None

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
