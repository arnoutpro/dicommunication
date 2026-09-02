"""Core anonymization transforms: nuke, fuzz, remove_patient, custom.

Every mode is applied through one `AnonBatch`, shared across every dataset
retrieved for a single Anonymize run. Study / Series / SOP Instance UIDs are
remapped through that batch's `UidMap` (old UID -> one fresh UID, generated
once and reused), so instances that shared a Study or Series Instance UID
before anonymization still share one afterwards — otherwise every instance
would land in its own orphan study once identifiers are scrubbed.

Nuke and fuzz both keep a small set of structural tags untouched (see
STRUCTURAL_KEYWORDS): without them the output is not a decodable DICOM
image, which would defeat the point of anonymizing a study rather than just
not retrieving it at all.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from typing import Any

from pydicom.datadict import dictionary_VR, tag_for_keyword
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from app.tools.anon_tags import IDENTIFYING_KEYWORDS, UID_KEYWORDS

MODES: tuple[str, ...] = ("nuke", "fuzz", "remove_patient", "custom")
MODE_LABELS: dict[str, str] = {
    "nuke": "Nuke all information",
    "fuzz": "Fuzz all data",
    "remove_patient": "Remove patient information",
    "custom": "Custom",
}
CUSTOM_ACTIONS: tuple[str, ...] = ("keep", "erase", "replace")
UID_CUSTOM_ACTIONS: tuple[str, ...] = ("keep", "fresh_uid")

# Tags every mode preserves so the output stays a decodable DICOM image
# instead of a blank file. UID-shaped tags are excluded here on purpose —
# they are always routed through UidMap, never copied or fuzzed as text.
STRUCTURAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "SOPClassUID",
        "Modality",
        "Rows",
        "Columns",
        "BitsAllocated",
        "BitsStored",
        "HighBit",
        "PixelRepresentation",
        "PhotometricInterpretation",
        "SamplesPerPixel",
        "PlanarConfiguration",
        "NumberOfFrames",
        "PixelData",
    }
)
_STRUCTURAL_TAGS: frozenset[int] = frozenset(
    tag for tag in (tag_for_keyword(keyword) for keyword in STRUCTURAL_KEYWORDS) if tag is not None
)
_UID_TAGS: dict[str, int] = {
    keyword: tag for keyword in UID_KEYWORDS if (tag := tag_for_keyword(keyword)) is not None
}


class UidMap:
    """Old UID string -> one fresh UID, stable for the lifetime of this map."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def get(self, old_uid: Any) -> str:
        old_uid = str(old_uid)
        fresh = self._map.get(old_uid)
        if fresh is None:
            fresh = generate_uid()
            self._map[old_uid] = fresh
        return fresh


def _random_text(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_uppercase, k=max(1, length)))


def _fuzz_value(vr: str, current: Any) -> Any:
    if vr == "DA":
        return "19000101"
    if vr == "TM":
        return "000000"
    if vr == "DT":
        return "19000101000000"
    if vr in {"US", "SS", "UL", "SL"}:
        return 0
    if vr in {"FL", "FD", "DS"}:
        return 0.0
    if vr == "IS":
        return "0"
    if vr == "PN":
        return f"{_random_text(6)}^{_random_text(4)}"
    length = len(str(current)) if current else 8
    return _random_text(max(4, min(length, 32)))


def _erase_value(vr: str) -> Any:
    if vr in {"US", "SS", "UL", "SL"}:
        return 0
    if vr in {"FL", "FD"}:
        return 0.0
    return ""


def _sync_file_meta(dataset: Dataset) -> Dataset:
    """After SOPInstanceUID changes, file_meta must match or save_as() writes
    a file whose header UID disagrees with the dataset's own SOPInstanceUID.
    """
    file_meta = getattr(dataset, "file_meta", None)
    if file_meta is not None and "SOPInstanceUID" in dataset:
        if "MediaStorageSOPInstanceUID" in file_meta or hasattr(file_meta, "MediaStorageSOPInstanceUID"):
            file_meta.MediaStorageSOPInstanceUID = str(dataset.SOPInstanceUID)
        if "SOPClassUID" in dataset and (
            "MediaStorageSOPClassUID" in file_meta or hasattr(file_meta, "MediaStorageSOPClassUID")
        ):
            file_meta.MediaStorageSOPClassUID = str(dataset.SOPClassUID)
    return dataset


