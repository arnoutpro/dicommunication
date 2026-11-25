"""Dicom Router: a rule that watches a folder for PDFs and sends them as Encapsulated PDF Storage.

Each run looks at the files directly in the folder (not in sub-folders), skips a file that is still
being written, sends the rest with the PDF to DICOM engine, and moves what the PACS stored into a
``Sent`` sub-folder. A file the PACS did not take stays where it is and is tried again on the next
run; a file that is not a PDF, or is too large, goes to ``Failed`` once.

Nothing here writes a file name into the run history or the log: a name often holds a patient's ID.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.models import LocalAE, RemoteNode, RouteMatch, RouteRule
from app.pdf_dicom import MAX_FILES, MAX_PDF_BYTES, PDF_MAGIC, is_pdf_filename

SENT_DIR = "Sent"
FAILED_DIR = "Failed"
# A file changed less than this long ago may still be being copied into the folder.
SETTLE_SECONDS = 10


class WatchError(ValueError):
    """The folder cannot be used (missing, not a folder, or not readable)."""


@dataclass
class Candidate:
    path: Path
    name: str
    key: str


@dataclass
class Scan:
    candidates: list[Candidate] = field(default_factory=list)
    rejected: list[tuple[Path, str]] = field(default_factory=list)  # (file, reason without a name)
    already_sent: list[Path] = field(default_factory=list)  # still in the folder after a failed move
    waiting: int = 0
    more: int = 0


@dataclass
class Outcome:
    sent: int = 0
    failed: int = 0
    waiting: int = 0
    more: int = 0
    stuck: int = 0  # sent, but the file could not be moved into Sent
    matches: list[RouteMatch] = field(default_factory=list)
    error: str = ""


def file_key(name: str, size: int, mtime_ns: int) -> str:
    """A short hash standing for one version of one file, so the file name is never stored."""
    return hashlib.sha256(f"{name}|{size}|{mtime_ns}".encode("utf-8", "replace")).hexdigest()[:16]


def resolve_folder(folder: str) -> Path:
    raw = (folder or "").strip()
    if not raw:
        raise WatchError("Watch folder is required.")
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise WatchError("The watch folder was not found, or the app's account cannot read it.") from None
    if not path.is_dir():
        raise WatchError("The watch folder is not a folder.")
    if path.parent == path:
        raise WatchError("Refusing to watch the filesystem root. Choose a reports folder.")
    return path


def scan_folder(folder: Path, sent_keys: set[str], now_ts: float) -> Scan:
    scan = Scan()
    try:
        children = sorted(folder.iterdir())
    except OSError:
        raise WatchError("The watch folder was not found, or the app's account cannot read it.") from None
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if child.is_symlink() or not child.is_file() or not is_pdf_filename(child.name):
                continue
            info = child.stat()
        except OSError:
            continue
        key = file_key(child.name, info.st_size, info.st_mtime_ns)
        if key in sent_keys:
            scan.already_sent.append(child)
            continue
        if now_ts - info.st_mtime < SETTLE_SECONDS:
            scan.waiting += 1
            continue
        if info.st_size > MAX_PDF_BYTES:
            scan.rejected.append((child, f"larger than {MAX_PDF_BYTES // (1024 * 1024)} MB"))
            continue
        try:
            with child.open("rb") as handle:
                head = handle.read(4)
        except OSError:
            continue  # not readable right now (locked by whoever writes it): try again next run
        if head != PDF_MAGIC:
            scan.rejected.append((child, "not a PDF (missing %PDF header)"))
            continue
        if len(scan.candidates) >= MAX_FILES:
            scan.more += 1
            continue
        scan.candidates.append(Candidate(path=child, name=child.name, key=key))
    return scan


def move_into(folder: Path, file: Path, subdir: str) -> bool:
    """Move a file into folder/subdir, never overwriting one that is already there."""
    try:
        target_dir = folder / subdir
        target_dir.mkdir(exist_ok=True)
        target = target_dir / file.name
        counter = 2
        while target.exists():
            target = target_dir / f"{file.stem} ({counter}){file.suffix}"
            counter += 1
        os.replace(file, target)
        return True
    except OSError:
        return False


def _scrub(text: str, names: list[str]) -> str:
    for name in names:
        if name:
            text = text.replace(name, "the file")
    return text


def _options(rule: RouteRule, batch: list[tuple[Candidate, bytes]]) -> dict:
    per_file = rule.pdf_patient_mode == "per_file"
    return {
        "pdfs": [
            {"filename": item.name, "content_b64": base64.b64encode(data).decode("ascii")} for item, data in batch
        ],
        "send": True,
        "modality": rule.pdf_modality,
        "unique_patient": per_file,
        "patient_name": rule.pdf_patient_name if rule.pdf_patient_mode == "typed" else "",
        "patient_id": rule.pdf_patient_id if rule.pdf_patient_mode == "typed" else "",
        "generate_name": rule.pdf_patient_mode == "generated",
        "generate_id": rule.pdf_patient_mode == "generated",
        "accession_number": rule.pdf_accession_number,
        "generate_accession": rule.pdf_generate_accession,
        "study_description": rule.pdf_study_description,
        "generate_study_description": rule.pdf_generate_study_description,
        "document_title": rule.pdf_document_title,
        "generate_document_title": rule.pdf_generate_document_title,
        "same_study": not per_file,
    }


def run_watch(
    rule: RouteRule, local: LocalAE, remote: RemoteNode, now: datetime, interrupt=None
) -> tuple[Outcome, list[str]]:
    """One run. Returns what happened and the keys of the files the PACS stored."""
    from app.tools.pdf_store import PdfStoreTool  # late: the tools import the models

    outcome = Outcome()
    folder = resolve_folder(rule.pdf_folder)
    scan = scan_folder(folder, set(rule.pdf_sent_keys), time.time())
    outcome.waiting, outcome.more = scan.waiting, scan.more

    # A file the PACS stored on an earlier run whose move into Sent failed: try the move again.
    for leftover in scan.already_sent:
        if not move_into(folder, leftover, SENT_DIR):
            outcome.stuck += 1

    for file, reason in scan.rejected:
        moved = move_into(folder, file, FAILED_DIR)
        outcome.failed += 1
        outcome.matches.append(
            RouteMatch(study_instance_uid="", status="failed", error=f"File {reason}" + ("" if moved else " (and could not be moved to Failed)"))
        )

    if not scan.candidates:
        return outcome, []
    if interrupt is not None and interrupt.is_set():
        return outcome, []

    batch: list[tuple[Candidate, bytes]] = []
    for item in scan.candidates:
        try:
            batch.append((item, item.path.read_bytes()))
        except OSError:
            continue  # vanished or locked since the scan: next run
    if not batch:
        return outcome, []

    names = [item.name for item, _ in batch]
    result = PdfStoreTool().run(local, remote, _options(rule, batch))
    records = {str(row.get("source")): row for row in (result.records or [])}
    sent_keys: list[str] = []
    for item, _data in batch:
        row = records.get(item.name)
        status = str(row.get("status")) if row else ""
        study_uid = str(row.get("study_instance_uid") or "") if row else ""
        if status == "stored":
            outcome.sent += 1
            sent_keys.append(item.key)
            moved = move_into(folder, item.path, SENT_DIR)
            if not moved:
                outcome.stuck += 1
            outcome.matches.append(
                RouteMatch(
                    study_instance_uid=study_uid, status="forwarded", modality=rule.pdf_modality,
                    error="" if moved else "Sent, but the file could not be moved into the Sent folder",
                )
            )
            continue
        if row and status == "no response":
            message = "No C-STORE response (timeout, abort, or invalid PDU)."
        elif row and status.startswith("0x"):
            message = f"C-STORE status {status}"
        else:
            message = _scrub(result.summary or "The PACS did not store the file.", names)
        outcome.failed += 1
        outcome.matches.append(
            RouteMatch(study_instance_uid=study_uid, status="failed", modality=rule.pdf_modality, error=message[:200])
        )
    return outcome, sent_keys
