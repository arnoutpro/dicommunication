"""Catalog of DICOM tags the Anonymizer knows about.

``identifying`` flags the tags **Remove patient info** auto-selects — a
curated subset roughly following DICOM PS3.15 Annex E (the Basic Application
Level Confidentiality Profile), not the full exhaustive standard list, but
the tags that commonly carry PHI on a real study. **Custom** mode exposes
every tag here, identifying or not, for a per-tag keep / erase / replace
decision.

UID-category tags (StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID,
FrameOfReferenceUID) are handled separately from plain erase/replace: a UID
cannot simply be blanked or given arbitrary text and stay valid, and several
instances retrieved together must keep matching UIDs to remain one coherent
study/series after anonymization. See app.tools.anon_engine.UidMap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnonTag:
    keyword: str
    label: str
    category: str  # "patient" | "study" | "physician" | "equipment" | "uid"
    identifying: bool = True


def _t(keyword: str, label: str, category: str, *, identifying: bool = True) -> AnonTag:
    return AnonTag(keyword=keyword, label=label, category=category, identifying=identifying)


ANON_TAGS: tuple[AnonTag, ...] = (
    # Patient
    _t("PatientName", "Patient Name", "patient"),
    _t("PatientID", "Patient ID", "patient"),
    _t("PatientBirthDate", "Patient Birth Date", "patient"),
    _t("PatientBirthTime", "Patient Birth Time", "patient"),
    _t("PatientSex", "Patient Sex", "patient"),
    _t("PatientAge", "Patient Age", "patient", identifying=False),
    _t("PatientWeight", "Patient Weight", "patient", identifying=False),
    _t("PatientSize", "Patient Size", "patient", identifying=False),
    _t("PatientAddress", "Patient Address", "patient"),
    _t("PatientTelephoneNumbers", "Patient Telephone Numbers", "patient"),
    _t("PatientMotherBirthName", "Patient Mother's Birth Name", "patient"),
    _t("OtherPatientIDs", "Other Patient IDs", "patient"),
    _t("OtherPatientNames", "Other Patient Names", "patient"),
    _t("EthnicGroup", "Ethnic Group", "patient"),
    _t("PatientComments", "Patient Comments", "patient"),
    _t("MilitaryRank", "Military Rank", "patient"),
    _t("BranchOfService", "Branch of Service", "patient"),
    _t("CountryOfResidence", "Country of Residence", "patient"),
    _t("RegionOfResidence", "Region of Residence", "patient"),
    _t("PatientReligiousPreference", "Patient Religious Preference", "patient"),
    _t("ResponsiblePerson", "Responsible Person", "patient"),
    _t("ResponsibleOrganization", "Responsible Organization", "patient"),
    # Study / procedure
    _t("AccessionNumber", "Accession Number", "study"),
    _t("StudyID", "Study ID", "study", identifying=False),
    _t("StudyDescription", "Study Description", "study", identifying=False),
    _t("AdmittingDiagnosesDescription", "Admitting Diagnoses", "study"),
    _t("SeriesDescription", "Series Description", "study", identifying=False),
    _t("ProtocolName", "Protocol Name", "study", identifying=False),
    _t("ImageComments", "Image Comments", "study"),
    _t("RequestAttributesSequence", "Request Attributes", "study"),
    _t("PerformedProcedureStepID", "Performed Procedure Step ID", "study", identifying=False),
    _t(
        "PerformedProcedureStepDescription",
        "Performed Procedure Step Description",
        "study",
        identifying=False,
    ),
    _t(
        "ScheduledProcedureStepDescription",
        "Scheduled Procedure Step Description",
        "study",
        identifying=False,
    ),
    # Physicians / operators
    _t("RequestingPhysician", "Requesting Physician", "physician"),
    _t("ReferringPhysicianName", "Referring Physician", "physician"),
    _t("ReferringPhysicianAddress", "Referring Physician Address", "physician"),
    _t("ReferringPhysicianTelephoneNumbers", "Referring Physician Telephone", "physician"),
    _t("PhysiciansOfRecord", "Physicians of Record", "physician"),
    _t("PerformingPhysicianName", "Performing Physician", "physician"),
    _t("NameOfPhysiciansReadingStudy", "Physician(s) Reading Study", "physician"),
    _t("OperatorsName", "Operator's Name", "physician"),
    # Institution / equipment
    _t("InstitutionName", "Institution Name", "equipment"),
    _t("InstitutionAddress", "Institution Address", "equipment"),
    _t("InstitutionalDepartmentName", "Institutional Department Name", "equipment"),
    _t("StationName", "Station Name", "equipment"),
    _t("DeviceSerialNumber", "Device Serial Number", "equipment"),
    _t("SoftwareVersions", "Software Versions", "equipment", identifying=False),
    _t("Manufacturer", "Manufacturer", "equipment", identifying=False),
    _t("ManufacturerModelName", "Manufacturer Model Name", "equipment", identifying=False),
    # Identifiers — always remapped to a fresh, consistent UID, never plain
    # erase/replace text (see app.tools.anon_engine).
    _t("StudyInstanceUID", "Study Instance UID", "uid", identifying=False),
    _t("SeriesInstanceUID", "Series Instance UID", "uid", identifying=False),
    _t("SOPInstanceUID", "SOP Instance UID", "uid", identifying=False),
    _t("FrameOfReferenceUID", "Frame of Reference UID", "uid", identifying=False),
)

TAGS_BY_KEYWORD: dict[str, AnonTag] = {tag.keyword: tag for tag in ANON_TAGS}
UID_KEYWORDS: frozenset[str] = frozenset(tag.keyword for tag in ANON_TAGS if tag.category == "uid")
IDENTIFYING_KEYWORDS: frozenset[str] = frozenset(tag.keyword for tag in ANON_TAGS if tag.identifying)

CATEGORY_LABELS: dict[str, str] = {
    "patient": "Patient",
    "study": "Study / procedure",
    "physician": "Physicians / operators",
    "equipment": "Institution / equipment",
    "uid": "Identifiers (UIDs)",
}
CATEGORY_ORDER: tuple[str, ...] = ("patient", "study", "physician", "equipment", "uid")


def tags_by_category() -> list[tuple[str, str, list[AnonTag]]]:
    """(category id, label, tags) in a stable display order."""
    buckets: dict[str, list[AnonTag]] = {}
    for tag in ANON_TAGS:
        buckets.setdefault(tag.category, []).append(tag)
    return [
        (category, CATEGORY_LABELS.get(category, category.title()), buckets[category])
        for category in CATEGORY_ORDER
        if category in buckets
    ]
