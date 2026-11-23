"""Security settings an administrator can change, kept in ``security.json``.

* ``retention_minutes``: how long the rows a tool fetched (names, IDs) are kept on
  disk. 0 keeps nothing. The Router history never keeps patient rows at all.
* ``history_days``: after how many days Router runs and tool results are deleted
  altogether. 0 keeps them until the fixed limits (500 runs, 200 results) push them out.
* ``import_roots``: the folders an operator may read files from (Dicom Import, PDF
  to DICOM). An administrator is not limited to them; with no folders listed, only
  an administrator can pick a folder.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_RETENTION_MINUTES = 30
DEFAULT_HISTORY_DAYS = 30


class SecuritySettings(BaseModel):
    retention_minutes: int = Field(default=DEFAULT_RETENTION_MINUTES, ge=0, le=24 * 60)
    history_days: int = Field(default=DEFAULT_HISTORY_DAYS, ge=0, le=3650)
    import_roots: list[str] = Field(default_factory=list)

    @field_validator("import_roots")
    @classmethod
    def _clean_roots(cls, value: list[str]) -> list[str]:
        roots: list[str] = []
        for item in value:
            text = (item or "").strip()
            if not text:
                continue
            if not (PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()):
                raise ValueError(f"{text} is not a full path. Give a folder such as D:\\Imports or /data/imports.")
            if text not in roots:
                roots.append(text)
        return roots


class SecuritySettingsStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "security.json"
        self._lock = threading.Lock()

    def load(self) -> SecuritySettings:
        with self._lock:
            try:
                return SecuritySettings.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError):
                return SecuritySettings()

    def save(self, settings: SecuritySettings) -> SecuritySettings:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(settings.model_dump(), handle, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return settings


def path_is_allowed(path: str, roots: list[str]) -> bool:
    """True if ``path`` is one of the allowed folders or inside one (symlinks and ``..`` resolved)."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in roots:
        try:
            base = Path(root).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if target == base or base in target.parents:
            return True
    return False
