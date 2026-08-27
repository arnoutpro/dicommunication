"""Study Root C-FIND keys at STUDY, SERIES, and IMAGE.

Catalog follows DICOM Q/R required/unique keys plus optional keys commonly
offered by PACS (including Philips Vue PACS 12.2.8). Hierarchical FIND is
assumed: no relational queries. Series keys need Study Instance UID; image
keys need Study Instance UID and Series Instance UID.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from pydicom.datadict import dictionary_VR, tag_for_keyword
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.tag import Tag
from pydicom.valuerep import PersonName

LEVELS = ("STUDY", "SERIES", "IMAGE")
LEVEL_LABELS = {"STUDY": "Study", "SERIES": "Series", "IMAGE": "Image"}
LEVEL_PARENTS: dict[str, tuple[str, ...]] = {
    "STUDY": (),
    "SERIES": ("StudyInstanceUID",),
    "IMAGE": ("StudyInstanceUID", "SeriesInstanceUID"),
}
GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "patient", "label": "Patient", "experimental": False, "hint": ""},
    {"id": "study", "label": "Study", "experimental": False, "hint": ""},
    {
        "id": "vue-study",
        "label": "Vue PACS · Study (ELSCINT1)",
        "experimental": True,
        "hint": (
            "Tamar / ELSCINT1 private study tags. Vue’s C-FIND DCS does not list them; "
            "unsupported fields are omitted. Grid Token sequences are not sent. Off by default."
        ),
    },
    {"id": "series", "label": "Series", "experimental": False, "hint": ""},
    {
        "id": "vue-series",
        "label": "Vue PACS · Series (ELSCINT1)",
        "experimental": True,
        "hint": (
            "Tamar / ELSCINT1 private series tags. Same rule: Vue may ignore them on C-FIND. Off by default."
        ),
    },
    {"id": "image", "label": "Image", "experimental": False, "hint": ""},
)
VUE_CREATOR = "ELSCINT1"
MAX_RECORDS = 2000
HTML_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HTML_DATE_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})$")
HTML_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$")


@dataclass(frozen=True)
class FindKey:
    keyword: str
    tag: str
    label: str
    vr: str
    group: str
    levels: tuple[str, ...]
    role: str = "optional"
    placeholder: str = ""
    hint: str = ""
    default_return: bool = True
    requires: tuple[str, ...] = ()
    modality_in: tuple[str, ...] = ()
    private_creator: str = ""


def _key(
    keyword: str,
    tag: str,
    label: str,
    vr: str,
    group: str,
    *levels: str,
    role: str = "optional",
    placeholder: str = "",
    hint: str = "",
    default_return: bool = True,
    requires: tuple[str, ...] = (),
    modality_in: tuple[str, ...] = (),
    private_creator: str = "",
) -> FindKey:
    return FindKey(
        keyword=keyword,
        tag=tag,
        label=label,
        vr=vr,
        group=group,
        levels=levels,
        role=role,
        placeholder=placeholder,
        hint=hint,
        default_return=default_return,
        requires=requires,
        modality_in=modality_in,
        private_creator=private_creator,
    )


def _vue(
    keyword: str,
    tag: str,
    label: str,
    vr: str,
    group: str,
    *levels: str,
    placeholder: str = "",
    requires: tuple[str, ...] = (),
) -> FindKey:
    return _key(
        keyword,
        tag,
        label,
        vr,
        group,
        *levels,
        placeholder=placeholder,
        hint="Vue / ELSCINT1 private tag. Not in the DCS C-FIND table; the SCP may omit it.",
        default_return=False,
        requires=requires,
        private_creator=VUE_CREATOR,
    )


SERIES_CHILD = ("StudyInstanceUID",)
IMAGE_CHILD = ("StudyInstanceUID", "SeriesInstanceUID")
SERIES_UID = ("StudyInstanceUID",)

KEYS: tuple[FindKey, ...] = (
    _key(
        "PatientName",
        "(0010,0010)",
        "Patient Name",
        "PN",
        "patient",
        "STUDY",
        role="required",
        placeholder="DOE*",
        hint="Person name. Literal, case-insensitive on Vue. Wildcards * and ?.",
    ),
    _key(
        "PatientID",
        "(0010,0020)",
        "Patient ID",
        "LO",
        "patient",
        "STUDY",
        role="unique",
        placeholder="1001",
    ),
    _key(
        "PatientBirthDate",
        "(0010,0030)",
        "Patient Birth Date",
        "DA",
        "patient",
        "STUDY",
        placeholder="YYYYMMDD",
    ),
    _key(
        "PatientSex",
        "(0010,0040)",
        "Patient Sex",
        "CS",
        "patient",
        "STUDY",
        placeholder="M / F / O",
    ),
    _key(
        "IssuerOfPatientID",
        "(0010,0021)",
        "Issuer of Patient ID",
        "LO",
        "patient",
        "STUDY",
        default_return=False,
    ),
    _key(
        "NumberOfPatientRelatedStudies",
        "(0020,1200)",
        "Number of Patient Related Studies",
        "IS",
        "patient",
        "STUDY",
        role="optional",
        default_return=False,
        hint="Usually a return key. Leave empty.",
    ),
    _key(
        "NumberOfPatientRelatedSeries",
        "(0020,1202)",
        "Number of Patient Related Series",
        "IS",
        "patient",
        "STUDY",
        default_return=False,
    ),
    _key(
        "NumberOfPatientRelatedInstances",
        "(0020,1204)",
        "Number of Patient Related Images",
        "IS",
        "patient",
        "STUDY",
        default_return=False,
    ),
    _key(
        "StudyDate",
        "(0008,0020)",
        "Study Date",
        "DA",
        "study",
        "STUDY",
        role="required",
        placeholder="YYYYMMDD or YYYYMMDD-YYYYMMDD",
        hint="Single day or a DICOM date range.",
    ),
    _key(
        "StudyTime",
        "(0008,0030)",
        "Study Time",
        "TM",
        "study",
        "STUDY",
        role="required",
        placeholder="HHMMSS",
        default_return=False,
    ),
    _key(
        "AccessionNumber",
        "(0008,0050)",
        "Accession Number",
        "SH",
        "study",
        "STUDY",
        role="required",
        placeholder="ACC1",
    ),
    _key(
        "ReferringPhysicianName",
        "(0008,0090)",
        "Referring Physician",
        "PN",
        "study",
        "STUDY",
        placeholder="SMITH*",
    ),
    _key(
        "StudyDescription",
        "(0008,1030)",
        "Study Description",
        "LO",
        "study",
        "STUDY",
        placeholder="CHEST",
    ),
    _key(
        "ModalitiesInStudy",
        "(0008,0061)",
        "Modalities in Study",
        "CS",
        "study",
        "STUDY",
        placeholder="CT",
        hint="Study-level modality list. Use Modality at Series.",
    ),
    _key(
        "StudyID",
        "(0020,0010)",
        "Study ID",
        "SH",
        "study",
        "STUDY",
        role="required",
        default_return=False,
    ),
    _key(
        "StudyInstanceUID",
        "(0020,000D)",
        "Study Instance UID",
        "UI",
        "study",
        "STUDY",
        "SERIES",
        "IMAGE",
        role="unique",
        placeholder="1.2.840…",
        hint="Unique study key. Required to query Series or Image.",
    ),
    _key(
        "NumberOfStudyRelatedSeries",
        "(0020,1206)",
        "Number of Study Related Series",
        "IS",
        "study",
        "STUDY",
        default_return=False,
    ),
    _key(
        "NumberOfStudyRelatedInstances",
        "(0020,1208)",
        "Number of Study Related Images",
        "IS",
        "study",
        "STUDY",
        default_return=False,
    ),
    _key(
        "Modality",
        "(0008,0060)",
        "Modality",
        "CS",
        "series",
        "SERIES",
        role="required",
        placeholder="CT",
        requires=SERIES_CHILD,
        hint="Series modality. Unlocks MR-only series keys when set to MR.",
    ),
    _key(
        "SeriesDate",
        "(0008,0021)",
        "Series Date",
        "DA",
        "series",
        "SERIES",
        placeholder="YYYYMMDD",
        requires=SERIES_CHILD,
    ),
    _key(
        "SeriesTime",
        "(0008,0031)",
        "Series Time",
        "TM",
        "series",
        "SERIES",
        placeholder="HHMMSS",
        default_return=False,
        requires=SERIES_CHILD,
    ),
    _key(
        "SeriesDescription",
        "(0008,103E)",
        "Series Description",
        "LO",
        "series",
        "SERIES",
        placeholder="AXIAL",
        requires=SERIES_CHILD,
    ),
    _key(
        "BodyPartExamined",
        "(0018,0015)",
        "Body Part Examined",
        "CS",
        "series",
        "SERIES",
        placeholder="CHEST",
        requires=SERIES_CHILD,
        hint="Series matching key on Vue. Not searchable archive-wide without a Study UID.",
    ),
    _key(
        "RepetitionTime",
        "(0018,0080)",
        "Repetition Time",
        "DS",
        "series",
        "SERIES",
        default_return=False,
        requires=SERIES_CHILD,
        modality_in=("MR",),
        hint="MR series key. Available when Modality is MR or empty.",
    ),
    _key(
        "SeriesInstanceUID",
        "(0020,000E)",
        "Series Instance UID",
        "UI",
        "series",
        "SERIES",
        "IMAGE",
        role="unique",
        placeholder="1.2.840…",
        requires=SERIES_UID,
        hint="Unique series key. Required (with Study UID) to query Image.",
    ),
    _key(
        "SeriesNumber",
        "(0020,0011)",
        "Series Number",
        "IS",
        "series",
        "SERIES",
        role="required",
        placeholder="1",
        requires=SERIES_CHILD,
    ),
    _key(
        "NumberOfSeriesRelatedInstances",
        "(0020,1209)",
        "Number of Series Related Images",
        "IS",
        "series",
        "SERIES",
        default_return=False,
        requires=SERIES_CHILD,
    ),
    _key(
        "SOPClassUID",
        "(0008,0016)",
        "SOP Class UID",
        "UI",
        "image",
        "IMAGE",
        placeholder="1.2.840.10008.5.1.4.1.1…",
        requires=IMAGE_CHILD,
    ),
    _key(
        "SOPInstanceUID",
        "(0008,0018)",
        "SOP Instance UID",
        "UI",
        "image",
        "IMAGE",
        role="unique",
        placeholder="1.2.840…",
        requires=IMAGE_CHILD,
    ),
    _key(
        "InstanceCreationDate",
        "(0008,0012)",
        "Instance Creation Date",
        "DA",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "InstanceCreationTime",
        "(0008,0013)",
        "Instance Creation Time",
        "TM",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "AcquisitionDate",
        "(0008,0022)",
        "Acquisition Date",
        "DA",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "ContentDate",
        "(0008,0023)",
        "Image Date",
        "DA",
        "image",
        "IMAGE",
        placeholder="YYYYMMDD",
        requires=IMAGE_CHILD,
    ),
    _key(
        "AcquisitionTime",
        "(0008,0032)",
        "Acquisition Time",
        "TM",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "ContentTime",
        "(0008,0033)",
        "Image Time",
        "TM",
        "image",
        "IMAGE",
        placeholder="HHMMSS",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "ImageType",
        "(0008,0008)",
        "Image Type",
        "CS",
        "image",
        "IMAGE",
        placeholder="ORIGINAL\\PRIMARY",
        requires=IMAGE_CHILD,
    ),
    _key(
        "InstanceNumber",
        "(0020,0013)",
        "Instance Number",
        "IS",
        "image",
        "IMAGE",
        role="required",
        placeholder="1",
        requires=IMAGE_CHILD,
    ),
    _key(
        "FrameOfReferenceUID",
        "(0020,0052)",
        "Frame of Reference UID",
        "UI",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "SliceLocation",
        "(0020,1041)",
        "Slice Location",
        "DS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "SamplesPerPixel",
        "(0028,0002)",
        "Samples per Pixel",
        "US",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "NumberOfFrames",
        "(0028,0008)",
        "Number of Frames",
        "IS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "Rows",
        "(0028,0010)",
        "Rows",
        "US",
        "image",
        "IMAGE",
        requires=IMAGE_CHILD,
    ),
    _key(
        "Columns",
        "(0028,0011)",
        "Columns",
        "US",
        "image",
        "IMAGE",
        requires=IMAGE_CHILD,
    ),
    _key(
        "BitsAllocated",
        "(0028,0100)",
        "Bits Allocated",
        "US",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "ContrastBolusAgent",
        "(0018,0010)",
        "Contrast Bolus Agent",
        "LO",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "ScanningSequence",
        "(0018,0020)",
        "Scanning Sequence",
        "CS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "SequenceVariant",
        "(0018,0021)",
        "Sequence Variant",
        "CS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "ScanOptions",
        "(0018,0022)",
        "Scan Options",
        "CS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _key(
        "MRAcquisitionType",
        "(0018,0023)",
        "MR Acquisition Type",
        "CS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "SequenceName",
        "(0018,0024)",
        "Sequence Name",
        "SH",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "EchoTime",
        "(0018,0081)",
        "Echo Time",
        "DS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "InversionTime",
        "(0018,0082)",
        "Inversion Time",
        "DS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "EchoNumbers",
        "(0018,0086)",
        "Echo Number",
        "IS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "EchoTrainLength",
        "(0018,0091)",
        "Echo Train Length",
        "IS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
        modality_in=("MR",),
    ),
    _key(
        "TriggerTime",
        "(0018,1060)",
        "Trigger Time",
        "DS",
        "image",
        "IMAGE",
        default_return=False,
        requires=IMAGE_CHILD,
    ),
    _vue(
        "TamarStudyStatus",
        "(07a1,102a)",
        "Tamar Study Status",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="UNREAD",
    ),
    _vue(
        "TamarStudyBodyPart",
        "(07a1,1040)",
        "Tamar Study Body Part",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="PELVIS",
    ),
    _vue(
        "TamarAssignToDoctor",
        "(07a1,1042)",
        "Tamar Assign To Doctor",
        "SH",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudyPriority",
        "(07a1,1043)",
        "Tamar Study Priority",
        "IS",
        "vue-study",
        "STUDY",
        placeholder="2",
    ),
    _vue(
        "TamarSiteId",
        "(07a1,1050)",
        "Tamar Site Id",
        "US",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudyPublished",
        "(07a1,1058)",
        "Tamar Study Published",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="A",
    ),
    _vue(
        "TamarStudyCreationDate",
        "(07a1,105d)",
        "Tamar Study Creation Date",
        "DT",
        "vue-study",
        "STUDY",
        placeholder="YYYYMMDDHHMMSS",
    ),
    _vue(
        "TamarStudyHasBookmark",
        "(07a1,105f)",
        "Tamar Study Has Bookmark",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="Y",
    ),
    _vue(
        "TamarStudyReferredBy",
        "(07a1,10a7)",
        "Tamar Study Referred By",
        "LO",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudyHasStickyNote",
        "(07a3,1003)",
        "Tamar Study Has Sticky Note",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarProcedureCode",
        "(07a3,1014)",
        "Tamar Procedure Code",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarPatientLocation",
        "(07a3,1015)",
        "Tamar Patient Location",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarOrderStatus",
        "(07a3,1017)",
        "Tamar Order Status",
        "SH",
        "vue-study",
        "STUDY",
        placeholder="CM",
    ),
    _vue(
        "TamarStudyReason",
        "(07a3,1018)",
        "Tamar Study Reason",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarReadingPhysicianId",
        "(07a3,101b)",
        "Tamar Reading Physician Id",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarMiscString4",
        "(07a3,1022)",
        "Tamar Misc String 4",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarMiscString5",
        "(07a3,1023)",
        "Tamar Misc String 5",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudyHasMammoCad",
        "(07a3,1055)",
        "Tamar Study Has Mammo CAD",
        "SH",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarPracticeSettingsCode",
        "(07a3,105c)",
        "Tamar Practice Settings Code",
        "ST",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarReportIsPendingSign",
        "(07a3,108c)",
        "Tamar Report Is Pending Sign",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarStudyHasNondicomData",
        "(07a3,108f)",
        "Tamar Study Has Non-DICOM Data",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarStudyHasKeyImage",
        "(07a3,109c)",
        "Tamar Study Has Key Image",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="Y",
    ),
    _vue(
        "TamarStudyHasKeySeries",
        "(07a3,10bb)",
        "Tamar Study Has Key Series",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarReferringPhysiciansStudyRead",
        "(07a5,1056)",
        "Tamar Referring Physicians Study Read",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="N",
    ),
    _vue(
        "TamarStudyInsertTime",
        "(07a5,1072)",
        "Tamar Study Insert Time",
        "DT",
        "vue-study",
        "STUDY",
        placeholder="YYYYMMDDHHMMSS",
    ),
    _vue(
        "TamarStudyHasDicomData",
        "(07a5,10c8)",
        "Tamar Study Has DICOM Data",
        "CS",
        "vue-study",
        "STUDY",
        placeholder="Y",
    ),
    _vue(
        "TamarStudySla",
        "(07a5,10dc)",
        "Tamar Study SLA",
        "UL",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudyRvu",
        "(07a5,10dd)",
        "Tamar Study RVU",
        "FL",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarStudySubspecialtyId",
        "(07a5,10de)",
        "Tamar Study Subspecialty Id",
        "LO",
        "vue-study",
        "STUDY",
    ),
    _vue(
        "TamarAssignmentRuleId",
        "(07a5,10e7)",
        "Tamar Assignment Rule Id",
        "LO",
        "vue-study",
        "STUDY",
        placeholder="manual_assignment",
    ),
    _vue(
        "TamarAssignmentRulePriority",
        "(07a5,10e8)",
        "Tamar Assignment Rule Priority",
        "IS",
        "vue-study",
        "STUDY",
        placeholder="0",
    ),
    _vue(
        "TamarNumberOfImagesInSeries",
        "(07a1,1002)",
        "Tamar Number of Images in Series",
        "UL",
        "vue-series",
        "SERIES",
        requires=SERIES_CHILD,
    ),
    _vue(
        "TamarKeySeriesIndication",
        "(07a3,10b9)",
        "Tamar Key Series Indication",
        "CS",
        "vue-series",
        "SERIES",
        placeholder="N",
        requires=SERIES_CHILD,
    ),
    _vue(
        "TamarBandwidth",
        "(07a5,1057)",
        "Tamar Bandwidth",
        "IS",
        "vue-series",
        "SERIES",
        requires=SERIES_CHILD,
    ),
    _vue(
        "TamarSeriesArrivalOrder",
        "(07a5,1059)",
        "Tamar Series Arrival Order",
        "IS",
        "vue-series",
        "SERIES",
        requires=SERIES_CHILD,
    ),
    _vue(
        "TamarOriginalStoringAe",
        "(07a5,1069)",
        "Tamar Original Storing AE",
        "AE",
        "vue-series",
        "SERIES",
        requires=SERIES_CHILD,
    ),
)

_KEYS_BY_KEYWORD = {key.keyword: key for key in KEYS}


def normalize_level(value: str | None) -> str:
    level = (value or "STUDY").strip().upper()
    if level not in LEVELS:
        raise ValueError("Query level must be STUDY, SERIES, or IMAGE.")
    return level


def keys_for_level(level: str) -> list[FindKey]:
    level = normalize_level(level)
    return [key for key in KEYS if level in key.levels]


def key_by_keyword(keyword: str) -> FindKey | None:
    return _KEYS_BY_KEYWORD.get(keyword)


def default_return_keys(level: str) -> list[str]:
    return [key.keyword for key in keys_for_level(level) if key.default_return]


def normalize_da(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if HTML_DATE.fullmatch(text):
        return text.replace("-", "")
    matched = HTML_DATE_RANGE.fullmatch(text)
    if matched:
        return matched.group(1).replace("-", "") + "-" + matched.group(2).replace("-", "")
    return text


def normalize_tm(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    matched = HTML_TIME.fullmatch(text)
    if matched:
        seconds = matched.group(3) or "00"
        if "." in seconds:
            whole, frac = seconds.split(".", 1)
            return f"{matched.group(1)}{matched.group(2)}{whole.zfill(2)}.{frac}"
        return f"{matched.group(1)}{matched.group(2)}{seconds.zfill(2)}"
    return text.replace(":", "")


def normalize_value(keyword: str, value: str) -> str:
    key = _KEYS_BY_KEYWORD.get(keyword)
    text = (value or "").strip()
    if not key or not text:
        return text
    if key.vr == "DA":
        return normalize_da(text)
    if key.vr == "TM":
        return normalize_tm(text)
    if key.vr == "CS":
        return text.upper()
    return text


def _form_list(form: Mapping[str, Any], name: str) -> list[str]:
    getter = getattr(form, "getlist", None)
    if callable(getter):
        return [str(item) for item in getter(name)]
    raw = form.get(name)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return [str(raw)]


def options_from_form(form: Mapping[str, Any]) -> dict[str, Any]:
    level = normalize_level(str(form.get("level") or "STUDY"))
    included = {item for item in _form_list(form, "include") if item}
    values: dict[str, str] = {}
    return_keys: list[str] = []
    for key in keys_for_level(level):
        raw = str(form.get(f"key_{key.keyword}") or "").strip()
        if raw:
            values[key.keyword] = raw
        if key.keyword in included or raw:
            return_keys.append(key.keyword)
    return {"level": level, "values": values, "return_keys": return_keys}


def options_from_payload(options: Mapping[str, Any] | None) -> dict[str, Any]:
    options = dict(options or {})
    level = normalize_level(str(options.get("level") or "STUDY"))
    raw_values = options.get("values")
    values: dict[str, str] = {}
    if isinstance(raw_values, Mapping):
        for keyword, raw in raw_values.items():
            text = str(raw or "").strip()
            if text:
                values[str(keyword)] = text
    else:
        for key in keys_for_level(level):
            text = str(options.get(key.keyword) or options.get(key.keyword.lower()) or "").strip()
            if text:
                values[key.keyword] = text
        for alias, keyword in (
            ("patient_name", "PatientName"),
            ("patient_id", "PatientID"),
            ("accession_number", "AccessionNumber"),
            ("study_date", "StudyDate"),
            ("modality", "ModalitiesInStudy" if level == "STUDY" else "Modality"),
            ("study_instance_uid", "StudyInstanceUID"),
            ("series_instance_uid", "SeriesInstanceUID"),
        ):
            text = str(options.get(alias) or "").strip()
            if text and keyword not in values:
                values[keyword] = text
    requested = options.get("return_keys")
    if isinstance(requested, list) and requested:
        allowed = {key.keyword for key in keys_for_level(level)}
        return_keys = [str(item) for item in requested if str(item) in allowed]
    else:
        return_keys = default_return_keys(level)
    for keyword in values:
        if keyword not in return_keys and any(keyword == key.keyword for key in keys_for_level(level)):
            return_keys.append(keyword)
    return {"level": level, "values": values, "return_keys": return_keys}


def missing_parents(level: str, values: Mapping[str, str]) -> list[str]:
    level = normalize_level(level)
    missing: list[str] = []
    for keyword in LEVEL_PARENTS[level]:
        if not str(values.get(keyword) or "").strip():
            missing.append(keyword)
    return missing


def validate_query(level: str, values: Mapping[str, str]) -> str | None:
    missing = missing_parents(level, values)
    if not missing:
        return None
    labels = ", ".join(_KEYS_BY_KEYWORD[name].label for name in missing)
    if level == "SERIES":
        return (
            f"Series C-FIND needs {labels}. Hierarchical Query/Retrieve does not "
            "search series across the archive without a Study Instance UID."
        )
    return (
        f"Image C-FIND needs {labels}. Hierarchical Query/Retrieve does not "
        "search instances without Study Instance UID and Series Instance UID."
    )


def key_unlocked(key: FindKey, values: Mapping[str, str]) -> tuple[bool, str]:
    missing = [name for name in key.requires if not str(values.get(name) or "").strip()]
    if missing:
        labels = ", ".join(_KEYS_BY_KEYWORD[name].label for name in missing)
        return False, f"Enter {labels} first"
    if key.modality_in:
        modality = str(values.get("Modality") or "").strip().upper()
        if modality and modality not in {item.upper() for item in key.modality_in}:
            wanted = ", ".join(key.modality_in)
            return False, f"Available when Modality is {wanted} (or left empty)"
    return True, ""


def selected_keywords(level: str, values: Mapping[str, str], return_keys: Iterable[str]) -> list[str]:
    wanted = list(dict.fromkeys([*LEVEL_PARENTS[normalize_level(level)], *return_keys, *values]))
    allowed = [key.keyword for key in keys_for_level(level)]
    return [keyword for keyword in wanted if keyword in allowed]


def _parse_tag(tag: str) -> tuple[int, int]:
    inner = tag.strip().strip("()")
    group_hex, element_hex = inner.split(",", 1)
    return int(group_hex, 16), int(element_hex, 16)


def _coerce_vr(vr: str, text: str) -> Any:
    if text == "":
        return None
    if vr in {"US", "SS", "UL", "SL"}:
        return int(float(text))
    if vr in {"FL", "FD"}:
        return float(text)
    return text


def apply_key(dataset: Dataset, keyword: str, value: str) -> None:
    key = _KEYS_BY_KEYWORD.get(keyword)
    text = normalize_value(keyword, value)
    if key and key.private_creator:
        group, element = _parse_tag(key.tag)
        block = dataset.private_block(group, key.private_creator, create=True)
        block.add_new(element & 0xFF, key.vr, _coerce_vr(key.vr, text))
        return
    tag = tag_for_keyword(keyword)
    if tag is None:
        raise KeyError(keyword)
    vr = dictionary_VR(tag)
    dataset.add_new(tag, vr, _coerce_vr(vr, text))


def build_identifier(level: str, values: Mapping[str, str], return_keys: Iterable[str]) -> Dataset:
    level = normalize_level(level)
    identifier = Dataset()
    identifier.QueryRetrieveLevel = level
    for keyword in selected_keywords(level, values, return_keys):
        apply_key(identifier, keyword, str(values.get(keyword) or ""))
    return identifier


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


def _read_key(dataset: Dataset, keyword: str) -> str:
    key = _KEYS_BY_KEYWORD.get(keyword)
    if key and key.private_creator:
        group, element = _parse_tag(key.tag)
        try:
            block = dataset.private_block(group, key.private_creator)
            return _stringify(block[element & 0xFF].value)
        except (KeyError, ValueError, IndexError):
            tag = Tag(group, element)
            if tag in dataset:
                return _stringify(dataset[tag].value)
            return ""
    return _stringify(getattr(dataset, keyword, None))


def record_from_dataset(dataset: Dataset, columns: list[str]) -> dict[str, str]:
    return {keyword: _read_key(dataset, keyword) for keyword in columns}


def column_labels(columns: Iterable[str]) -> list[str]:
    labels: list[str] = []
    for keyword in columns:
        key = _KEYS_BY_KEYWORD.get(keyword)
        labels.append(key.label if key else keyword)
    return labels


def records_to_csv(records: list[dict[str, str]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(column_labels(columns))
    for record in records:
        writer.writerow([record.get(column, "") for column in columns])
    return buffer.getvalue()


def records_to_json(records: list[dict[str, str]], columns: list[str], *, level: str) -> str:
    payload = {
        "level": normalize_level(level),
        "columns": [{"keyword": column, "label": label} for column, label in zip(columns, column_labels(columns))],
        "records": [{column: record.get(column, "") for column in columns} for record in records],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def catalog_payload() -> dict[str, Any]:
    return {
        "levels": [
            {
                "id": level,
                "label": LEVEL_LABELS[level],
                "parents": list(LEVEL_PARENTS[level]),
            }
            for level in LEVELS
        ],
        "groups": list(GROUPS),
        "search_keys": [asdict(key) for key in KEYS],
    }
