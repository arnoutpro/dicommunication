"""Batch C-ECHO across every configured remote DICOM node."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app.models import EchoBoard, EchoBoardRow, RemoteNode, ToolResult
from app.store import ConfigStore
from app.tools import get_tool

MAX_WORKERS = 8
ECHO_TOOL_ID = "c-echo"


def latest_echo_by_remote(store: ConfigStore) -> dict[str, ToolResult]:
    latest: dict[str, ToolResult] = {}
    for result in store.list_results(limit=200):
        if result.tool_id != ECHO_TOOL_ID or not result.remote_id:
            continue
        if result.remote_id not in latest:
            latest[result.remote_id] = result
    return latest


def snapshot(store: ConfigStore, results_by_id: dict[str, ToolResult] | None = None) -> EchoBoard:
    config = store.load()
    latest = latest_echo_by_remote(store) if results_by_id is None else results_by_id
    rows: list[EchoBoardRow] = []
    for remote in config.remotes:
        result = latest.get(remote.id)
        if result is None:
            status = "unknown"
        else:
            status = "pass" if result.ok else "fail"
        rows.append(EchoBoardRow(remote=remote, result=result, status=status))
    return EchoBoard(
        rows=rows,
        total=len(rows),
        passed=sum(1 for row in rows if row.status == "pass"),
        failed=sum(1 for row in rows if row.status == "fail"),
        unknown=sum(1 for row in rows if row.status == "unknown"),
    )


def _echo_one(local, remote: RemoteNode) -> ToolResult:
    tool = get_tool(ECHO_TOOL_ID)
    return tool.run(local, remote)


def run_all(store: ConfigStore) -> EchoBoard:
    config = store.load()
    remotes = list(config.remotes)
    if not remotes:
        return snapshot(store, {})

    started = time.perf_counter()
    results_by_id: dict[str, ToolResult] = {}
    workers = min(MAX_WORKERS, len(remotes))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_echo_one, config.local, remote): remote.id for remote in remotes
        }
        for future in as_completed(futures):
            remote_id = futures[future]
            results_by_id[remote_id] = future.result()

    ordered = [results_by_id[remote.id] for remote in remotes]
    store.add_results(ordered)

    board = snapshot(store, results_by_id)
    board.duration_ms = round((time.perf_counter() - started) * 1000, 1)
    board.ran_at = datetime.now(timezone.utc)
    return board
