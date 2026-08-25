"""Atomic JSON persistence for local/remote config and recent tool results."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.models import (
    AppConfig,
    Hl7Message,
    LoggingSettings,
    RemoteNode,
    ToolResult,
    VirtualAE,
    WorklistEntry,
    new_record_id,
)
from app.paths import runtime_os_name

def _windows_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "dicommunication"


def _default_data_dir() -> Path:
    env = os.environ.get("DICOMM_DATA_DIR")
    if env:
        return Path(env)
    if runtime_os_name() == "nt":
        return _windows_data_dir()
    home_dir = Path.home() / ".dicommunication"
    legacy = Path("data")
    if not (home_dir / "config.json").exists() and (legacy / "config.json").exists():
        return legacy
    return home_dir


DEFAULT_DATA_DIR = _default_data_dir()

ModelT = TypeVar("ModelT", bound=BaseModel)


def _ensure_unique_id(record, existing: list) -> None:
    """Give ``record`` a fresh id if one already in the store uses it.

    Ids address a single record: two rows sharing one means a delete removes
    both and an edit rewrites the wrong one.
    """
    taken = {item.id for item in existing}
    while record.id in taken:
        record.id = new_record_id()


def _parse_all(model: type[ModelT], raw: list) -> list[ModelT]:
    """Validate stored records, dropping any the current schema rejects.

    One unreadable record from an older build or a hand-edit should cost the
    operator that record, not the whole worklist / result history / draft list.
    """
    parsed: list[ModelT] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            parsed.append(model.model_validate(item))
        except ValidationError:
            dropped += 1
    if dropped:
        from app.applog import log

        log.warning("Skipped %s unreadable %s record(s).", dropped, model.__name__)
    return parsed


class ConfigStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.json"
        self.results_path = self.data_dir / "results.json"
        self.worklist_path = self.data_dir / "worklist.json"
        self.hl7_path = self.data_dir / "hl7_messages.json"
        self._lock = Lock()
        self._max_results = 200

    def load(self) -> AppConfig:
        with self._lock:
            return self._load_unlocked()

    def save(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def save_local(self, local) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            config.local = local
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def save_logging(self, settings: LoggingSettings) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            config.logging = settings
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def add_remote(self, remote: RemoteNode) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            _ensure_unique_id(remote, config.remotes)
            config.remotes.append(remote)
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def update_remote(self, remote_id: str, remote: RemoteNode) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            updated: list[RemoteNode] = []
            found = False
            for existing in config.remotes:
                if existing.id == remote_id:
                    payload = remote.model_dump()
                    payload["id"] = remote_id
                    payload["created_at"] = existing.created_at
                    updated.append(RemoteNode.model_validate(payload))
                    found = True
                else:
                    updated.append(existing)
            if not found:
                raise KeyError(remote_id)
            config.remotes = updated
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def delete_remote(self, remote_id: str) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            config.remotes = [item for item in config.remotes if item.id != remote_id]
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def add_identity(self, identity: VirtualAE) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            _ensure_unique_id(identity, config.identities)
            config.identities.append(identity)
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def update_identity(self, identity_id: str, identity: VirtualAE) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            updated: list[VirtualAE] = []
            found = False
            for existing in config.identities:
                if existing.id == identity_id:
                    payload = identity.model_dump()
                    payload["id"] = identity_id
                    updated.append(VirtualAE.model_validate(payload))
                    found = True
                else:
                    updated.append(existing)
            if not found:
                raise KeyError(identity_id)
            config.identities = updated
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def delete_identity(self, identity_id: str) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
            config.identities = [item for item in config.identities if item.id != identity_id]
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def add_result(self, result: ToolResult) -> None:
        self.add_results([result])

    def add_results(self, new_results: list[ToolResult]) -> None:
        if not new_results:
            return
        with self._lock:
            results = self._load_results_unlocked()
            payload = [item.model_dump(mode="json") for item in new_results]
            self._write_json(self.results_path, (payload + results)[: self._max_results])

    def list_results(self, limit: int = 20) -> list[ToolResult]:
        with self._lock:
            raw = self._load_results_unlocked()
        return _parse_all(ToolResult, raw[:limit])

    def list_worklist(self) -> list[WorklistEntry]:
        with self._lock:
            return _parse_all(WorklistEntry, self._load_worklist_unlocked())

    def save_worklist(self, entries: list[WorklistEntry]) -> list[WorklistEntry]:
        with self._lock:
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])
            return entries

    def add_worklist_entry(self, entry: WorklistEntry) -> WorklistEntry:
        with self._lock:
            entries = _parse_all(WorklistEntry, self._load_worklist_unlocked())
            _ensure_unique_id(entry, entries)
            entries.insert(0, entry)
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])
            return entry

    def delete_worklist_entry(self, entry_id: str) -> None:
        with self._lock:
            entries = [
                entry
                for entry in _parse_all(WorklistEntry, self._load_worklist_unlocked())
                if entry.id != entry_id
            ]
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])

    def list_hl7_messages(self) -> list[Hl7Message]:
        with self._lock:
            return _parse_all(Hl7Message, self._load_hl7_unlocked())

    def get_hl7_message(self, message_id: str) -> Hl7Message | None:
        with self._lock:
            for message in _parse_all(Hl7Message, self._load_hl7_unlocked()):
                if message.id == message_id:
                    return message
            return None

    def add_hl7_message(self, message: Hl7Message) -> Hl7Message:
        with self._lock:
            entries = _parse_all(Hl7Message, self._load_hl7_unlocked())
            _ensure_unique_id(message, entries)
            entries.insert(0, message)
            self._write_json(self.hl7_path, [item.model_dump(mode="json") for item in entries])
            return message

    def delete_hl7_message(self, message_id: str) -> None:
        with self._lock:
            entries = [
                message
                for message in _parse_all(Hl7Message, self._load_hl7_unlocked())
                if message.id != message_id
            ]
            self._write_json(self.hl7_path, [item.model_dump(mode="json") for item in entries])

    def _load_unlocked(self) -> AppConfig:
        if not self.config_path.exists():
            config = AppConfig()
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            return AppConfig.model_validate(raw)
        except (json.JSONDecodeError, OSError, ValidationError, ValueError) as exc:
            # A half-written or hand-edited config must not stop the workstation
            # from starting. Keep the bad file so the operator can recover the
            # remotes list by hand, and continue on defaults.
            self._quarantine(self.config_path, exc)
            config = AppConfig()
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config

    def _quarantine(self, path: Path, exc: Exception) -> Path | None:
        """Rename an unreadable JSON file out of the way. Best effort."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        try:
            os.replace(path, backup)
        except OSError:
            backup = None
        # Imported here because app.applog imports app.models, and importing the
        # logger at module scope would make store -> applog -> models circular
        # for callers that import app.store first.
        from app.applog import log

        log.error(
            "Could not read %s (%s: %s). Continuing with defaults.%s",
            path.name,
            type(exc).__name__,
            exc,
            f" Previous file kept as {backup.name}." if backup else "",
        )
        return backup

    def _load_list_unlocked(self, path: Path) -> list:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            self._quarantine(path, exc)
            return []
        return data if isinstance(data, list) else []

    def _load_results_unlocked(self) -> list:
        return self._load_list_unlocked(self.results_path)

    def _load_worklist_unlocked(self) -> list:
        return self._load_list_unlocked(self.worklist_path)

    def _load_hl7_unlocked(self) -> list:
        return self._load_list_unlocked(self.hl7_path)

    def _write_json(self, path: Path, payload: object) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, default=str)
                handle.write("\n")
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
