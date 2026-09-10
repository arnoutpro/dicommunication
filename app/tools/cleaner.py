"""Query a PACS, pick studies, redact a burned-in region from their pixel data,
and send the cleaned instances back over C-STORE.

Same two-action shape as Dicom Anonymizer (query -> pick -> run), and the
query/browse/retrieve plumbing below is a deliberate near-duplicate of
app/tools/anonymize.py's — same reasoning as that module has for not
sharing helpers with app/tools/find_advanced.py: each tool's retrieve step
is one association per selected entity with its own error handling, small
enough that a shared abstraction would cost more than it saves.

What Cleaner adds on top of retrieve: blacking out one operator-configured
rectangle in every retrieved image's pixel data (app/tools/redact_engine.py)
and then C-STOREing the result to a destination node — by default the same
PACS the study came from. It does not touch other tags the way Anonymizer's
modes do; combine the two tools if a study needs both.
"""

from __future__ import annotations

import time
from typing import Any

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.dicom_client import associate, capture_pynetdicom_log, context_rows, reject_reason, rejected_sop_message
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.mwl_scp import STORAGE_INBOX
from app.tools.base import BaseTool, elapsed_ms
from app.tools.find_advanced import retrieve_storage_gate_message
from app.tools.find_keys import build_identifier, normalize_da, record_from_dataset
from app.tools.redact_engine import RedactionError, RedactRegion, parse_region, redact_pixels
from app.tools.registry import register

PENDING = {0xFF00, 0xFF01}
MAX_QUERY_RESULTS = 500
MAX_BROWSE_RESULTS = 2000
MAX_ENTITIES_PER_RUN = 200

QUERY_COLUMNS = [
    "StudyInstanceUID",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "ModalitiesInStudy",
]
SERIES_COLUMNS = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "Modality",
    "SeriesNumber",
    "SeriesDescription",
    "NumberOfSeriesRelatedInstances",
]
IMAGE_COLUMNS = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "InstanceNumber",
]

LEVELS = ("STUDY", "SERIES", "IMAGE")
UID_MODES = ("new", "same")


def _study_query(local: LocalAE, remote: RemoteNode, values: dict[str, str]):
    identifier = build_identifier("STUDY", values, QUERY_COLUMNS)
    contexts: list[dict[str, Any]] = []
    assoc = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(contexts, "Study Root Query/Retrieve FIND")
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve FIND was not accepted.", contexts
        records: list[dict[str, str]] = []
        error = None
        for status, identifier_ds in assoc.send_c_find(
            identifier, StudyRootQueryRetrieveInformationModelFind
        ):
            if not status:
                error = "No C-FIND response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code in PENDING and identifier_ds is not None:
                if len(records) < MAX_QUERY_RESULTS:
                    records.append(record_from_dataset(identifier_ds, QUERY_COLUMNS))
            elif code == 0x0000:
                continue
            else:
                error = f"C-FIND status 0x{code:04X}"
                break
        assoc.release()
        return records, error, contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass


def _child_query(
    local: LocalAE, remote: RemoteNode, assoc, level: str, values: dict[str, str], columns: list[str], cap: int
) -> tuple[list[dict[str, str]], str | None]:
    identifier = build_identifier(level, values, columns)
    records: list[dict[str, str]] = []
    error = None
    for status, identifier_ds in assoc.send_c_find(identifier, StudyRootQueryRetrieveInformationModelFind):
        if not status:
            error = "No C-FIND response (timeout, abort, or invalid PDU)."
            break
        code = int(status.Status)
        if code in PENDING and identifier_ds is not None:
            if len(records) < cap:
                records.append(record_from_dataset(identifier_ds, columns))
        elif code == 0x0000:
            continue
        else:
            error = f"C-FIND status 0x{code:04X}"
            break
    return records, error


