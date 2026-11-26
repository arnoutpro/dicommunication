"""DICOM import: scan a folder, summarise what is in it, edit patient and exam
details, and C-STORE it to a PACS.

Folder in, PACS out, nothing written in between. The scan keeps only a small
index (where each file is and its identifying tags). Edits are applied to each
file as it is read for sending, so no modified copy ever lands on disk, and
nothing about the patients is stored by the app.

The pieces, in the order a user meets them:

* ``scan_folder``   finds a DICOMDIR and/or loose DICOM files and builds a
                    patient > study > series > instance index with warnings.
* ``ImportEdits``   the changes to make, validated and normalised.
* ``send_import``   reads each selected file, applies the edits, and stores it.

No web code here, so it can be tested with plain files and a mock PACS.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    UID,
    generate_uid,
)

try:  # 7z needs this; without it 7z files are reported, not read
    import py7zr
except ImportError:  # pragma: no cover
    py7zr = None

from app.dicom_client import make_ae
from app.models import LocalAE, RemoteNode

DICOMDIR_SOP_CLASS = "1.2.840.10008.1.3.10"
MAX_FILES = 200_000
MAX_EXAMPLES = 8
MAX_FAILURES_LISTED = 200
MAX_SOP_CLASSES = 120  # one presentation context each; the protocol allows 128
READ_DEFER_SIZE = "64 KB"  # large elements (waveforms, PDFs) are not loaded just to be indexed
RECONNECTS = 2
ARCHIVE_SUFFIXES = (".zip", ".7z")
MAX_UNPACK_BYTES = 8 * 1024 * 1024 * 1024  # a 7z is unpacked to a temporary folder; refuse one that would be larger
ARCHIVE_SEP = " \u25b8 "  # "export.zip ▸ study/IM0001": a file inside an archive, read in place, never unpacked to disk
MAX_MEMBER_BYTES = 1024 * 1024 * 1024  # one file inside an archive is read into memory to send it

ProgressFn = Callable[["ScanProgress"], None]


class ImportProblem(Exception):
    """A problem the user can fix (bad folder, bad edit value)."""


# --- files inside a ZIP, read in place -----------------------------------------

_open_zip = threading.local()


def split_source(path: str) -> tuple[str, str]:
    """``(file, member)``: member is empty for a plain file, else the name inside the ZIP."""
    archive, sep, member = path.partition(ARCHIVE_SEP)
    return (archive, member) if sep else (path, "")


def close_sources() -> None:
    """Close the ZIP this thread has open (it is kept open between files, then released here)."""
    cached = getattr(_open_zip, "cached", None)
    _open_zip.cached = None
    if cached is not None:
        try:
            cached[1].close()
        except Exception:  # noqa: BLE001
            pass


def _zip_for(archive: str) -> zipfile.ZipFile:
    cached = getattr(_open_zip, "cached", None)
    if cached is not None and cached[0] == archive:
        return cached[1]
    close_sources()
    opened = zipfile.ZipFile(archive)
    _open_zip.cached = (archive, opened)
    return opened


def read_dataset(path: str, **kwargs) -> Dataset:
    """Read a DICOM file, from disk or from inside a ZIP, without writing anything."""
    archive, member = split_source(path)
    if not member:
        return dcmread(path, **kwargs)
    archive_file = _zip_for(archive)
    info = archive_file.getinfo(member)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ImportProblem(f"The file is larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB inside the ZIP.")
    return dcmread(io.BytesIO(archive_file.read(info)), **kwargs)


def discard_unpacked(scan: ScanResult | None) -> None:
    """Delete the temporary folder a scan unpacked 7z files into."""
    if scan is not None and scan.workdir:
        shutil.rmtree(scan.workdir, ignore_errors=True)
        scan.workdir = ""
        scan.unpacked = {}


def _safe_member(name: str) -> bool:
    """A member name that stays inside the archive: no absolute path, no drive, no '..'."""
    clean = name.replace("\\", "/")
    if not clean or clean.endswith("/") or clean.startswith("/") or re.match(r"^[A-Za-z]:", clean):
        return False
    return ".." not in clean.split("/")


# --- the index -----------------------------------------------------------------


@dataclass
class ImportedInstance:
    path: str
    size: int
    sop_class_uid: str
    sop_instance_uid: str
    transfer_syntax_uid: str
    number: int | None = None
    frames: int = 1
    in_dicomdir: bool = False


@dataclass
class ImportedSeries:
    uid: str
    number: int | None = None
    modality: str = ""
    description: str = ""
    body_part: str = ""
    instances: list[ImportedInstance] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(item.size for item in self.instances)


@dataclass
class ImportedStudy:
    uid: str
    patient_key: str
    date: str = ""
    time: str = ""
    description: str = ""
    accession: str = ""
    study_id: str = ""
    referring_physician: str = ""
    series: dict[str, ImportedSeries] = field(default_factory=dict)

    @property
    def instance_count(self) -> int:
        return sum(len(item.instances) for item in self.series.values())

    @property
    def size(self) -> int:
        return sum(item.size for item in self.series.values())

    @property
    def modalities(self) -> list[str]:
        return sorted({item.modality for item in self.series.values() if item.modality})


@dataclass
class ImportedPatient:
    key: str
    patient_id: str = ""
    names: list[str] = field(default_factory=list)  # more than one means the files disagree
    birth_dates: list[str] = field(default_factory=list)
    sexes: list[str] = field(default_factory=list)
    studies: dict[str, ImportedStudy] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def birth_date(self) -> str:
        return self.birth_dates[0] if self.birth_dates else ""

    @property
    def sex(self) -> str:
        return self.sexes[0] if self.sexes else ""

    @property
    def instance_count(self) -> int:
        return sum(study.instance_count for study in self.studies.values())


@dataclass
class ScanWarning:
    level: str  # "error": left out of the import, "warn": check it, "info"
    text: str


@dataclass
class ScanProgress:
    files_seen: int = 0
    dicom_files: int = 0
    current: str = ""


@dataclass
class ScanResult:
    root: str
    patients: dict[str, ImportedPatient] = field(default_factory=dict)
    warnings: list[ScanWarning] = field(default_factory=list)
    files_seen: int = 0
    skipped_files: int = 0
    skipped_examples: list[str] = field(default_factory=list)
    dicomdirs: list[str] = field(default_factory=list)
    seconds: float = 0.0
    cancelled: bool = False
    workdir: str = ""  # where 7z files were unpacked (temporary); remove with discard_unpacked
    unpacked: dict[str, str] = field(default_factory=dict)  # unpacked folder -> the 7z it came from

    def display(self, path: str) -> str:
        """A file's path as the person knows it: relative to the folder, or inside its archive."""
        for folder, label in self.unpacked.items():
            prefix = folder + os.sep
            if path.startswith(prefix):
                return f"{label}{ARCHIVE_SEP}{path[len(prefix):].replace(os.sep, '/')}"
        return os.path.relpath(path, self.root)

    def entry_of(self, path: str) -> str:
        """The name directly inside the scanned folder that a file belongs to (a loose file, a sub-folder or an archive)."""
        shown = self.display(path).replace(os.sep, "/")
        return shown.split(ARCHIVE_SEP)[0].split("/")[0]

    def studies(self) -> list[ImportedStudy]:
        return [study for patient in self.patients.values() for study in patient.studies.values()]

    def study(self, uid: str) -> ImportedStudy | None:
        for study in self.studies():
            if study.uid == uid:
                return study
        return None

    @property
    def instance_count(self) -> int:
        return sum(study.instance_count for study in self.studies())

    @property
    def series_count(self) -> int:
        return sum(len(study.series) for study in self.studies())

    @property
    def size(self) -> int:
        return sum(study.size for study in self.studies())

    def transfer_syntaxes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for study in self.studies():
            for series in study.series.values():
                for instance in series.instances:
                    counts[instance.transfer_syntax_uid] = counts.get(instance.transfer_syntax_uid, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))


