"""DICOM Modality Worklist query, matching, and dataset conversion."""

from __future__ import annotations

import time
import uuid
from fnmatch import fnmatch
from typing import Any

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom.sop_class import ModalityWorklistInformationFind

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.models import LocalAE, RemoteNode, WorklistEntry, WorklistQuery, WorklistQueryResult
from app.store import ConfigStore

PENDING = {0xFF00, 0xFF01}


def to_dicom_date(value: str) -> str:
    return (value or "").strip().replace("-", "")


def to_dicom_time(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    compact = value.replace(":", "")
    if len(compact) == 4:
        return compact + "00"
    return compact


def display_date(value: str) -> str:
    value = to_dicom_date(value)
    if len(value) == 8:
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def display_time(value: str) -> str:
    compact = to_dicom_time(value)
    if len(compact) >= 4:
        return f"{compact[0:2]}:{compact[2:4]}"
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sps(ds: Dataset) -> Dataset:
    sequence = getattr(ds, "ScheduledProcedureStepSequence", None)
    if sequence:
        return sequence[0]
    return Dataset()


def entry_to_dataset(entry: WorklistEntry) -> Dataset:
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 100"
    ds.PatientName = entry.patient_name
    ds.PatientID = entry.patient_id
    ds.PatientBirthDate = to_dicom_date(entry.patient_birth_date)
    ds.PatientSex = entry.patient_sex
    ds.AccessionNumber = entry.accession_number
    ds.RequestedProcedureID = entry.requested_procedure_id
    ds.RequestedProcedureDescription = entry.requested_procedure_description
    ds.StudyInstanceUID = entry.study_instance_uid or generate_uid()
    item = Dataset()
    item.Modality = entry.modality
    item.ScheduledStationAETitle = entry.station_ae_title
    item.ScheduledStationName = entry.station_name
    item.ScheduledProcedureStepStartDate = to_dicom_date(entry.scheduled_date)
    item.ScheduledProcedureStepStartTime = to_dicom_time(entry.scheduled_time)
    item.ScheduledProcedureStepDescription = entry.requested_procedure_description
    item.ScheduledProcedureStepID = entry.scheduled_procedure_step_id or entry.id
    item.ScheduledPerformingPhysicianName = entry.scheduled_physician
    ds.ScheduledProcedureStepSequence = [item]
    return ds


def dataset_to_entry(ds: Dataset) -> WorklistEntry:
    item = _sps(ds)
    study_uid = _text(getattr(ds, "StudyInstanceUID", ""))
    station = _text(getattr(item, "ScheduledStationAETitle", ""))
    try:
        if station:
            from app.models import normalize_ae_title

            station = normalize_ae_title(station)
    except ValueError:
        station = ""

    return WorklistEntry(
        id=study_uid[-12:] if study_uid else uuid.uuid4().hex[:12],
        patient_name=_text(getattr(ds, "PatientName", "")),
        patient_id=_text(getattr(ds, "PatientID", "")),
        patient_birth_date=display_date(_text(getattr(ds, "PatientBirthDate", ""))),
        patient_sex=_text(getattr(ds, "PatientSex", "")),
        accession_number=_text(getattr(ds, "AccessionNumber", "")),
        requested_procedure_id=_text(getattr(ds, "RequestedProcedureID", "")),
        requested_procedure_description=_text(
            getattr(ds, "RequestedProcedureDescription", "")
            or getattr(item, "ScheduledProcedureStepDescription", "")
        ),
        modality=_text(getattr(item, "Modality", "")),
        station_ae_title=station,
        station_name=_text(getattr(item, "ScheduledStationName", "")),
        scheduled_date=display_date(_text(getattr(item, "ScheduledProcedureStepStartDate", ""))),
        scheduled_time=display_time(_text(getattr(item, "ScheduledProcedureStepStartTime", ""))),
        scheduled_physician=_text(getattr(item, "ScheduledPerformingPhysicianName", "")),
        study_instance_uid=study_uid,
        scheduled_procedure_step_id=_text(getattr(item, "ScheduledProcedureStepID", "")),
    )


def build_identifier(query: WorklistQuery) -> Dataset:
    ds = Dataset()
    ds.PatientName = query.patient_name
    ds.PatientID = query.patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.AccessionNumber = query.accession_number
    ds.RequestedProcedureID = ""
    ds.RequestedProcedureDescription = ""
    ds.StudyInstanceUID = ""
    item = Dataset()
    item.Modality = query.modality
    item.ScheduledStationAETitle = query.station_ae_title
    item.ScheduledStationName = ""
    item.ScheduledProcedureStepStartDate = to_dicom_date(query.scheduled_date)
    item.ScheduledProcedureStepStartTime = ""
    item.ScheduledProcedureStepDescription = ""
    item.ScheduledProcedureStepID = ""
    item.ScheduledPerformingPhysicianName = ""
    ds.ScheduledProcedureStepSequence = [item]
    return ds


def _wildcard_match(query: str, value: str) -> bool:
    if not query:
        return True
    pattern = query.replace("*", "").upper() if "*" not in query else query.upper()
    candidate = value.upper()
    if "*" in query:
        return fnmatch(candidate, query.upper())
    return pattern in candidate


def matches_entry(entry: WorklistEntry, query: WorklistQuery) -> bool:
    if query.patient_id and query.patient_id.upper() != entry.patient_id.upper():
        return False
    if query.accession_number and query.accession_number.upper() != entry.accession_number.upper():
        return False
    if query.modality and query.modality.upper() != entry.modality.upper():
        return False
    if query.station_ae_title and query.station_ae_title.upper() != entry.station_ae_title.upper():
        return False
    if query.patient_name and not _wildcard_match(query.patient_name, entry.patient_name):
        return False
    if query.scheduled_date and to_dicom_date(query.scheduled_date) != to_dicom_date(entry.scheduled_date):
        return False
    return True


def query_from_identifier(ds: Dataset) -> WorklistQuery:
    item = _sps(ds)
    return WorklistQuery(
        patient_name=_text(getattr(ds, "PatientName", "")),
        patient_id=_text(getattr(ds, "PatientID", "")),
        accession_number=_text(getattr(ds, "AccessionNumber", "")),
        modality=_text(getattr(item, "Modality", "")),
        station_ae_title=_text(getattr(item, "ScheduledStationAETitle", "")),
        scheduled_date=display_date(_text(getattr(item, "ScheduledProcedureStepStartDate", ""))),
    )


def matches_dataset(ds: Dataset, identifier: Dataset) -> bool:
    return matches_entry(dataset_to_entry(ds), query_from_identifier(identifier))


def query_local(store: ConfigStore, query: WorklistQuery) -> WorklistQueryResult:
    started = time.perf_counter()
    entries = [entry for entry in store.list_worklist() if matches_entry(entry, query)]
    return WorklistQueryResult(
        ok=True,
        source="local",
        summary=f"{len(entries)} local worklist item{'s' if len(entries) != 1 else ''}",
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        entries=entries,
    )


def query_remote(local: LocalAE, remote: RemoteNode, query: WorklistQuery) -> WorklistQueryResult:
    started = time.perf_counter()
    entries: list[WorklistEntry] = []
    contexts: list[dict[str, Any]] = []
    assoc = None
    with capture_pynetdicom_log() as log_stream:
        try:
            _ae, assoc = associate(local, remote, [ModalityWorklistInformationFind])
            contexts = context_rows(assoc)
            rejected = rejected_sop_message(
                contexts,
                "Modality Worklist FIND (1.2.840.10008.5.1.4.31)",
                "This node is not an MWL SCP. Study Root C-FIND searches stored studies "
                "and is a different SOP Class.",
            )
            if not assoc.is_established:
                return WorklistQueryResult(
                    ok=False,
                    source=remote.name,
                    summary=rejected or reject_reason(assoc),
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    log=log_stream.getvalue().strip(),
                    contexts=contexts,
                )
            if not assoc.accepted_contexts:
                assoc.release()
                return WorklistQueryResult(
                    ok=False,
                    source=remote.name,
                    summary=rejected
                    or (
                        "Association opened, but Modality Worklist FIND "
                        "(1.2.840.10008.5.1.4.31) was not accepted. This node is not an "
                        "MWL SCP. Study Root C-FIND searches stored studies and is a "
                        "different SOP Class."
                    ),
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    log=log_stream.getvalue().strip(),
                    contexts=contexts,
                )

            identifier = build_identifier(query)
            for status, identifier_ds in assoc.send_c_find(
                identifier, ModalityWorklistInformationFind
            ):
                if not status:
                    return WorklistQueryResult(
                        ok=False,
                        source=remote.name,
                        summary="No C-FIND response (timeout, abort, or invalid PDU).",
                        duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        log=log_stream.getvalue().strip(),
                        contexts=contexts,
                    )
                code = int(status.Status)
                if code in PENDING and identifier_ds is not None:
                    entries.append(dataset_to_entry(identifier_ds))
                elif code == 0x0000:
                    continue
                else:
                    assoc.release()
                    return WorklistQueryResult(
                        ok=False,
                        source=remote.name,
                        summary=f"C-FIND status 0x{code:04X}",
                        duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        entries=entries,
                        log=log_stream.getvalue().strip(),
                        contexts=contexts,
                    )
            assoc.release()
            return WorklistQueryResult(
                ok=True,
                source=remote.name,
                summary=f"{len(entries)} worklist item{'s' if len(entries) != 1 else ''} from {remote.ae_title}",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                entries=entries,
                log=log_stream.getvalue().strip(),
                contexts=contexts,
            )
        except Exception as exc:  # noqa: BLE001
            return WorklistQueryResult(
                ok=False,
                source=remote.name,
                summary=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                log=log_stream.getvalue().strip(),
                contexts=contexts,
            )
        finally:
            if assoc is not None and getattr(assoc, "is_established", False):
                try:
                    assoc.abort()
                except Exception:  # noqa: BLE001
                    pass


def query_worklist(
    store: ConfigStore,
    source: str,
    query: WorklistQuery,
) -> WorklistQueryResult:
    if source == "local":
        return query_local(store, query)
    config = store.load()
    remote = config.get_remote(source)
    if remote is None:
        return WorklistQueryResult(ok=False, summary="Remote DICOM node not found.", source=source)
    if not query.station_ae_title and config.local.station_ae_title:
        query = query.model_copy(update={"station_ae_title": config.local.station_ae_title})
    return query_remote(config.local, remote, query)
