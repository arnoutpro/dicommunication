"""The audit log (app/audit.py)."""

from __future__ import annotations

import json
from pathlib import Path

from app.audit import GENESIS, AuditLog


def _log(tmp_path: Path, **kw) -> AuditLog:
    return AuditLog(tmp_path, **kw)


def test_entries_are_json_lines_chained_to_the_one_before(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record(user="alice", role="admin", ip="10.0.0.5", action="Sign in", method="POST", path="/login", status=303)
    log.record(user="alice", role="admin", ip="10.0.0.5", action="Run an Analytics query", method="POST", path="/tools/c-find-advanced/run", status=200)
    rows = log.entries()
    assert [r["seq"] for r in rows] == [1, 2] and rows[0]["prev"] == GENESIS and rows[1]["prev"] == rows[0]["hash"]
    assert rows[0]["user"] == "alice" and rows[0]["action"] == "Sign in" and rows[0]["ts"].endswith("+00:00")
    assert log.verify() == (True, "")


def test_the_chain_carries_on_after_a_restart(tmp_path: Path) -> None:
    first = _log(tmp_path)
    first.record(action="a")
    again = _log(tmp_path)
    again.record(action="b")
    assert [r["seq"] for r in again.entries()] == [1, 2] and again.verify() == (True, "")


def test_an_edited_removed_or_reordered_line_breaks_the_chain(tmp_path: Path) -> None:
    log = _log(tmp_path)
    for name in ("a", "b", "c", "d"):
        log.record(user="alice", action=name)
    lines = log.path.read_text().splitlines()

    def check(new_lines: list[str]) -> tuple[bool, str]:
        log.path.write_text("\n".join(new_lines) + "\n")
        return log.verify()

    edited = json.loads(lines[1])
    edited["user"] = "mallory"
    ok, why = check([lines[0], json.dumps(edited), *lines[2:]])
    assert not ok and "entry 2 was changed" in why
    ok, why = check([lines[0], lines[2], lines[3]])  # a line cut out
    assert not ok and "missing" in why
    ok, why = check([lines[0], lines[2], lines[1], lines[3]])  # swapped
    assert not ok
    ok, why = check([lines[0], "garbage", *lines[2:]])
    assert not ok and "not readable" in why
    assert check(lines) == (True, "")


def test_the_log_never_holds_more_than_it_is_given_and_trims_long_values(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record(action="x", path="/p" + "a" * 1000, detail="d" * 1000)
    row = log.entries()[0]
    assert len(row["path"]) == 300 and len(row["detail"]) == 300
    assert set(row) == {"seq", "ts", "user", "role", "ip", "action", "method", "path", "status", "detail", "prev", "hash"}


def test_tail_is_newest_first_and_can_filter(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record(user="alice", action="Sign in")
    log.record(user="bob", action="Run an Analytics query")
    log.record(user="alice", action="Dicom Import")
    assert [r["action"] for r in log.tail()] == ["Dicom Import", "Run an Analytics query", "Sign in"]
    assert [r["action"] for r in log.tail(user="ALICE")] == ["Dicom Import", "Sign in"]
    assert [r["user"] for r in log.tail(action="query")] == ["bob"]
    assert len(log.tail(limit=2)) == 2


def test_the_file_rolls_over_and_the_chain_continues(tmp_path: Path) -> None:
    log = _log(tmp_path, max_bytes=600, keep=3)
    for index in range(30):
        log.record(user="alice", action=f"step {index}", path="/x" * 20)
    names = sorted(p.name for p in tmp_path.glob("audit*.log"))
    assert "audit.log" in names and "audit.1.log" in names and len(names) <= 4  # the live file and at most `keep` older ones
    live = log.entries()
    older = log.entries(tmp_path / "audit.1.log")
    assert older and live and live[0]["prev"] == older[-1]["hash"]
    assert log.verify() == (True, "")


def test_an_empty_log_is_fine(tmp_path: Path) -> None:
    log = _log(tmp_path)
    assert log.entries() == [] and log.tail() == [] and log.verify() == (True, "")