def _browse(local: LocalAE, remote: RemoteNode, study_uids: list[str], level: str):
    """Walk down from each selected study to Series or all the way to every image."""
    contexts: list[dict[str, Any]] = []
    assoc = None
    rows: list[dict[str, str]] = []
    error: str | None = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(contexts, "Study Root Query/Retrieve FIND")
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve FIND was not accepted.", contexts
        for study_uid in study_uids:
            series_rows, series_error = _child_query(
                local, remote, assoc, "SERIES", {"StudyInstanceUID": study_uid}, SERIES_COLUMNS, MAX_BROWSE_RESULTS
            )
            if series_error and not series_rows:
                error = series_error
                continue
            if level == "SERIES":
                rows.extend(series_rows)
                continue
            for series_row in series_rows:
                series_uid = series_row.get("SeriesInstanceUID", "")
                image_rows, image_error = _child_query(
                    local, remote, assoc, "IMAGE",
                    {"StudyInstanceUID": study_uid, "SeriesInstanceUID": series_uid},
                    IMAGE_COLUMNS, MAX_BROWSE_RESULTS,
                )
                if image_error and not image_rows:
                    error = image_error
                    continue
                rows.extend(image_rows)
            if len(rows) >= MAX_BROWSE_RESULTS:
                break
        assoc.release()
        return rows[:MAX_BROWSE_RESULTS], (error if not rows else None), contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass


def _move_identifier(entity: dict[str, Any]) -> Dataset:
    identifier = Dataset()
    study_uid = str(entity.get("study_uid") or entity.get("StudyInstanceUID") or "")
    series_uid = str(entity.get("series_uid") or entity.get("SeriesInstanceUID") or "")
    sop_uid = str(entity.get("sop_instance_uid") or entity.get("SOPInstanceUID") or "")
    identifier.StudyInstanceUID = study_uid
    if sop_uid:
        identifier.QueryRetrieveLevel = "IMAGE"
        identifier.SeriesInstanceUID = series_uid
        identifier.SOPInstanceUID = sop_uid
    elif series_uid:
        identifier.QueryRetrieveLevel = "SERIES"
        identifier.SeriesInstanceUID = series_uid
    else:
        identifier.QueryRetrieveLevel = "STUDY"
    return identifier


def _retrieve_many(local: LocalAE, remote: RemoteNode, entities: list[dict[str, Any]], dest_ae: str):
    """One association, one C-MOVE per selected entity, one shared capture."""
    STORAGE_INBOX.begin()
    contexts: list[dict[str, Any]] = []
    assoc = None
    error: str | None = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelMove])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(
            contexts, "Study Root Query/Retrieve MOVE",
            "The remote must accept C-MOVE (not C-GET) and know this AE as a destination.",
        )
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve MOVE was not accepted.", contexts
        for entity in entities:
            identifier = _move_identifier(entity)
            for status, _remaining in assoc.send_c_move(identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove):
                if not status:
                    error = "No C-MOVE response (timeout, abort, or invalid PDU)."
                    break
                code = int(status.Status)
                if code in PENDING:
                    continue
                if code != 0x0000:
                    error = f"C-MOVE status 0x{code:04X}"
                break
        assoc.release()
        time.sleep(0.05)
        return STORAGE_INBOX.finish(), error, contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass
        STORAGE_INBOX.finish()


def browse_study_images(local: LocalAE, remote: RemoteNode, study_uid: str):
    """List every image in one study, for the region-preview picker."""
    return _browse(local, remote, [study_uid], "IMAGE")


def retrieve_preview_instance(
    local: LocalAE, remote: RemoteNode, study_uid: str, series_uid: str, sop_uid: str, dest_ae: str
) -> tuple[Dataset | None, str | None]:
    """Retrieve exactly one instance, for rendering a region-picker preview."""
    entity = {"study_uid": study_uid, "series_uid": series_uid, "sop_instance_uid": sop_uid}
    datasets, error, _contexts = _retrieve_many(local, remote, [entity], dest_ae)
    if not datasets:
        return None, error or "Nothing was retrieved."
    return datasets[0], error


def storage_gate_message(local: LocalAE, options: dict[str, Any]) -> str | None:
    """Same Accept-C-STORE / listener gate the run step checks, reused before a preview retrieve."""
    dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
    storage_enabled = bool(options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False)))
    storage_running = bool(options.get("_storage_running", False))
    storage_error = str(options.get("_storage_error") or "").strip()
    return retrieve_storage_gate_message(
        dest_ae=dest_ae, port=int(local.port), calling_ae=local.ae_title,
        enabled=storage_enabled, running=storage_running, storage_error=storage_error,
    )


