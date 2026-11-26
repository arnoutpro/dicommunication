"""Dicom Router: a rule that watches a folder for DICOM files and sends them to a PACS.

Each run looks at what is directly in the folder. Every *item* there is one unit: a loose file, a
sub-folder (a study exported to a folder, at any depth below it), or a ``.zip`` / ``.7z`` archive. An
item that is still being copied in (anything in it changed in the last few seconds) waits for the next run.
The settled items are scanned and sent in one association with the same engine as Dicom Import. An item
whose images were all stored moves into a ``Sent`` sub-folder, as a whole. An item with any image the PACS
did not take stays where it is and is tried again on the next run. An item with no DICOM in it is left alone.

A 7z has to be unpacked to scan it; that happens in a temporary folder that is removed again at the end
of the run. Nothing here writes a file or folder name into the run history or the log: a name often
holds a patient's ID. Counts only.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.dicom_import import (
    ScanResult,
    SendOptions,
    SendProgress,
    close_sources,
    discard_unpacked,
    scan_folder,
    send_import,
)
from app.models import LocalAE, RemoteNode, RouteMatch, RouteRule
from app.pdf_watch import (
    FAILED_DIR,
    SENT_DIR,
    SETTLE_SECONDS,
    Outcome,
    WatchError,
    file_key,
    move_into,
    resolve_folder,
)

MAX_ITEMS_PER_RUN = 200  # more than this many items in the folder: the rest wait for the next run
# An export copied from a CD or over the network pauses between files; wait longer than for one PDF.
ITEM_SETTLE_SECONDS = max(SETTLE_SECONDS, 30)


@dataclass
class _Item:
    path: Path
    name: str
    key: str
    newest: float  # modification time of the newest thing in it


def _fingerprint(path: Path) -> tuple[int, int, float] | None:
    """(total size, newest mtime in ns, newest mtime in seconds) of a file or of everything in a folder."""
    try:
        info = path.stat()
        if not path.is_dir():
            return info.st_size, info.st_mtime_ns, info.st_mtime
        size, newest_ns, count = 0, info.st_mtime_ns, 0
        for folder, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    one = os.stat(os.path.join(folder, name))
                except OSError:
                    continue
                size += one.st_size
                count += 1
                newest_ns = max(newest_ns, one.st_mtime_ns)
        return size + count, newest_ns, newest_ns / 1e9  # the count makes adding an empty file change the key
    except OSError:
        return None


def _items(folder: Path) -> list[_Item]:
    try:
        children = sorted(folder.iterdir())
    except OSError:
        raise WatchError("The watch folder was not found, or the app's account cannot read it.") from None
    items: list[_Item] = []
    for child in children:
        if child.name.startswith(".") or child.name in (SENT_DIR, FAILED_DIR):
            continue
        try:
            if child.is_symlink():
                continue
        except OSError:
            continue
        found = _fingerprint(child)
        if found is None:
            continue
        size, newest_ns, newest = found
        items.append(_Item(path=child, name=child.name, key=file_key(child.name, size, newest_ns), newest=newest))
    return items


def run_watch(
    rule: RouteRule,
    local: LocalAE,
    remote: RemoteNode,
    now: datetime,
    interrupt: threading.Event | None = None,
    workdir: str | os.PathLike[str] | None = None,
) -> tuple[Outcome, list[str]]:
    """One run. Returns what happened and the keys of the items the PACS stored."""
    outcome = Outcome()
    folder = resolve_folder(rule.pdf_folder)
    known = set(rule.pdf_sent_keys)
    now_ts = time.time()

    todo: list[_Item] = []
    skip: set[str] = {SENT_DIR, FAILED_DIR}
    for item in _items(folder):
        if item.key in known:
            # Stored on an earlier run, and still here because moving it into Sent failed: try the move again.
            skip.add(item.name)
            if not move_into(folder, item.path, SENT_DIR):
                outcome.stuck += 1
            continue
        if now_ts - item.newest < ITEM_SETTLE_SECONDS:
            skip.add(item.name)
            outcome.waiting += 1
            continue
        if len(todo) >= MAX_ITEMS_PER_RUN:
            skip.add(item.name)
            outcome.more += 1
            continue
        todo.append(item)
    for child in folder.iterdir():
        if child.name.startswith(".") or child.is_symlink():
            skip.add(child.name)
    if not todo or (interrupt is not None and interrupt.is_set()):
        return outcome, []

    scan: ScanResult | None = None
    sent_keys: list[str] = []
    try:
        scan = scan_folder(folder, cancel=interrupt, workdir=workdir, skip_top=skip)
        if scan.cancelled or scan.instance_count == 0:
            return outcome, []
        wanted: dict[str, list[str]] = {}  # item name -> the instance paths in it
        for study in scan.studies():
            for series in study.series.values():
                for instance in series.instances:
                    wanted.setdefault(scan.entry_of(instance.path), []).append(instance.path)
        progress = SendProgress()
        send_import(scan, _no_edits(), local, remote, SendOptions(record_paths=True), progress, interrupt)
        stored = set(progress.sent_paths)
        for item in todo:
            paths = wanted.get(item.name)
            if not paths:
                continue  # nothing DICOM in it (or all repeats of images sent from another item): leave it alone
            if all(path in stored for path in paths):
                outcome.sent += 1
                sent_keys.append(item.key)
                moved = move_into(folder, item.path, SENT_DIR)
                if not moved:
                    outcome.stuck += 1
                outcome.matches.append(
                    RouteMatch(
                        study_instance_uid="", status="forwarded",
                        error="" if moved else "Sent, but could not be moved into the Sent folder",
                    )
                )
            elif not progress.cancelled:
                outcome.failed += 1
                reason = progress.message or "The PACS did not store every image"
                outcome.matches.append(RouteMatch(study_instance_uid="", status="failed", error=reason[:200]))
        return outcome, sent_keys
    finally:
        close_sources()
        discard_unpacked(scan)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _no_edits():
    from app.dicom_import import ImportEdits

    return ImportEdits()
