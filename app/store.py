"""Atomic JSON persistence for local/remote config and recent tool results."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from app.models import AppConfig, RemoteNode, ToolResult, WorklistEntry

def _default_data_dir() -> Path:
    env = os.environ.get("DICOMM_DATA_DIR")
    if env:
        return Path(env)
    home_dir = Path.home() / ".dicommunication"
    legacy = Path("data")
    if not (home_dir / "config.json").exists() and (legacy / "config.json").exists():
        return legacy
    return home_dir


DEFAULT_DATA_DIR = _default_data_dir()


class ConfigStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.json"
        self.results_path = self.data_dir / "results.json"
        self.worklist_path = self.data_dir / "worklist.json"
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

    def add_remote(self, remote: RemoteNode) -> AppConfig:
        with self._lock:
            config = self._load_unlocked()
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
        return [ToolResult.model_validate(item) for item in raw[:limit]]

    def list_worklist(self) -> list[WorklistEntry]:
        with self._lock:
            return [WorklistEntry.model_validate(item) for item in self._load_worklist_unlocked()]

    def save_worklist(self, entries: list[WorklistEntry]) -> list[WorklistEntry]:
        with self._lock:
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])
            return entries

    def add_worklist_entry(self, entry: WorklistEntry) -> WorklistEntry:
        with self._lock:
            entries = [WorklistEntry.model_validate(item) for item in self._load_worklist_unlocked()]
            entries.insert(0, entry)
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])
            return entry

    def delete_worklist_entry(self, entry_id: str) -> None:
        with self._lock:
            entries = [
                WorklistEntry.model_validate(item)
                for item in self._load_worklist_unlocked()
                if item.get("id") != entry_id
            ]
            self._write_json(self.worklist_path, [item.model_dump(mode="json") for item in entries])

    def _load_unlocked(self) -> AppConfig:
        if not self.config_path.exists():
            config = AppConfig()
            self._write_json(self.config_path, config.model_dump(mode="json"))
            return config
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)

    def _load_results_unlocked(self) -> list:
        if not self.results_path.exists():
            return []
        try:
            data = json.loads(self.results_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _load_worklist_unlocked(self) -> list:
        if not self.worklist_path.exists():
            return []
        try:
            data = json.loads(self.worklist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

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