def _renumber_for_new_uid(ds: Dataset) -> None:
    """Fresh SOPInstanceUID so the PACS accepts the cleaned image as a new
    object rather than colliding with the original; Study/Series UID are
    left alone so it lands next to the original instead of a new study.
    """
    new_sop_uid = generate_uid()
    ds.SOPInstanceUID = new_sop_uid
    file_meta = getattr(ds, "file_meta", None)
    if file_meta is not None:
        if "MediaStorageSOPInstanceUID" in file_meta or hasattr(file_meta, "MediaStorageSOPInstanceUID"):
            file_meta.MediaStorageSOPInstanceUID = new_sop_uid
        if "SOPClassUID" in ds and (
            "MediaStorageSOPClassUID" in file_meta or hasattr(file_meta, "MediaStorageSOPClassUID")
        ):
            file_meta.MediaStorageSOPClassUID = str(ds.SOPClassUID)


def _send_back(local: LocalAE, destination: RemoteNode, datasets: list[Dataset]) -> tuple[int, str]:
    """C-STORE every dataset to destination, using each one's own SOP Class."""
    if not datasets:
        return 0, ""
    sop_classes: list[str] = []
    for ds in datasets:
        sop = str(getattr(ds, "SOPClassUID", "") or "")
        if sop and sop not in sop_classes:
            sop_classes.append(sop)
    if not sop_classes:
        return 0, "Retrieved objects had no SOP Class UID."
    assoc = None
    stored = 0
    error = ""
    try:
        _ae, assoc = associate(local, destination, sop_classes)
        if not getattr(assoc, "is_established", False) or not assoc.accepted_contexts:
            return 0, rejected_sop_message(context_rows(assoc), "Storage") or "Storage association was not accepted."
        for ds in datasets:
            status = assoc.send_c_store(ds)
            if not status:
                error = "No C-STORE response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code == 0x0000:
                stored += 1
            else:
                error = f"C-STORE status 0x{code:04X}"
                break
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.release()
            except Exception:  # noqa: BLE001
                pass
    return stored, error


