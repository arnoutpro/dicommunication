"""Audit log: who did what, and when. Never what the patient data was.

Every sign-in, failed sign-in, change and use of a tool is one line of JSON in
``audit.log`` in the data folder. A line holds the user, role, address, action, URL
path and result. It does not hold form values, query filters, or anything read
from a patient record, so the log can be kept longer than the data it describes.

Each line carries the hash of the one before it, so an edited, removed or
reordered line breaks the chain and ``verify`` says where. The file rolls over at
5 MB, keeping ten, and the chain carries across. There is deliberately no way to
clear it from the app. (Logging who looked at patient data is what hospital
information-security standards such as NEN 7513 ask for; check the exact
requirements with your security officer.)
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
KEEP_FILES = 10
GENESIS = "0" * 64
FIELDS = ("seq", "ts", "user", "role", "ip", "action", "method", "path", "status", "detail", "prev")


def _digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, data_dir: Path | str, max_bytes: int = MAX_BYTES, keep: int = KEEP_FILES) -> None:
        self.path = Path(data_dir) / "audit.log"
        self.max_bytes = max_bytes
        self.keep = keep
        self._lock = threading.Lock()
        self._seq, self._last = self._resume()

    def _resume(self) -> tuple[int, str]:
        last_line = ""
        try:
            with open(self.path, "rb") as handle:
                for raw in handle:
                    if raw.strip():
                        last_line = raw.decode("utf-8", "replace")
        except FileNotFoundError:
            return 0, GENESIS
        try:
            entry = json.loads(last_line)
            return int(entry["seq"]), str(entry["hash"])
        except (ValueError, KeyError, TypeError):
            return 0, GENESIS

    def record(
        self,
        *,
        user: str = "",
        role: str = "",
        ip: str = "",
        action: str,
        method: str = "",
        path: str = "",
        status: int | str = "",
        detail: str = "",
    ) -> None:
        with self._lock:
            self._seq += 1
            body = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "user": user,
                "role": role,
                "ip": ip,
                "action": action,
                "method": method,
                "path": path[:300],
                "status": status,
                "detail": detail[:300],
                "prev": self._last,
            }
            entry = {**body, "hash": _digest(body)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._roll_if_needed()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._last = entry["hash"]

    def _roll_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except FileNotFoundError:
            return
        for index in range(self.keep - 1, 0, -1):
            older = self.path.with_name(f"audit.{index}.log")
            if older.exists():
                older.replace(self.path.with_name(f"audit.{index + 1}.log"))
        self.path.replace(self.path.with_name("audit.1.log"))
        stale = self.path.with_name(f"audit.{self.keep + 1}.log")
        stale.unlink(missing_ok=True)

    def entries(self, path: Path | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            with open(path or self.path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        out.append({"broken": line.strip()[:200]})
        except FileNotFoundError:
            pass
        return out

    def tail(self, limit: int = 500, user: str = "", action: str = "") -> list[dict[str, Any]]:
        """Newest first, optionally only one user or actions containing some text."""
        rows = [row for row in self.entries() if "broken" not in row]
        if user:
            rows = [row for row in rows if row.get("user", "").casefold() == user.casefold()]
        if action:
            rows = [row for row in rows if action.casefold() in str(row.get("action", "")).casefold()]
        return list(reversed(rows[-limit:]))

    def verify(self) -> tuple[bool, str]:
        """(True, "") if the current file's chain is intact, else (False, what is wrong)."""
        previous = None
        for index, row in enumerate(self.entries(), start=1):
            if "broken" in row:
                return False, f"line {index} is not readable"
            body = {key: row.get(key) for key in FIELDS}
            if _digest(body) != row.get("hash"):
                return False, f"entry {row.get('seq')} was changed"
            if previous is not None and row.get("prev") != previous:
                return False, f"an entry before {row.get('seq')} is missing or out of order"
            previous = row["hash"]
        return True, ""