# --- scanning ------------------------------------------------------------------


def _text(ds: Dataset, keyword: str) -> str:
    value = getattr(ds, keyword, "")
    return "" if value is None else str(value).strip()


def _int(ds: Dataset, keyword: str) -> int | None:
    try:
        return int(getattr(ds, keyword))
    except (AttributeError, TypeError, ValueError):
        return None


def is_part10(path: str | os.PathLike[str]) -> bool:
    """True if the file starts like a DICOM file: 128 bytes, then ``DICM``."""
    try:
        with open(path, "rb") as handle:
            handle.seek(128)
            return handle.read(4) == b"DICM"
    except OSError:
        return False


def _read_header(path: str, *, force: bool = False) -> Dataset | None:
    try:
        return dcmread(path, stop_before_pixels=True, defer_size=READ_DEFER_SIZE, force=force)
    except Exception:  # noqa: BLE001 - not DICOM, or damaged: the caller counts it
        return None


def _referenced_by_dicomdir(path: Path) -> tuple[set[str], str]:
    """The files a DICOMDIR lists (normalised absolute paths), or why it cannot be read.

    Read straight from its directory records: a library reader would quietly
    drop a listed file that is missing, which is exactly what is worth reporting.
    """
    try:
        ds = dcmread(str(path), defer_size=READ_DEFER_SIZE)
        records = list(ds.DirectoryRecordSequence)
    except Exception as exc:  # noqa: BLE001
        return set(), f"{type(exc).__name__}: {exc}"
    referenced: set[str] = set()
    for record in records:
        parts = getattr(record, "ReferencedFileID", None)
        if not parts:
            continue
        names = [parts] if isinstance(parts, str) else [str(part) for part in parts]
        referenced.add(os.path.normcase(os.path.abspath(os.path.join(path.parent, *names))))
    return referenced, ""


