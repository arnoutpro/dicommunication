"""Parse DICOM Structured Report Content Sequence. No language model."""

from __future__ import annotations

from typing import Any

from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.valuerep import PersonName
from pynetdicom.sop_class import (
    AcquisitionContextSRStorage,
    BasicTextSRStorage,
    ChestCADSRStorage,
    ColonCADSRStorage,
    Comprehensive3DSRStorage,
    ComprehensiveSRStorage,
    EnhancedSRStorage,
    EnhancedXRayRadiationDoseSRStorage,
    ExtensibleSRStorage,
    ImplantationPlanSRStorage,
    KeyObjectSelectionDocumentStorage,
    MammographyCADSRStorage,
    PatientRadiationDoseSRStorage,
    PerformedImagingAgentAdministrationSRStorage,
    PlannedImagingAgentAdministrationSRStorage,
    ProcedureLogStorage,
    RadiopharmaceuticalRadiationDoseSRStorage,
    SimplifiedAdultEchoSRStorage,
    WaveformAnnotationSRStorage,
    XRayRadiationDoseSRStorage,
)

SR_STORAGE_SOP_CLASSES: tuple[Any, ...] = (
    BasicTextSRStorage,
    EnhancedSRStorage,
    ComprehensiveSRStorage,
    Comprehensive3DSRStorage,
    ExtensibleSRStorage,
    ProcedureLogStorage,
    MammographyCADSRStorage,
    KeyObjectSelectionDocumentStorage,
    ChestCADSRStorage,
    XRayRadiationDoseSRStorage,
    RadiopharmaceuticalRadiationDoseSRStorage,
    ColonCADSRStorage,
    ImplantationPlanSRStorage,
    AcquisitionContextSRStorage,
    SimplifiedAdultEchoSRStorage,
    PatientRadiationDoseSRStorage,
    PlannedImagingAgentAdministrationSRStorage,
    PerformedImagingAgentAdministrationSRStorage,
    EnhancedXRayRadiationDoseSRStorage,
    WaveformAnnotationSRStorage,
)

SR_SOP_CLASS_UIDS = {str(uid) for uid in SR_STORAGE_SOP_CLASSES}
MAX_SR_ITEMS = 800


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, PersonName):
        return str(value).strip()
    if isinstance(value, MultiValue) or isinstance(value, (list, tuple)):
        return "\\".join(part for part in (_stringify(item) for item in value) if part)
    if isinstance(value, bytes):
        return value.decode("ascii", "replace").strip()
    return str(value).strip()


def _first_code(dataset: Dataset, keyword: str) -> tuple[str, str, str]:
    sequence = getattr(dataset, keyword, None) or []
    if not sequence:
        return "", "", ""
    first = sequence[0]
    return (
        _stringify(getattr(first, "CodeValue", None)),
        _stringify(getattr(first, "CodingSchemeDesignator", None)),
        _stringify(getattr(first, "CodeMeaning", None)),
    )


def _code_label(dataset: Dataset, keyword: str) -> str:
    value, _scheme, meaning = _first_code(dataset, keyword)
    return meaning or value


def _numeric_value(item: Dataset) -> str:
    measured = getattr(item, "MeasuredValueSequence", None) or []
    if measured:
        first = measured[0]
        number = _stringify(getattr(first, "NumericValue", None))
        units = _code_label(first, "MeasurementUnitsCodeSequence")
        return f"{number} {units}".strip()
    return _stringify(getattr(item, "NumericValue", None))


def _item_value(item: Dataset, value_type: str) -> str:
    kind = (value_type or "").strip().upper()
    if kind == "TEXT":
        return _stringify(getattr(item, "TextValue", None))
    if kind == "CODE":
        return _code_label(item, "ConceptCodeSequence")
    if kind == "NUM":
        return _numeric_value(item)
    if kind == "PNAME":
        return _stringify(getattr(item, "PersonName", None))
    if kind == "DATE":
        return _stringify(getattr(item, "Date", None))
    if kind == "TIME":
        return _stringify(getattr(item, "Time", None))
    if kind in {"DATETIME", "DT"}:
        return _stringify(getattr(item, "DateTime", None))
    if kind == "UIDREF":
        return _stringify(getattr(item, "UID", None))
    if kind in {"IMAGE", "COMPOSITE", "WAVEFORM"}:
        refs = getattr(item, "ReferencedSOPSequence", None) or []
        uids = [_stringify(getattr(ref, "ReferencedSOPInstanceUID", None)) for ref in refs]
        return "\\".join(uid for uid in uids if uid)
    return ""


def is_structured_report(dataset: Dataset) -> bool:
    sop = _stringify(getattr(dataset, "SOPClassUID", None))
    if sop in SR_SOP_CLASS_UIDS:
        return True
    modality = _stringify(getattr(dataset, "Modality", None)).upper()
    if modality == "SR" and hasattr(dataset, "ContentSequence"):
        return True
    return bool(getattr(dataset, "ContentSequence", None)) and bool(
        getattr(dataset, "ConceptNameCodeSequence", None)
    )