class CleanerTool(BaseTool):
    id = "dicom-cleaner"
    name = "Dicom Cleaner"
    description = (
        "Query a PACS, retrieve studies/series/images, black out a configured "
        "rectangle of burned-in pixel data (patient info, device overlays), and "
        "send the cleaned instances back over C-STORE."
    )
    category = "dimse"
    template = "cleaner.html"

    def run(self, local: LocalAE, remote: RemoteNode | None, options: dict[str, Any] | None = None) -> ToolResult:
        if remote is None:
            return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary="Select a remote DICOM node first.")
        options = options or {}
        action = str(options.get("action") or "query").strip()
        if action == "run":
            return self._run(local, remote, options)
        return self._query(local, remote, options)

    def _query(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        study_date = normalize_da(str(options.get("study_date") or "").strip())
        if not study_date:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary="Study Date is required.",
                remote_id=remote.id, remote_name=remote.name,
            )
        values: dict[str, str] = {"StudyDate": study_date}
        patient_id = str(options.get("patient_id") or "").strip()
        accession_number = str(options.get("accession_number") or "").strip()
        modality = str(options.get("modality") or "").strip()
        if patient_id:
            values["PatientID"] = patient_id
        if accession_number:
            values["AccessionNumber"] = accession_number
        if modality:
            values["ModalitiesInStudy"] = modality

        started = time.perf_counter()
        with capture_pynetdicom_log() as log_stream:
            find_started = time.perf_counter()
            records, error, contexts = _study_query(local, remote, values)
            log = log_stream.getvalue().strip()

        ok = not error
        message = f"Found {len(records)} stud" + ("y" if len(records) == 1 else "ies") if ok else (error or "Query failed.")
        steps = [ToolStep(name="C-FIND", ok=ok, message=message, duration_ms=elapsed_ms(find_started))]
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=message,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts, records=records,
        )

    def _run(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        level = str(options.get("level") or "STUDY").strip().upper()
        if level not in LEVELS:
            level = "STUDY"
        entities = [{"study_uid": uid} for uid in (options.get("study_uids") or []) if uid]
        if not entities:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary="Select at least one study first.",
                remote_id=remote.id, remote_name=remote.name,
            )

        browse_step: ToolStep | None = None
        if level != "STUDY":
            study_uids = [str(item.get("study_uid") or "") for item in entities if item.get("study_uid")]
            browse_started = time.perf_counter()
            with capture_pynetdicom_log():
                rows, browse_error, _contexts = _browse(local, remote, study_uids, level)
            noun = "series" if level == "SERIES" else "image"
            if not rows:
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=browse_error or f"No {noun}s found for the selected studies.",
                    remote_id=remote.id, remote_name=remote.name,
                    steps=[ToolStep(name=f"Find {noun}s", ok=False, message=browse_error or f"No {noun}s found.")],
                )
            entities = rows
            browse_step = ToolStep(
                name=f"Find {noun}s", ok=True,
                message=f"Found {len(rows)} {noun}" + ("" if len(rows) == 1 else "s")
                + (f" (last error: {browse_error})" if browse_error else "") + " across the selected studies.",
                duration_ms=elapsed_ms(browse_started),
            )
        entities = entities[:MAX_ENTITIES_PER_RUN]

        region = parse_region(
            options.get("region_x"), options.get("region_y"), options.get("region_width"), options.get("region_height")
        )
        uid_mode = str(options.get("uid_mode") or "new").strip()
        if uid_mode not in UID_MODES:
            uid_mode = "new"

        config_remotes = options.get("_remotes") or {}
        destination_id = str(options.get("destination_remote_id") or remote.id).strip() or remote.id
        destination = config_remotes.get(destination_id) or remote

        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        storage_enabled = bool(options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False)))
        storage_running = bool(options.get("_storage_running", False))
        storage_error = str(options.get("_storage_error") or "").strip()
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae, port=int(local.port), calling_ae=local.ae_title,
            enabled=storage_enabled, running=storage_running, storage_error=storage_error,
        )
        if blocked:
            return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary=blocked, remote_id=remote.id, remote_name=remote.name)

        started = time.perf_counter()
        steps: list[ToolStep] = [browse_step] if browse_step else []
        records: list[dict[str, Any]] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, move_error, contexts = _retrieve_many(local, remote, entities, dest_ae)
            if not datasets:
                steps.append(ToolStep(
                    name="C-MOVE", ok=False, message=move_error or "No instances were stored on this AE.",
                    duration_ms=elapsed_ms(move_started),
                ))
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=move_error or "Nothing retrieved; nothing redacted.",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            steps.append(ToolStep(
                name="C-MOVE", ok=True,
                message=f"Retrieved {len(datasets)} instance(s) from {len(entities)} selection(s)"
                + (f". Last error: {move_error}" if move_error else ""),
                duration_ms=elapsed_ms(move_started),
            ))

            redact_started = time.perf_counter()
            cleaned: list[Dataset] = []
            for ds in datasets:
                sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
                try:
                    redact_pixels(ds, region)
                except RedactionError as exc:
                    records.append({
                        "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "")),
                        "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "")),
                        "sop_instance_uid": sop_uid,
                        "status": "failed",
                        "error": str(exc),
                    })
                    continue
                if uid_mode == "new":
                    _renumber_for_new_uid(ds)
                cleaned.append(ds)
            steps.append(ToolStep(
                name="Redact", ok=bool(cleaned),
                message=f"Redacted {len(cleaned)} of {len(datasets)} instance(s) "
                f"(x={region.x}, y={region.y}, w={region.width or 'full'}, h={region.height})",
                duration_ms=elapsed_ms(redact_started),
            ))

            send_started = time.perf_counter()
            stored, send_error = _send_back(local, destination, cleaned)
            for index, ds in enumerate(cleaned):
                if index < stored:
                    status, err = "sent", ""
                elif index == stored:
                    status, err = "failed", send_error or "C-STORE failed"
                else:
                    status, err = "failed", "Not attempted — association stopped after a prior C-STORE failure."
                records.append({
                    "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "")),
                    "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "")),
                    "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "")),
                    "status": status,
                    "error": err,
                })
            steps.append(ToolStep(
                name="C-STORE", ok=stored == len(cleaned) and not send_error,
                message=f"Sent {stored} of {len(cleaned)} cleaned instance(s) to {destination.name} "
                f"as {'new' if uid_mode == 'new' else 'original'} SOP Instance UID(s)"
                + (f". Error: {send_error}" if send_error else ""),
                duration_ms=elapsed_ms(send_started),
            ))
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        summary = (
            f"Redacted and sent {stored} instance(s) to {destination.name}"
            if ok else (next((s.message for s in steps if not s.ok), "Cleaner run failed"))
        )
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts, records=records,
        )


register(CleanerTool())
