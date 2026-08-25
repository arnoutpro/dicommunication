"""Collect PDFs and wrap them as Encapsulated PDF Storage datasets."""

from __future__ import annotations

import base64
import io
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


def collect_from_directory(path: str | Path) -> tuple[list[PdfSource], list[str]]:
    raw = str(path).strip()
    if not raw:
        return [], []
    root = Path(raw).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise CollectError(f"Path not found: {raw}") from exc
    files: list[Path]
    if root.is_file():
        files = [root]
    elif root.is_dir():
        if root.parent == root:
            raise CollectError("Refusing to scan the filesystem root. Choose a reports folder.")
        files = sorted(
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file() and not candidate.is_symlink()
        )
    else:
        raise CollectError(f"Not a file or directory: {raw}")

    sources: list[PdfSource] = []
    warnings: list[str] = []
    for file in files:
        try:
            relative = file.relative_to(root if root.is_dir() else root.parent)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if len(relative.parts) > MAX_DIR_DEPTH + 1:
            continue
        if not _is_pdf_name(file.name):
            continue
        try:
            with file.open("rb") as handle:
                data = _read_limited(handle, MAX_PDF_BYTES)
        except OSError as exc:
            warnings.append(f"{file.name}: {exc}")
            continue
        _add_source(sources, warnings, name=file.name, data=data, origin=f"dir:{file}")
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


def encapsulate_sources(
    sources: list[PdfSource],
    *,
    patient_name: str,
    patient_id: str,
    accession_number: str = "",
    study_description: str = "",
    document_title: str = "",
    same_study: bool = True,
) -> list[Dataset]:
    study_uid = generate_uid() if same_study else None
    series_uid = generate_uid() if same_study else None
    datasets: list[Dataset] = []
    for index, source in enumerate(sources, start=1):
        title = document_title
        if title and len(sources) > 1:
            title = f"{title} — {Path(source.name).stem}"
        datasets.append(
            encapsulate_pdf(
                source,
                patient_name=patient_name,
                patient_id=patient_id,
                accession_number=accession_number,
                study_description=study_description,
                document_title=title,
                study_uid=study_uid,
                series_uid=series_uid,
                instance_number=index,
            )
        )
    return datasets