def scan_folder(
    root: str | os.PathLike[str],
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    include_headerless: bool = False,
    max_files: int = MAX_FILES,
    workdir: str | os.PathLike[str] | None = None,
    skip_top: set[str] | None = None,
) -> ScanResult:
    """Index every DICOM file under ``root``, with a DICOMDIR cross-check if there is one.

    ``include_headerless`` also tries files without the ``DICM`` marker (old or
    stripped files); they are accepted only if they carry the UIDs needed to
    send them.
    """
    base = Path(str(root)).expanduser()
    single_archive = base.is_file() and base.suffix.lower() in ARCHIVE_SUFFIXES
    if single_archive:
        walker = [(str(base.parent), [], [base.name])]
        base = base.parent
    elif base.is_dir():
        walker = os.walk(base, followlinks=False)
    else:
        raise ImportProblem(f"{base} is not a folder or ZIP file this app can read.")
    started = time.perf_counter()
    result = ScanResult(root=str(base))
    state = ScanProgress()
    seen_sop: set[str] = set()
    duplicates = 0
    no_uids = 0
    unreadable_dicomdirs = 0
    indexed: set[str] = set()
    found: set[str] = set()
    stopped_at_limit = False
    unpacked_count = 0
    sevenz_unreadable: list[str] = []
    sevenz_locked = 0
    sevenz_big = 0
    sevenz_missing = 0
    sevenz_no_room = 0
    archives = 0
    encrypted = 0
    nested = 0
    too_big = 0
    broken: list[str] = []

    def tick(current: str = "") -> None:
        state.current = current
        if progress is not None:
            progress(state)

    def index_dataset(ds: Dataset, path: str, size: int, shown: str, in_archive: bool) -> None:
        nonlocal duplicates, no_uids, unreadable_dicomdirs
        meta = getattr(ds, "file_meta", None)
        media_class = str(getattr(meta, "MediaStorageSOPClassUID", "") or "") if meta is not None else ""
        sop_class = _text(ds, "SOPClassUID") or media_class
        if media_class == DICOMDIR_SOP_CLASS or sop_class == DICOMDIR_SOP_CLASS:
            result.dicomdirs.append(shown)
            if in_archive:
                return  # the files inside a ZIP are read directly; the DICOMDIR is not cross-checked
            refs, reason = _referenced_by_dicomdir(Path(path))
            if reason:
                unreadable_dicomdirs += 1
                result.warnings.append(
                    ScanWarning("warn", f"The DICOMDIR {shown} could not be read ({reason}). The files were scanned directly instead.")
                )
            indexed.update(refs)
            return
        sop_instance = _text(ds, "SOPInstanceUID") or (
            str(getattr(meta, "MediaStorageSOPInstanceUID", "") or "") if meta is not None else ""
        )
        study_uid = _text(ds, "StudyInstanceUID")
        series_uid = _text(ds, "SeriesInstanceUID")
        transfer = str(getattr(meta, "TransferSyntaxUID", "") or "") if meta is not None else ""
        if not (sop_class and sop_instance and study_uid and series_uid):
            no_uids += 1
            return
        if not transfer:
            transfer = str(ImplicitVRLittleEndian)
        if sop_instance in seen_sop:
            duplicates += 1
            return
        seen_sop.add(sop_instance)
        state.dicom_files += 1
        found.add(os.path.normcase(os.path.abspath(path)))
        _add_instance(result, ds, path, size, sop_class, sop_instance, study_uid, series_uid, transfer)
    def consider_file(path: str, size: int, shown: str, in_archive: bool, part10: bool) -> None:
        if not part10 and not include_headerless:
            result.skipped_files += 1
            if len(result.skipped_examples) < MAX_EXAMPLES:
                result.skipped_examples.append(shown)
            return
        ds = _read_header(path, force=not part10)
        if ds is None:
            result.skipped_files += 1
            if len(result.skipped_examples) < MAX_EXAMPLES:
                result.skipped_examples.append(shown)
            return
        index_dataset(ds, path, size, shown, in_archive)

    def unpack_7z(path: str, shown: str) -> None:
        """Unpack a 7z into the temporary folder, then index what came out of it."""
        nonlocal unpacked_count, sevenz_locked, sevenz_big, sevenz_missing, sevenz_no_room, stopped_at_limit
        if py7zr is None:
            sevenz_missing += 1
            return
        if workdir is None:
            sevenz_unreadable.append(f"{shown} (no temporary folder to unpack into)")
            return
        try:
            archive_file = py7zr.SevenZipFile(path)
        except Exception as exc:  # noqa: BLE001 - not a 7z, damaged, or password-protected headers
            if "password" in str(exc).lower() or type(exc).__name__ == "PasswordRequired":
                sevenz_locked += 1
            else:
                sevenz_unreadable.append(f"{shown} ({type(exc).__name__})")
            return
        with archive_file:
            if archive_file.needs_password():
                sevenz_locked += 1
                return
            declared = sum(int(getattr(entry, "uncompressed", 0) or 0) for entry in archive_file.list())
            if declared > MAX_UNPACK_BYTES:
                sevenz_big += 1
                return
            os.makedirs(workdir, mode=0o700, exist_ok=True)
            result.workdir = str(workdir)
            if shutil.disk_usage(workdir).free < declared * 1.1 + 64 * 1024 * 1024:
                sevenz_no_room += 1
                return
            unpacked_count += 1
            target = os.path.join(str(workdir), f"a{unpacked_count}")
            os.makedirs(target, mode=0o700)
            result.unpacked[target] = shown
            tick(f"{shown} (unpacking)")
            try:
                archive_file.extractall(path=target)
            except Exception as exc:  # noqa: BLE001
                sevenz_unreadable.append(f"{shown} ({type(exc).__name__})")
                shutil.rmtree(target, ignore_errors=True)
                result.unpacked.pop(target, None)
                return
        for inner_folder, inner_dirs, inner_files in os.walk(target, followlinks=False):
            inner_dirs.sort()
            for inner in sorted(inner_files):
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    return
                if state.files_seen >= max_files:
                    stopped_at_limit = True
                    return
                inner_path = os.path.join(inner_folder, inner)
                if os.path.islink(inner_path):
                    continue  # a link in an archive could point anywhere on this computer
                state.files_seen += 1
                member = os.path.relpath(inner_path, target).replace(os.sep, "/")
                if state.files_seen % 25 == 0:
                    tick(f"{shown}{ARCHIVE_SEP}{member}")
                try:
                    inner_size = os.path.getsize(inner_path)
                except OSError:
                    continue
                consider_file(inner_path, inner_size, f"{shown}{ARCHIVE_SEP}{member}", True, is_part10(inner_path))

    def scan_zip(path: str, shown: str) -> None:
        nonlocal archives, encrypted, nested, too_big, stopped_at_limit
        try:
            archive_file = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
            broken.append(f"{shown} ({type(exc).__name__})")
            return
        archives += 1
        with archive_file:
            for info in archive_file.infolist():
                if info.is_dir() or not _safe_member(info.filename):
                    continue
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    return
                if state.files_seen >= max_files:
                    stopped_at_limit = True
                    return
                state.files_seen += 1
                member_path = f"{path}{ARCHIVE_SEP}{info.filename}"
                member_shown = f"{shown}{ARCHIVE_SEP}{info.filename}"
                if state.files_seen % 25 == 0:
                    tick(member_shown)
                if info.flag_bits & 0x1:
                    encrypted += 1
                    continue
                if info.filename.lower().endswith(ARCHIVE_SUFFIXES):
                    nested += 1
                    continue
                if info.file_size > MAX_MEMBER_BYTES:
                    too_big += 1
                    continue
                try:
                    with archive_file.open(info) as handle:
                        head = handle.read(132)
                        part10 = head[128:132] == b"DICM"
                        if not part10 and not include_headerless:
                            result.skipped_files += 1
                            if len(result.skipped_examples) < MAX_EXAMPLES:
                                result.skipped_examples.append(member_shown)
                            continue
                    with archive_file.open(info) as handle:
                        ds = dcmread(handle, stop_before_pixels=True, force=not part10)
                except Exception:  # noqa: BLE001 - not DICOM, damaged, or an unsupported compression
                    result.skipped_files += 1
                    if len(result.skipped_examples) < MAX_EXAMPLES:
                        result.skipped_examples.append(member_shown)
                    continue
                index_dataset(ds, member_path, info.file_size, member_shown, True)
    for folder, dirs, files in walker:
        if skip_top and os.path.normcase(folder) == os.path.normcase(str(base)):
            dirs[:] = [name for name in dirs if name not in skip_top]  # names directly in the scanned folder to leave out
            files = [name for name in files if name not in skip_top]
        dirs.sort()
        for name in sorted(files):
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            if state.files_seen >= max_files:
                stopped_at_limit = True
                break
            state.files_seen += 1
            path = os.path.join(folder, name)
            if state.files_seen % 25 == 0:
                tick(os.path.relpath(path, base))
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            part10 = is_part10(path)
            lower = name.lower()
            if not part10 and lower.endswith(".zip"):
                scan_zip(path, os.path.relpath(path, base))
                if result.cancelled or stopped_at_limit:
                    break
                continue
            if not part10 and lower.endswith(".7z"):
                unpack_7z(path, os.path.relpath(path, base))
                if result.cancelled or stopped_at_limit:
                    break
                continue
            consider_file(path, size, os.path.relpath(path, base), False, part10)
        if result.cancelled or stopped_at_limit:
            break

    tick("")
    if archives:
        result.warnings.append(ScanWarning("info", f"{archives} ZIP file(s) were read in place. Nothing is unpacked to disk."))
    if unpacked_count:
        result.warnings.append(ScanWarning("info", f"{unpacked_count} 7z file(s) were unpacked to a temporary folder on this computer. It is deleted when you start over, after {30} minutes idle, and when the app starts."))
    if sevenz_missing:
        result.warnings.append(ScanWarning("warn", f"{sevenz_missing} 7z file(s) were skipped: 7z support is not installed in this copy of the app."))
    if sevenz_locked:
        result.warnings.append(ScanWarning("warn", f"{sevenz_locked} password-protected 7z file(s) cannot be read. They are left out."))
    if sevenz_big:
        result.warnings.append(ScanWarning("warn", f"{sevenz_big} 7z file(s) would unpack to more than {MAX_UNPACK_BYTES // (1024 ** 3)} GB and were left out."))
    if sevenz_no_room:
        result.warnings.append(ScanWarning("warn", f"{sevenz_no_room} 7z file(s) were left out: not enough free disk space to unpack them."))
    if sevenz_unreadable:
        result.warnings.append(ScanWarning("warn", f"{len(sevenz_unreadable)} 7z file(s) could not be unpacked (for example {sevenz_unreadable[0]})."))
    if broken:
        result.warnings.append(ScanWarning("warn", f"{len(broken)} ZIP file(s) could not be opened and were skipped (for example {broken[0]})."))
    if encrypted:
        result.warnings.append(ScanWarning("warn", f"{encrypted} password-protected file(s) inside ZIP files cannot be read. They are left out."))
    if nested:
        result.warnings.append(ScanWarning("info", f"{nested} archive file(s) inside a ZIP were not opened. Unpack those first."))
    if too_big:
        result.warnings.append(ScanWarning("warn", f"{too_big} file(s) inside ZIP files are larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB and are left out."))
    _mark_dicomdir_members(result, indexed)
    _finish_warnings(result, indexed, found, duplicates, no_uids, stopped_at_limit, max_files)
    result.files_seen = state.files_seen
    result.seconds = round(time.perf_counter() - started, 2)
    return result


