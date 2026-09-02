"""Query a PACS, pick studies (and a level), anonymize them, and export.

Two actions, staged like Tag Editor's lookup -> fetch -> push:

  "query"  Study-level C-FIND on Patient ID / Accession Number / Study Date /
           Modality (Study Date required, same convention as every other
           Study-level query in this app). Returns a checkable study list.
  "run"    Given the checked studies and a level (Study / Series / Image):
           at Study level, C-MOVEs exactly those studies. At Series or Image
           level, first walks down from each checked study to every series
           (and, for Image, every image in every series) with an internal
           C-FIND, then C-MOVEs all of those — v1 has no per-series/per-image
           picker yet, so "Series" or "Image" means "every series/image in
           the studies I checked", not a further hand-picked subset.
           Anonymizes every retrieved instance with AnonBatch and writes the
           result to a chosen folder, as loose files or zipped.

Every output filename and directory is built from the *anonymized* UIDs
(never the original ones), so a filename can never leak PHI regardless of
which mode was used or whether the batch's UID map even changed anything.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from pydicom.dataset import Dataset
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.dicom_client import associate, capture_pynetdicom_log, context_rows, reject_reason, rejected_sop_message
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.mwl_scp import STORAGE_INBOX
from app.tools.anon_engine import MODE_LABELS, MODES, AnonBatch
from app.tools.base import BaseTool, elapsed_ms
from app.tools.find_advanced import retrieve_storage_gate_message
from app.tools.find_keys import build_identifier, normalize_da, record_from_dataset
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


def _parse_json_list(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


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
    local: LocalAE,
    remote: RemoteNode,
    assoc,
    level: str,
    values: dict[str, str],
    columns: list[str],
    cap: int,
) -> tuple[list[dict[str, str]], str | None]:
    identifier = build_identifier(level, values, columns)
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
            if len(records) < cap:
                records.append(record_from_dataset(identifier_ds, columns))
        elif code == 0x0000:
            continue
        else:
            error = f"C-FIND status 0x{code:04X}"
            break
    return records, error


def _browse(
    local: LocalAE, remote: RemoteNode, study_uids: list[str], level: str
) -> tuple[list[dict[str, str]], str | None, list[dict[str, Any]]]:
    """Walk down from each selected study to Series (level=SERIES) or all the
    way to every image in every series (level=IMAGE). One association, reused
    for every child query, since PACS commonly cap moves/finds per association
    but rarely mind several sequential FINDs.
    """
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
                    local,
                    remote,
                    assoc,
                    "IMAGE",
                    {"StudyInstanceUID": study_uid, "SeriesInstanceUID": series_uid},
                    IMAGE_COLUMNS,
                    MAX_BROWSE_RESULTS,
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


def _retrieve_many(
    local: LocalAE, remote: RemoteNode, entities: list[dict[str, Any]], dest_ae: str
) -> tuple[list[Dataset], str | None, list[dict[str, Any]]]:
    """One association, one C-MOVE per selected entity, one shared capture."""
    STORAGE_INBOX.begin()
    contexts: list[dict[str, Any]] = []
    assoc = None
    error: str | None = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelMove])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(
            contexts,
            "Study Root Query/Retrieve MOVE",
            "The remote must accept C-MOVE (not C-GET) and know this AE as a destination.",
        )
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve MOVE was not accepted.", contexts
        for entity in entities:
            identifier = _move_identifier(entity)
            for status, _remaining in assoc.send_c_move(
                identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove
            ):
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


def _safe_uid_component(value: str) -> str:
    return "".join(ch if (ch.isalnum() or ch == ".") else "_" for ch in str(value)) or "unknown"


def _write_anonymized(datasets: list[Dataset], staging: Path) -> list[Path]:
    written: list[Path] = []
    for dataset in datasets:
        study_dir = staging / _safe_uid_component(getattr(dataset, "StudyInstanceUID", "study"))
        series_dir = study_dir / _safe_uid_component(getattr(dataset, "SeriesInstanceUID", "series"))
        series_dir.mkdir(parents=True, exist_ok=True)
        sop_uid = _safe_uid_component(getattr(dataset, "SOPInstanceUID", f"instance-{len(written)}"))
        path = series_dir / f"{sop_uid}.dcm"
        dataset.save_as(path, enforce_file_format=True)
        written.append(path)
    return written


def _archive(staging: Path, output_dir: Path, archive: str) -> Path:
    if archive == "zip":
        stamp = time.strftime("%Y%m%d-%H%M%S")
        zip_path = output_dir / f"anonymized-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging))
        return zip_path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final_dir = output_dir / f"anonymized-{stamp}"
    shutil.move(str(staging), str(final_dir))
    return final_dir


class AnonymizeTool(BaseTool):
    id = "anonymize"
    name = "Dicom Anonymizer"
    description = (
        "Query a PACS by Patient ID, Accession Number, Study Date, and Modality, pick "
        "studies, series, or individual images, anonymize them (nuke, fuzz, remove "
        "patient info, or a custom per-tag list), and export the result as loose "
        "DICOM files or a ZIP archive."
    )
    category = "dimse"
    template = "anonymize.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Select a remote DICOM node first.",
            )
        options = options or {}
        action = str(options.get("action") or "query").strip()
        if action == "run":
            return self._run(local, remote, options)
        return self._query(local, remote, options)

    def _query(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        study_date = normalize_da(str(options.get("study_date") or "").strip())
        if not study_date:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Study Date is required.",
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
        if ok:
            message = f"Found {len(records)} stud" + ("y" if len(records) == 1 else "ies")
        else:
            message = error or "Query failed."
        steps = [ToolStep(name="C-FIND", ok=ok, message=message, duration_ms=elapsed_ms(find_started))]
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=message,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )

    def _run(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        level = str(options.get("level") or "STUDY").strip().upper()
        if level not in LEVELS:
            level = "STUDY"
        entities = _parse_json_list(options.get("entities_json") or options.get("entities"))
        if not entities:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Select at least one study first.",
                remote_id=remote.id, remote_name=remote.name,
            )

        browse_step: ToolStep | None = None
        if level != "STUDY":
            study_uids = [str(item.get("study_uid") or "") for item in entities]
            study_uids = [uid for uid in study_uids if uid]
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
                name=f"Find {noun}s",
                ok=True,
                message=f"Found {len(rows)} {noun}" + ("" if len(rows) == 1 else "s")
                + (f" (last error: {browse_error})" if browse_error else "")
                + " across the selected studies.",
                duration_ms=elapsed_ms(browse_started),
            )
        entities = entities[:MAX_ENTITIES_PER_RUN]

        mode = str(options.get("mode") or "").strip()
        if mode not in MODES:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Choose an anonymization mode first.",
                remote_id=remote.id, remote_name=remote.name,
            )
        remove_patient_erase = str(options.get("remove_patient_action") or "erase") != "fuzz"
        custom_actions_raw = options.get("custom_actions_json") or options.get("custom_actions")
        custom_actions: dict[str, dict[str, str]] = {}
        if mode == "custom":
            if isinstance(custom_actions_raw, str):
                try:
                    custom_actions = json.loads(custom_actions_raw) or {}
                except json.JSONDecodeError:
                    custom_actions = {}
            elif isinstance(custom_actions_raw, dict):
                custom_actions = custom_actions_raw

        output_dir_raw = str(options.get("output_dir") or "").strip()
        if not output_dir_raw:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Choose an output folder first.",
                remote_id=remote.id, remote_name=remote.name,
            )
        output_dir = Path(output_dir_raw).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary=f"Could not use output folder {output_dir}: {exc}",
                remote_id=remote.id, remote_name=remote.name,
            )
        archive = str(options.get("archive") or "none").strip()
        if archive not in ("none", "zip"):
            archive = "none"

        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        storage_enabled = bool(
            options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False))
        )
        storage_running = bool(options.get("_storage_running", False))
        storage_error = str(options.get("_storage_error") or "").strip()
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae, port=int(local.port), calling_ae=local.ae_title,
            enabled=storage_enabled, running=storage_running, storage_error=storage_error,
        )
        if blocked:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary=blocked,
                remote_id=remote.id, remote_name=remote.name,
            )

        started = time.perf_counter()
        steps: list[ToolStep] = [browse_step] if browse_step else []
        records: list[dict[str, Any]] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, move_error, contexts = _retrieve_many(local, remote, entities, dest_ae)
            if not datasets:
                steps.append(
                    ToolStep(
                        name="C-MOVE", ok=False,
                        message=move_error or "No instances were stored on this AE.",
                        duration_ms=elapsed_ms(move_started),
                    )
                )
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=move_error or "Nothing retrieved; nothing anonymized.",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            steps.append(
                ToolStep(
                    name="C-MOVE", ok=True,
                    message=f"Retrieved {len(datasets)} instance(s) from {len(entities)} selection(s)"
                    + (f". Last error: {move_error}" if move_error else ""),
                    duration_ms=elapsed_ms(move_started),
                )
            )

            anon_started = time.perf_counter()
            batch = AnonBatch()
            anonymized: list[Dataset] = []
            for dataset in datasets:
                try:
                    anonymized.append(
                        batch.anonymize(
                            dataset, mode,
                            remove_patient_erase=remove_patient_erase,
                            custom_actions=custom_actions,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    records.append({"sop_instance_uid": str(getattr(dataset, "SOPInstanceUID", "")), "status": f"anonymize failed: {exc}"})
            steps.append(
                ToolStep(
                    name="Anonymize",
                    ok=bool(anonymized),
                    message=f"Anonymized {len(anonymized)} of {len(datasets)} instance(s) ({MODE_LABELS.get(mode, mode)})",
                    duration_ms=elapsed_ms(anon_started),
                )
            )

            write_started = time.perf_counter()
            staging = Path(tempfile.mkdtemp(prefix="dicomm-anon-"))
            try:
                written = _write_anonymized(anonymized, staging)
                final_path = _archive(staging, output_dir, archive)
            except Exception as exc:  # noqa: BLE001
                shutil.rmtree(staging, ignore_errors=True)
                steps.append(ToolStep(name="Export", ok=False, message=f"Could not write output: {exc}"))
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=f"Could not write output: {exc}",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            steps.append(
                ToolStep(
                    name="Export", ok=True,
                    message=f"Wrote {len(written)} file(s) to {final_path}",
                    duration_ms=elapsed_ms(write_started),
                )
            )
            for dataset in anonymized:
                records.append(
                    {
                        "study_instance_uid": str(getattr(dataset, "StudyInstanceUID", "")),
                        "series_instance_uid": str(getattr(dataset, "SeriesInstanceUID", "")),
                        "sop_instance_uid": str(getattr(dataset, "SOPInstanceUID", "")),
                        "status": "written",
                    }
                )
            log = log_stream.getvalue().strip()

        ok = bool(anonymized) and bool(steps) and all(step.ok for step in steps)
        summary = (
            f"Anonymized and exported {len(anonymized)} instance(s) to {final_path}"
            if ok
            else (next((s.message for s in steps if not s.ok), "Anonymize run failed"))
        )
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )


register(AnonymizeTool())