def _remap_uids(dataset: Dataset, uid_map: UidMap) -> None:
    for keyword, tag in _UID_TAGS.items():
        if tag in dataset:
            dataset[tag].value = uid_map.get(dataset[tag].value)


def _nuke(dataset: Dataset, uid_map: UidMap) -> Dataset:
    fresh = Dataset()
    for tag in _STRUCTURAL_TAGS:
        if tag in dataset:
            fresh[tag] = dataset[tag]
    for keyword, tag in _UID_TAGS.items():
        if tag in dataset:
            vr = dictionary_VR(tag)
            fresh.add_new(tag, vr, uid_map.get(dataset[tag].value))
    fresh.file_meta = getattr(dataset, "file_meta", None)
    return _sync_file_meta(fresh)


def _fuzz(dataset: Dataset, uid_map: UidMap) -> Dataset:
    for elem in list(dataset):
        if elem.tag in _STRUCTURAL_TAGS or elem.keyword in UID_KEYWORDS or elem.VR == "SQ":
            continue
        try:
            elem.value = _fuzz_value(elem.VR, elem.value)
        except Exception:  # noqa: BLE001 — a handful of VR/value shapes aren't worth failing the run over
            continue
    _remap_uids(dataset, uid_map)
    return _sync_file_meta(dataset)


def _remove_patient(dataset: Dataset, uid_map: UidMap, *, erase: bool) -> Dataset:
    for keyword in IDENTIFYING_KEYWORDS:
        tag = tag_for_keyword(keyword)
        if tag is None or tag not in dataset:
            continue
        elem = dataset[tag]
        try:
            elem.value = _erase_value(elem.VR) if erase else _fuzz_value(elem.VR, elem.value)
        except Exception:  # noqa: BLE001
            continue
    _remap_uids(dataset, uid_map)
    return _sync_file_meta(dataset)


def _custom(dataset: Dataset, uid_map: UidMap, actions: dict[str, dict[str, str]]) -> Dataset:
    """actions: {keyword: {"action": "keep"|"erase"|"replace"|"fresh_uid", "value": str}}.
    A keyword not mentioned, or set to "keep", is left exactly as retrieved.
    """
    for keyword, spec in actions.items():
        action = str((spec or {}).get("action") or "keep")
        if action == "keep":
            continue
        tag = tag_for_keyword(keyword)
        if tag is None or tag not in dataset:
            continue
        elem = dataset[tag]
        if action == "fresh_uid" and keyword in UID_KEYWORDS:
            elem.value = uid_map.get(elem.value)
        elif action == "erase" and keyword not in UID_KEYWORDS:
            try:
                elem.value = _erase_value(elem.VR)
            except Exception:  # noqa: BLE001
                continue
        elif action == "replace" and keyword not in UID_KEYWORDS:
            elem.value = str((spec or {}).get("value") or "")
    return _sync_file_meta(dataset)


@dataclass
class AnonBatch:
    """One Anonymize run's shared UID remapping."""

    uid_map: UidMap = field(default_factory=UidMap)

    def anonymize(
        self,
        dataset: Dataset,
        mode: str,
        *,
        remove_patient_erase: bool = True,
        custom_actions: dict[str, dict[str, str]] | None = None,
    ) -> Dataset:
        if mode == "nuke":
            return _nuke(dataset, self.uid_map)
        if mode == "fuzz":
            return _fuzz(dataset, self.uid_map)
        if mode == "remove_patient":
            return _remove_patient(dataset, self.uid_map, erase=remove_patient_erase)
        if mode == "custom":
            return _custom(dataset, self.uid_map, custom_actions or {})
        raise ValueError(f"Unknown anonymization mode: {mode!r}")