def _add_instance(
    result: ScanResult,
    ds: Dataset,
    path: str,
    size: int,
    sop_class: str,
    sop_instance: str,
    study_uid: str,
    series_uid: str,
    transfer: str,
) -> None:
    patient_id = _text(ds, "PatientID")
    name = _text(ds, "PatientName")
    key = patient_id or f"(no Patient ID) {name}"
    patient = result.patients.setdefault(key, ImportedPatient(key=key, patient_id=patient_id))
    for values, value in ((patient.names, name), (patient.birth_dates, _text(ds, "PatientBirthDate")), (patient.sexes, _text(ds, "PatientSex"))):
        if value and value not in values:
            values.append(value)
    study = patient.studies.get(study_uid)
    if study is None:
        study = patient.studies[study_uid] = ImportedStudy(
            uid=study_uid,
            patient_key=key,
            date=_text(ds, "StudyDate"),
            time=_text(ds, "StudyTime"),
            description=_text(ds, "StudyDescription"),
            accession=_text(ds, "AccessionNumber"),
            study_id=_text(ds, "StudyID"),
            referring_physician=_text(ds, "ReferringPhysicianName"),
        )
    series = study.series.get(series_uid)
    if series is None:
        series = study.series[series_uid] = ImportedSeries(
            uid=series_uid,
            number=_int(ds, "SeriesNumber"),
            modality=_text(ds, "Modality"),
            description=_text(ds, "SeriesDescription"),
            body_part=_text(ds, "BodyPartExamined"),
        )
    series.instances.append(
        ImportedInstance(
            path=path,
            size=size,
            sop_class_uid=sop_class,
            sop_instance_uid=sop_instance,
            transfer_syntax_uid=transfer,
            number=_int(ds, "InstanceNumber"),
            frames=_int(ds, "NumberOfFrames") or 1,
        )
    )