def document_title(dataset: Dataset) -> str:
    title = _code_label(dataset, "ConceptNameCodeSequence")
    if title:
        return title
    return _stringify(getattr(dataset, "SeriesDescription", None)) or "Structured Report"


def flatten_sr(dataset: Dataset, *, max_items: int = MAX_SR_ITEMS) -> list[dict[str, str | int]]:
    """Walk ContentSequence into concept / type / value rows."""
    items: list[dict[str, str | int]] = []

    def walk(sequence: Any, depth: int) -> None:
        if sequence is None or len(items) >= max_items:
            return
        for item in sequence:
            if len(items) >= max_items:
                return
            value_type = _stringify(getattr(item, "ValueType", None))
            name = _code_label(item, "ConceptNameCodeSequence") or value_type or "Item"
            code, scheme, _meaning = _first_code(item, "ConceptNameCodeSequence")
            coded = f"{scheme}:{code}" if code else ""
            items.append(
                {
                    "depth": depth,
                    "name": name,
                    "value_type": value_type,
                    "value": _item_value(item, value_type),
                    "code": coded,
                    "relationship": _stringify(getattr(item, "RelationshipType", None)),
                }
            )
            walk(getattr(item, "ContentSequence", None), depth + 1)

    walk(getattr(dataset, "ContentSequence", None), 0)
    return items


FINDINGS_NAMES = {"findings", "finding"}
FINDINGS_CODES = {"121070", "121071"}
IMPRESSION_NAMES = {"impression", "impressions"}
IMPRESSION_CODES = {"121073"}


def _item_code(item: dict[str, str | int]) -> str:
    code = str(item.get("code") or "").strip()
    if ":" in code:
        return code.split(":", 1)[1].strip()
    return code


def section_text(
    items: list[dict[str, str | int]],
    *,
    names: set[str],
    codes: set[str],
) -> str:
    """Collect TEXT/CODE/NUM values under matching concept names or codes."""
    wanted_names = {name.lower() for name in names}
    wanted_codes = {code.lower() for code in codes}
    chunks: list[str] = []
    index = 0
    while index < len(items):
        item = items[index]
        name = str(item.get("name") or "").strip().lower()
        code = _item_code(item).lower()
        if name not in wanted_names and code not in wanted_codes:
            index += 1
            continue
        depth = int(item.get("depth") or 0)
        values: list[str] = []
        own = str(item.get("value") or "").strip()
        if own:
            values.append(own)
        index += 1
        while index < len(items) and int(items[index].get("depth") or 0) > depth:
            nested = str(items[index].get("value") or "").strip()
            if nested:
                values.append(nested)
            index += 1
        if values:
            chunks.append("\n".join(values))
    return "\n".join(chunks).strip()


def prune_sections(
    items: list[dict[str, str | int]],
    *,
    names: set[str],
    codes: set[str],
) -> list[dict[str, str | int]]:
    """Drop concepts matching names/codes, and anything nested under them."""
    wanted_names = {name.lower() for name in names}
    wanted_codes = {code.lower() for code in codes}
    kept: list[dict[str, str | int]] = []
    skip_below_depth: int | None = None
    for item in items:
        depth = int(item.get("depth") or 0)
        if skip_below_depth is not None:
            if depth > skip_below_depth:
                continue
            skip_below_depth = None
        name = str(item.get("name") or "").strip().lower()
        code = _item_code(item).lower()
        if name in wanted_names or code in wanted_codes:
            skip_below_depth = depth
            continue
        kept.append(item)
    return kept


def sr_plain_text(items: list[dict[str, str | int]]) -> str:
    lines: list[str] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        depth = int(item.get("depth") or 0)
        indent = "  " * depth
        if value and name:
            lines.append(f"{indent}{name}: {value}")
        elif value:
            lines.append(f"{indent}{value}")
        elif name and str(item.get("value_type") or "").upper() == "CONTAINER":
            lines.append(f"{indent}{name}")
        elif name:
            lines.append(f"{indent}{name}")
    return "\n".join(lines).strip()


def parse_sr(dataset: Dataset) -> dict[str, Any]:
    items = flatten_sr(dataset)
    return {
        "document_title": document_title(dataset),
        "completion_flag": _stringify(getattr(dataset, "CompletionFlag", None)),
        "verification_flag": _stringify(getattr(dataset, "VerificationFlag", None)),
        "sop_class_uid": _stringify(getattr(dataset, "SOPClassUID", None)),
        "sop_instance_uid": _stringify(getattr(dataset, "SOPInstanceUID", None)),
        "study_instance_uid": _stringify(getattr(dataset, "StudyInstanceUID", None)),
        "series_instance_uid": _stringify(getattr(dataset, "SeriesInstanceUID", None)),
        "items": items,
        "text": sr_plain_text(items),
        "findings": section_text(items, names=FINDINGS_NAMES, codes=FINDINGS_CODES),
        "impression": section_text(items, names=IMPRESSION_NAMES, codes=IMPRESSION_CODES),
        "truncated": len(items) >= MAX_SR_ITEMS,
    }
