"""Collect PDFs and wrap them as Encapsulated PDF Storage datasets."""

from __future__ import annotations

import base64
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    PYDICOM_IMPLEMENTATION_UID,
    ExplicitVRLittleEndian,
    generate_uid,
)
from pynetdicom.sop_class import EncapsulatedPDFStorage

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_ZIP_BYTES = 40 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED = 80 * 1024 * 1024
MAX_FILES = 40
MAX_DIR_DEPTH = 3
PDF_MAGIC = b"%PDF"


class CollectError(ValueError):
    """Fatal problem while gathering PDFs (missing path, bad zip, over the cap)."""


@dataclass(frozen=True)
class PdfSource:
    name: str
    data: bytes
    origin: str = "upload"


def is_pdf(data: bytes) -> bool:
    return bool(data) and data[:4] == PDF_MAGIC


def _read_limited(stream: Any, limit: int) -> bytes:
    return stream.read(limit + 1)


def _too_large(data: bytes, limit: int) -> bool:
    return len(data) > limit


def _safe_zip_basename(name: str) -> str | None:
    raw = name.replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or ".." in parts:
        return None
    if raw.startswith("/") or raw.startswith("../") or "/../" in f"/{raw}/":
        return None
    if any(part == "__MACOSX" or part.startswith("._") for part in parts):
        return None
    return parts[-1]