def _mark_dicomdir_members(result: ScanResult, indexed: set[str]) -> None:
    for study in result.studies():
        for series in study.series.values():
            series.instances.sort(key=lambda item: (item.number is None, item.number or 0, item.path))
            for instance in series.instances:
                instance.in_dicomdir = os.path.normcase(os.path.abspath(instance.path)) in indexed


def _finish_warnings(
    result: ScanResult,
    indexed: set[str],
    found: set[str],
    duplicates: int,
    no_uids: int,
    stopped_at_limit: bool,
    max_files: int,
) -> None:
    add = result.warnings.append
    if not result.patients and not result.cancelled:
        add(ScanWarning("error", "No DICOM files were found in this folder."))
    if result.cancelled:
        add(ScanWarning("warn", "The scan was stopped. What is listed is only the part that was read."))
    if stopped_at_limit:
        add(ScanWarning("warn", f"The folder has more than {max_files:,} files; only the first {max_files:,} were read."))
    if no_uids:
        add(ScanWarning("error", f"{no_uids} DICOM file(s) lack a SOP Class, SOP Instance, Study or Series UID and cannot be sent. They are left out."))
    if duplicates:
        add(ScanWarning("warn", f"{duplicates} file(s) repeat a SOP Instance UID already seen. Only the first of each is kept."))
    if result.skipped_files:
        shown = ", ".join(result.skipped_examples)
        more = " …" if result.skipped_files > len(result.skipped_examples) else ""
        add(ScanWarning("info", f"{result.skipped_files} file(s) are not DICOM and were ignored (for example {shown}{more})."))
    if result.dicomdirs and indexed:
        missing = sorted(path for path in indexed if path not in found and not os.path.exists(path))
        if missing:
            add(ScanWarning("warn", f"The DICOMDIR lists {len(missing)} file(s) that are not on disk (for example {os.path.basename(missing[0])})."))
        unlisted = sum(
            1
            for study in result.studies()
            for series in study.series.values()
            for instance in series.instances
            if not instance.in_dicomdir and ARCHIVE_SEP not in instance.path
        )
        if unlisted:
            add(ScanWarning("info", f"{unlisted} DICOM file(s) are in the folder but not in the DICOMDIR. They are included."))
    for patient in result.patients.values():
        if len(patient.names) > 1:
            add(ScanWarning("warn", f"Patient ID {patient.patient_id or '(none)'} appears with {len(patient.names)} different names. Check it before sending."))
        if len(patient.birth_dates) > 1:
            add(ScanWarning("warn", f"Patient ID {patient.patient_id or '(none)'} appears with {len(patient.birth_dates)} different birth dates."))
    syntaxes = result.transfer_syntaxes()
    compressed = {uid: count for uid, count in syntaxes.items() if UID(uid).is_compressed}
    if compressed:
        total = sum(compressed.values())
        names = ", ".join(UID(uid).name for uid in compressed)
        add(ScanWarning("info", f"{total} image(s) are compressed ({names}). If the PACS does not accept that format they are decompressed on the way, which needs a decoder for it."))


# --- edits ---------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")
_DATE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_TIME = re.compile(r"^(\d{2}):?(\d{2})(?::?(\d{2}))?$")

PATIENT_FIELDS: dict[str, tuple[str, str, int]] = {
    # form field: (DICOM keyword, label, max length)
    "name": ("PatientName", "Patient name", 64),
    "patient_id": ("PatientID", "Patient ID", 64),
    "birth_date": ("PatientBirthDate", "Birth date", 8),
    "sex": ("PatientSex", "Sex", 1),
}
STUDY_FIELDS: dict[str, tuple[str, str, int]] = {
    "description": ("StudyDescription", "Study description", 64),
    "accession": ("AccessionNumber", "Accession number", 16),
    "date": ("StudyDate", "Study date", 8),
    "time": ("StudyTime", "Study time", 14),
    "referring_physician": ("ReferringPhysicianName", "Referring physician", 64),
    "study_id": ("StudyID", "Study ID", 16),
}


def normalise_value(kind: str, field_name: str, value: str) -> str:
    """The DICOM form of a typed value, or ImportProblem saying what is wrong."""
    table = PATIENT_FIELDS if kind == "patient" else STUDY_FIELDS
    keyword, label, limit = table[field_name]
    text = (value or "").strip()
    if _CONTROL.search(text):
        raise ImportProblem(f"{label}: remove backslashes and control characters.")
    if keyword in ("PatientBirthDate", "StudyDate"):
        if not text:
            return ""
        match = _DATE.match(text)
        if not match:
            raise ImportProblem(f"{label}: use a date like 2026-11-19 or 20261119.")
        year, month, day = (int(part) for part in match.groups())
        if not (1800 <= year <= 2200 and 1 <= month <= 12 and 1 <= day <= 31):
            raise ImportProblem(f"{label}: {text} is not a real date.")
        return f"{year:04d}{month:02d}{day:02d}"
    if keyword == "StudyTime":
        if not text:
            return ""
        match = _TIME.match(text)
        if not match:
            raise ImportProblem(f"{label}: use a time like 14:30 or 143000.")
        hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
        if hour > 23 or minute > 59 or second > 59:
            raise ImportProblem(f"{label}: {text} is not a real time.")
        return f"{hour:02d}{minute:02d}{second:02d}"
    if keyword == "PatientSex":
        text = text.upper()
        if text not in ("", "M", "F", "O"):
            raise ImportProblem(f"{label}: use M, F or O, or leave it empty.")
        return text
    if len(text) > limit:
        raise ImportProblem(f"{label}: at most {limit} characters (this is {len(text)}).")
    return text


@dataclass
class ImportEdits:
    """What to change. Only changed fields are present; nothing else is touched."""

    patients: dict[str, dict[str, str]] = field(default_factory=dict)  # patient key -> field -> value
    studies: dict[str, dict[str, str]] = field(default_factory=dict)  # study UID -> field -> value
    new_uids: bool = False  # give the studies, series and images new UIDs

    def is_empty(self) -> bool:
        return not (self.patients or self.studies or self.new_uids)

    def changed_count(self) -> int:
        return sum(len(v) for v in self.patients.values()) + sum(len(v) for v in self.studies.values())


def edits_from_form(scan: ScanResult, form: dict[str, str], new_uids: bool) -> tuple[ImportEdits, dict[str, str]]:
    """Compare the submitted values with what was scanned. Returns the edits and
    ``{form field name: message}`` for anything that does not validate."""
    edits = ImportEdits(new_uids=new_uids)
    errors: dict[str, str] = {}
    for index, patient in enumerate(scan.patients.values()):
        current = {"name": patient.name, "patient_id": patient.patient_id, "birth_date": patient.birth_date, "sex": patient.sex}
        for name, was in current.items():
            field_name = f"p{index}_{name}"
            if field_name not in form:
                continue
            try:
                value = normalise_value("patient", name, form[field_name])
            except ImportProblem as exc:
                errors[field_name] = str(exc)
                continue
            if value != was:
                edits.patients.setdefault(patient.key, {})[name] = value
    for index, study in enumerate(scan.studies()):
        current = {
            "description": study.description,
            "accession": study.accession,
            "date": study.date,
            "time": study.time,
            "referring_physician": study.referring_physician,
            "study_id": study.study_id,
        }
        for name, was in current.items():
            field_name = f"s{index}_{name}"
            if field_name not in form:
                continue
            try:
                value = normalise_value("study", name, form[field_name])
            except ImportProblem as exc:
                errors[field_name] = str(exc)
                continue
            if value != was:
                edits.studies.setdefault(study.uid, {})[name] = value
    return edits, errors