def _is_pdf_name(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _decode_b64(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    text = str(value or "").strip()
    if not text:
        return b""
    return base64.b64decode(text, validate=False)


def _add_source(
    sources: list[PdfSource],
    warnings: list[str],
    *,
    name: str,
    data: bytes,
    origin: str,
) -> None:
    if len(sources) >= MAX_FILES:
        raise CollectError(f"Too many PDFs (max {MAX_FILES}).")
    label = name or "document.pdf"
    if _too_large(data, MAX_PDF_BYTES):
        warnings.append(f"{label}: larger than {MAX_PDF_BYTES // (1024 * 1024)} MB, skipped.")
        return
    if not is_pdf(data):
        warnings.append(f"{label}: not a PDF (missing %PDF header), skipped.")
        return
    sources.append(PdfSource(name=label, data=data, origin=origin))


def collect_from_zip(payload: bytes) -> tuple[list[PdfSource], list[str]]:
    if _too_large(payload, MAX_ZIP_BYTES):
        raise CollectError(f"ZIP is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise CollectError("Not a valid ZIP archive.") from exc
    sources: list[PdfSource] = []
    warnings: list[str] = []
    uncompressed = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                warnings.append(f"{info.filename}: encrypted ZIP entry, skipped.")
                continue
            basename = _safe_zip_basename(info.filename)
            if basename is None:
                warnings.append(f"{info.filename}: skipped (unsafe path).")
                continue
            if not _is_pdf_name(basename):
                continue
            size = int(info.file_size or 0)
            if size > MAX_PDF_BYTES:
                warnings.append(f"{basename}: larger than {MAX_PDF_BYTES // (1024 * 1024)} MB, skipped.")
                continue
            uncompressed += size
            if uncompressed > MAX_ZIP_UNCOMPRESSED:
                raise CollectError(
                    f"ZIP uncompressed size exceeds {MAX_ZIP_UNCOMPRESSED // (1024 * 1024)} MB."
                )
            with archive.open(info, "r") as handle:
                data = _read_limited(handle, MAX_PDF_BYTES)
            _add_source(sources, warnings, name=basename, data=data, origin=f"zip:{info.filename}")
    return sources, warnings


def _resolve_import_root(path: str | Path) -> Path:
    raw = str(path).strip()
    if not raw:
        raise CollectError("Type a directory or use … to browse.")
    root = Path(raw).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise CollectError(f"Path not found: {raw}") from exc
    if root.is_file():
        return root
    if root.is_dir():
        if root.parent == root:
            raise CollectError("Refusing to scan the filesystem root. Choose a reports folder.")
        return root
    raise CollectError(f"Not a file or directory: {raw}")


def iter_directory_pdfs(path: str | Path) -> tuple[Path, list[tuple[Path, str]]]:
    """Return (root, [(file, relative path), ...]) for PDF names under path."""
    root = _resolve_import_root(path)
    files: list[Path]
    base = root if root.is_dir() else root.parent
    if root.is_file():
        files = [root]
    else:
        files = sorted(
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file() and not candidate.is_symlink()
        )
    hits: list[tuple[Path, str]] = []
    for file in files:
        try:
            relative = file.relative_to(base)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if len(relative.parts) > MAX_DIR_DEPTH + 1:
            continue
        if not _is_pdf_name(file.name):
            continue
        hits.append((file, relative.as_posix()))
    return root, hits


def list_directory_pdfs(path: str | Path) -> dict[str, Any]:
    """Count PDFs in a folder without loading their bytes (for the Scan button)."""
    root, hits = iter_directory_pdfs(path)
    oversize = 0
    files: list[dict[str, Any]] = []
    for file, relative in hits:
        try:
            size = file.stat().st_size
        except OSError:
            size = 0
        too_big = size > MAX_PDF_BYTES
        if too_big:
            oversize += 1
        files.append(
            {
                "name": file.name,
                "relative": relative,
                "size": size,
                "too_big": too_big,
            }
        )
    pdf_count = len(files)
    sendable = sum(1 for item in files if not item["too_big"])
    return {
        "ok": True,
        "path": str(root),
        "pdf_count": pdf_count,
        "sendable": min(sendable, MAX_FILES),
        "capped": sendable > MAX_FILES,
        "oversize": oversize,
        "max_files": MAX_FILES,
        "files": files[:MAX_FILES],
        "extra": max(0, pdf_count - MAX_FILES),
    }


def collect_from_directory(path: str | Path) -> tuple[list[PdfSource], list[str]]:
    if not str(path).strip():
        return [], []
    root, hits = iter_directory_pdfs(path)
    sources: list[PdfSource] = []
    warnings: list[str] = []
    for file, relative in hits:
        try:
            with file.open("rb") as handle:
                data = _read_limited(handle, MAX_PDF_BYTES)
        except OSError as exc:
            warnings.append(f"{file.name}: {exc}")
            continue
        _add_source(sources, warnings, name=file.name, data=data, origin=f"dir:{file}")
    if root and not hits and not sources:
        warnings.append(f"No PDF files under {root}.")
    return sources, warnings


def collect_from_uploads(items: Iterable[dict[str, Any]]) -> tuple[list[PdfSource], list[str]]:
    sources: list[PdfSource] = []
    warnings: list[str] = []
    for item in items:
        name = str(item.get("filename") or item.get("name") or "document.pdf")
        if "content" in item and item["content"] is not None:
            data = item["content"]
            if not isinstance(data, (bytes, bytearray)):
                data = bytes(data)
        elif item.get("content_b64"):
            try:
                data = _decode_b64(item.get("content_b64"))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{name}: invalid base64 ({exc}).")
                continue
        else:
            continue
        origin = str(item.get("origin") or "upload")
        _add_source(sources, warnings, name=name, data=bytes(data), origin=origin)
    return sources, warnings


def collect_pdfs(options: dict[str, Any] | None) -> tuple[list[PdfSource], list[str]]:
    options = options or {}
    sources: list[PdfSource] = []
    warnings: list[str] = []

    uploads = options.get("pdfs") or []
    if uploads:
        found, notes = collect_from_uploads(uploads)
        sources.extend(found)
        warnings.extend(notes)

    zip_payload = options.get("zip_bytes")
    if zip_payload is None and options.get("zip_b64"):
        try:
            zip_payload = _decode_b64(options.get("zip_b64"))
        except Exception as exc:  # noqa: BLE001
            raise CollectError(f"ZIP base64 is invalid: {exc}") from exc
    if zip_payload:
        if not isinstance(zip_payload, (bytes, bytearray)):
            raise CollectError("ZIP payload must be bytes.")
        found, notes = collect_from_zip(bytes(zip_payload))
        sources.extend(found)
        warnings.extend(notes)

    directory = str(options.get("directory") or "").strip()
    if directory:
        found, notes = collect_from_directory(directory)
        sources.extend(found)
        warnings.extend(notes)

    if len(sources) > MAX_FILES:
        raise CollectError(f"Too many PDFs (max {MAX_FILES}).")
    return sources, warnings


def _dicom_lo(value: str, length: int = 64) -> str:
    return value.strip()[:length]


def _even_bytes(data: bytes) -> bytes:
    if len(data) % 2:
        return data + b"\x00"
    return data


def encapsulate_pdf(
    source: PdfSource,
    *,
    patient_name: str,
    patient_id: str,
    accession_number: str = "",
    study_description: str = "",
    document_title: str = "",
    study_uid: str | None = None,
    series_uid: str | None = None,
    instance_number: int = 1,
) -> Dataset:
    now = datetime.now(timezone.utc)
    sop_uid = generate_uid()
    title = _dicom_lo(document_title or Path(source.name).stem or "PDF")
    body = _even_bytes(source.data)

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.FileMetaInformationVersion = b"\x00\x01"
    ds.file_meta.MediaStorageSOPClassUID = EncapsulatedPDFStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    ds.file_meta.ImplementationVersionName = "DICOMM"

    ds.SOPClassUID = EncapsulatedPDFStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid or generate_uid()
    ds.SeriesInstanceUID = series_uid or generate_uid()
    ds.PatientName = _dicom_lo(patient_name)
    ds.PatientID = _dicom_lo(patient_id, 64)
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.StudyID = "PDF"
    ds.AccessionNumber = _dicom_lo(accession_number, 16)
    ds.ReferringPhysicianName = ""
    ds.StudyDescription = _dicom_lo(study_description)
    ds.Modality = "DOC"
    ds.SeriesNumber = 1
    ds.SeriesDescription = "Encapsulated PDF"
    ds.InstanceNumber = int(instance_number)
    ds.Manufacturer = "Arnout.pro"
    ds.ManufacturerModelName = "Dicommunication"
    ds.InstitutionName = "Dicommunication Tool"
    ds.ConversionType = "WSD"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = body
    ds.DocumentTitle = title
    ds.BurnedInAnnotation = "YES"
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.ConceptNameCodeSequence = Sequence()
    text = f"{ds.PatientName}{ds.PatientID}{title}{ds.StudyDescription}"
    if not text.isascii():
        ds.SpecificCharacterSet = "ISO_IR 192"
    return ds


def new_batch_patient() -> tuple[str, str]:
    token = uuid.uuid4().hex[:8].upper()
    return f"ARNPRO^PDF{token[:4]}", f"PDF{token}"


def _slug_stem(name: str, index: int) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^A-Za-z0-9]", "", stem.upper())[:16]
    return slug or f"{index:03d}"


def unique_patient_for(source: PdfSource, index: int, used_ids: set[str]) -> tuple[str, str]:
    stem = Path(source.name).stem.strip() or f"PDF{index:03d}"
    name_token = _dicom_lo(re.sub(r"\s+", " ", stem) or f"PDF{index:03d}", 48)
    patient_id = f"PDF{_slug_stem(source.name, index)}"
    if patient_id in used_ids:
        patient_id = f"{patient_id}{index:03d}"
    used_ids.add(patient_id)
    return f"PDF^{name_token}", _dicom_lo(patient_id, 64)


def resolve_patient_identities(
    sources: list[PdfSource],
    *,
    patient_name: str,
    patient_id: str,
    generate_name: bool = False,
    generate_id: bool = False,
    unique_patient: bool = False,
) -> list[tuple[str, str]]:
    if unique_patient:
        generate_name = True
        generate_id = True
    batch_name, batch_id = new_batch_patient()
    name = patient_name.strip() or (batch_name if generate_name else "")
    pid = patient_id.strip() or (batch_id if generate_id else "")
    if unique_patient:
        used: set[str] = set()
        return [unique_patient_for(source, index, used) for index, source in enumerate(sources, start=1)]
    if generate_name and not patient_name.strip():
        name = batch_name
    if generate_id and not patient_id.strip():
        pid = batch_id
    if not name or not pid:
        raise CollectError("Patient Name and Patient ID are required, or enable Generate.")
    return [(name, pid) for _ in sources]


def encapsulate_sources(
    sources: list[PdfSource],
    *,
    patient_name: str,
    patient_id: str,
    accession_number: str = "",
    study_description: str = "",
    document_title: str = "",
    same_study: bool = True,
    identities: list[tuple[str, str]] | None = None,
) -> list[Dataset]:
    study_uid = generate_uid() if same_study else None
    series_uid = generate_uid() if same_study else None
    datasets: list[Dataset] = []
    for index, source in enumerate(sources, start=1):
        title = document_title
        if title and len(sources) > 1:
            title = f"{title} — {Path(source.name).stem}"
        if identities:
            name, pid = identities[index - 1]
        else:
            name, pid = patient_name, patient_id
        datasets.append(
            encapsulate_pdf(
                source,
                patient_name=name,
                patient_id=pid,
                accession_number=accession_number,
                study_description=study_description,
                document_title=title,
                study_uid=study_uid,
                series_uid=series_uid,
                instance_number=index,
            )
        )
    return datasets