@dataclass
class UidMap:
    old_to_new: dict[str, str] = field(default_factory=dict)

    def get(self, uid: str) -> str:
        if uid not in self.old_to_new:
            self.old_to_new[uid] = str(generate_uid())
        return self.old_to_new[uid]


def build_uid_map(studies: Iterable[ImportedStudy]) -> UidMap:
    """New UIDs for every study, series and image chosen, so each keeps pointing at its parent."""
    mapping = UidMap()
    for study in studies:
        mapping.get(study.uid)
        for series in study.series.values():
            mapping.get(series.uid)
            for instance in series.instances:
                mapping.get(instance.sop_instance_uid)
    return mapping


def apply_edits(ds: Dataset, patient_key: str, study_uid: str, edits: ImportEdits, uids: UidMap | None) -> None:
    """Change one dataset in place: patient and study fields, then (optionally) every UID."""
    for field_name, value in edits.patients.get(patient_key, {}).items():
        setattr(ds, PATIENT_FIELDS[field_name][0], value)
    for field_name, value in edits.studies.get(study_uid, {}).items():
        setattr(ds, STUDY_FIELDS[field_name][0], value)
    if uids is not None:
        _remap_uids(ds, uids.old_to_new)
        meta = getattr(ds, "file_meta", None)
        if meta is not None and getattr(meta, "MediaStorageSOPInstanceUID", None) in uids.old_to_new:
            meta.MediaStorageSOPInstanceUID = uids.old_to_new[str(meta.MediaStorageSOPInstanceUID)]


def _remap_uids(ds: Dataset, mapping: dict[str, str]) -> None:
    """Replace every UID value that is one of ours, in the dataset and its sequences.

    References between objects (a report that points at its images) follow the
    images to their new UIDs. A UID that is not in the import is left alone.
    """
    for elem in ds:
        if elem.VR == "SQ":
            for item in elem.value:
                _remap_uids(item, mapping)
        elif elem.VR == "UI":
            if elem.VM > 1:
                elem.value = [mapping.get(str(v), str(v)) for v in elem.value]
            elif str(elem.value) in mapping:
                elem.value = mapping[str(elem.value)]


# --- sending -------------------------------------------------------------------


@dataclass
class SendFailure:
    path: str
    reason: str


@dataclass
class SendProgress:
    """Counts only. Updated by the sending thread, read by the page."""

    total: int = 0
    stored: int = 0
    warnings: int = 0
    failed: int = 0
    not_attempted: int = 0
    decompressed: int = 0
    bytes_sent: int = 0
    current: str = ""
    sent_paths: list[str] = field(default_factory=list)  # only when SendOptions.record_paths
    failures: list[SendFailure] = field(default_factory=list)
    message: str = ""  # a problem with the connection itself
    cancelled: bool = False
    done: bool = False
    seconds: float = 0.0

    @property
    def finished(self) -> int:
        return self.stored + self.failed + self.not_attempted

    @property
    def ok(self) -> bool:
        return self.done and not self.failed and not self.not_attempted and not self.message and not self.cancelled


@dataclass
class SendOptions:
    study_uids: set[str] | None = None  # None: every study
    decompress: bool = True  # decompress an image the PACS will not take as it is
    record_paths: bool = False  # keep which files were stored (the Router folder watch needs it; the Import page does not)


def _selected(scan: ScanResult, options: SendOptions) -> list[tuple[ImportedStudy, ImportedInstance]]:
    chosen: list[tuple[ImportedStudy, ImportedInstance]] = []
    for study in scan.studies():
        if options.study_uids is not None and study.uid not in options.study_uids:
            continue
        for series in sorted(study.series.values(), key=lambda s: (s.number is None, s.number or 0, s.uid)):
            chosen.extend((study, instance) for instance in series.instances)
    return chosen


def _contexts(items: list[tuple[ImportedStudy, ImportedInstance]]) -> dict[str, list[str]]:
    """Per SOP Class: the formats the files are in, then the two plain ones as a fallback."""
    wanted: dict[str, list[str]] = {}
    for _study, instance in items:
        syntaxes = wanted.setdefault(instance.sop_class_uid, [])
        if instance.transfer_syntax_uid not in syntaxes:
            syntaxes.append(instance.transfer_syntax_uid)
    for syntaxes in wanted.values():
        for plain in (ExplicitVRLittleEndian, ImplicitVRLittleEndian):
            if str(plain) not in syntaxes:
                syntaxes.append(str(plain))
    return wanted


def _associate(local: LocalAE, remote: RemoteNode, contexts: dict[str, list[str]]):
    ae = make_ae(local)
    for sop_class, syntaxes in contexts.items():
        ae.add_requested_context(sop_class, syntaxes)
    return ae.associate(remote.connect_host, remote.port, ae_title=remote.ae_title)


def _release(assoc) -> None:
    if assoc is not None and getattr(assoc, "is_established", False):
        try:
            assoc.release()
        except Exception:  # noqa: BLE001
            pass


def _store_one(assoc, ds: Dataset, decompress: bool) -> tuple[int | None, str, bool]:
    """Send one dataset. Returns (status code or None, reason, was decompressed)."""
    decompressed = False
    for attempt in (1, 2):
        try:
            status = assoc.send_c_store(ds)
        except ValueError as exc:
            compressed = UID(str(ds.file_meta.TransferSyntaxUID)).is_compressed
            if attempt == 1 and compressed and decompress and "presentation context" in str(exc).lower():
                try:
                    ds.decompress()
                except Exception as problem:  # noqa: BLE001
                    return None, f"The PACS does not take {UID(str(ds.file_meta.TransferSyntaxUID)).name}, and it could not be decompressed: {problem}", False
                decompressed = True
                continue
            return None, str(exc), decompressed
        except (AttributeError, RuntimeError) as exc:
            return None, str(exc), decompressed
        if not status:
            return None, "No C-STORE response (timeout, abort, or invalid PDU).", decompressed
        return int(status.Status), "", decompressed
    return None, "Could not send.", decompressed


def send_import(
    scan: ScanResult,
    edits: ImportEdits,
    local: LocalAE,
    remote: RemoteNode,
    options: SendOptions,
    progress: SendProgress,
    cancel: threading.Event | None = None,
) -> SendProgress:
    """Read, edit and C-STORE the chosen studies, one file at a time."""
    started = time.perf_counter()
    items = _selected(scan, options)
    progress.total = len(items)
    try:
        if not items:
            progress.message = "Nothing is selected to send."
            return progress
        contexts = _contexts(items)
        if len(contexts) > MAX_SOP_CLASSES:
            progress.message = f"The files use {len(contexts)} different SOP Classes; at most {MAX_SOP_CLASSES} can be sent at once."
            progress.not_attempted = len(items)
            return progress
        uids = build_uid_map(_unique_studies(items)) if edits.new_uids else None
        assoc = _associate(local, remote, contexts)
        if not getattr(assoc, "is_established", False) or not assoc.accepted_contexts:
            progress.message = "The PACS did not accept the association (wrong AE title, address or port, or it does not take these SOP Classes)."
            progress.not_attempted = len(items)
            return progress
        reconnects = RECONNECTS
        index = 0
        while index < len(items):
            if cancel is not None and cancel.is_set():
                progress.cancelled = True
                progress.not_attempted = len(items) - index
                break
            study, instance = items[index]
            progress.current = os.path.basename(instance.path)
            try:
                ds = read_dataset(instance.path)
            except Exception as exc:  # noqa: BLE001
                _fail(progress, instance, f"Could not read the file: {type(exc).__name__}: {exc}")
                index += 1
                continue
            apply_edits(ds, study.patient_key, study.uid, edits, uids)
            code, reason, was_decompressed = _store_one(assoc, ds, options.decompress)
            if code is None and reason.startswith("No C-STORE response") and reconnects > 0:
                # The connection dropped. Reconnect and send this file again, a limited number of times.
                reconnects -= 1
                _release(assoc)
                assoc = _associate(local, remote, contexts)
                if getattr(assoc, "is_established", False) and assoc.accepted_contexts:
                    continue
                progress.message = "The connection to the PACS was lost and could not be re-established."
                progress.not_attempted = len(items) - index
                break
            if code is None:
                _fail(progress, instance, reason)
            elif code == 0x0000:
                progress.stored += 1
                progress.bytes_sent += instance.size
                if options.record_paths:
                    progress.sent_paths.append(instance.path)
            elif 0xB000 <= code <= 0xBFFF:
                progress.stored += 1
                progress.warnings += 1
                progress.bytes_sent += instance.size
                if options.record_paths:
                    progress.sent_paths.append(instance.path)
            else:
                _fail(progress, instance, f"C-STORE status 0x{code:04X}")
            if was_decompressed and code is not None and (code == 0 or 0xB000 <= code <= 0xBFFF):
                progress.decompressed += 1
            index += 1
        _release(assoc)
    except Exception as exc:  # noqa: BLE001
        progress.message = f"{type(exc).__name__}: {exc}"
        progress.not_attempted = max(progress.total - progress.stored - progress.failed, 0)
    finally:
        close_sources()
        progress.current = ""
        progress.seconds = round(time.perf_counter() - started, 2)
        progress.done = True
    return progress


def _unique_studies(items: list[tuple[ImportedStudy, ImportedInstance]]) -> list[ImportedStudy]:
    seen: dict[str, ImportedStudy] = {}
    for study, _instance in items:
        seen.setdefault(study.uid, study)
    return list(seen.values())


def _fail(progress: SendProgress, instance: ImportedInstance, reason: str) -> None:
    progress.failed += 1
    if len(progress.failures) < MAX_FAILURES_LISTED:
        progress.failures.append(SendFailure(path=instance.path, reason=reason))
