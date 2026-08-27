"""Study Root C-FIND at STUDY, SERIES, or IMAGE with selectable return keys.

Follow-ups list Structured Report series (Modality SR) and optionally C-MOVE
those series onto the local Storage SCP so the Content Sequence can be parsed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from pydicom.dataset import Dataset
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
    uid_name,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.mwl_scp import STORAGE_INBOX
from app.sr import is_structured_report, parse_sr
from app.tools.base import BaseTool
from app.tools.find_keys import (
    LEVEL_LABELS,
    MAX_RECORDS,
    build_identifier,
    keys_for_level,
    options_from_payload,
    record_from_dataset,
    selected_keywords,
    validate_query,
)
from app.tools.registry import register

PENDING = {0xFF00, 0xFF01}
MAX_SR_STUDIES = 80
MAX_SR_RETRIEVE = 20
SR_SERIES_RETURN = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "Modality",
    "SeriesNumber",
    "SeriesDescription",
    "SeriesDate",
    "NumberOfSeriesRelatedInstances",
]
SR_LIST_COLUMNS = [
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "ModalitiesInStudy",
    "StudyInstanceUID",
    "Modality",
    "SeriesNumber",
    "SeriesDescription",
    "SeriesDate",
    "SeriesInstanceUID",
    "NumberOfSeriesRelatedInstances",
]
SR_CONTENT_COLUMNS = [
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "DocumentTitle",
    "Findings",
    "Impression",
    "CompletionFlag",
    "VerificationFlag",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "SOPClass",
    "sr_text",
]
MOVE_HINTS = {
    0xA801: "Move Destination unknown — Vue does not have this AE as a C-MOVE destination.",
    0xA702: "Unable to perform sub-operations (C-STORE to this AE failed).",
    0xC002: "Unable to process the C-MOVE identifier.",
}


def retrieve_storage_gate_message(
    *,
    dest_ae: str,
    port: int,
    calling_ae: str,
    enabled: bool,
    running: bool,
    storage_error: str = "",
) -> str | None:
    """Explain why retrieve cannot C-MOVE yet. None when the local Storage SCP is up."""
    dest = dest_ae.strip() or "the Local AE Title"
    if not enabled:
        text = (
            f"Accept C-STORE is off. On Local DICOM AE enable Accept C-STORE of "
            f"Structured Reports and save. Vue must list {dest} as a C-MOVE destination "
            f"at this host, listen port {port}."
        )
        calling = calling_ae.strip()
        if calling and calling != dest:
            text += (
                f" Present as {calling} is only the calling AE for C-FIND; Vue C-STOREs "
                f"the report to {dest}, not to a viewer that uses that title."
            )
        return text
    if not running:
        return storage_error or (
            "The local Storage SCP is not listening. Save Local DICOM AE with "
            "Accept C-STORE enabled, and allow inbound TCP on the listen port "
            f"{port} as {dest}."
        )
    return None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _noun(level: str, count: int) -> str:
    if level == "STUDY":
        return "study" if count == 1 else "studies"
    if level == "SERIES":
        return "series"
    return "image" if count == 1 else "images"


def _cell(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (dict, list)):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _plain_row(row: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in row.items():
        if key == "sr_items" or isinstance(value, (dict, list)):
            continue
        out[str(key)] = "" if value is None else str(value)
    return out


def _rows_from_options(options: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = options.get("studies")
    if raw is None:
        raw = options.get("records")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [_plain_row(item) for item in raw if isinstance(item, Mapping)]


def _collect_c_find(assoc, identifier: Dataset, columns: list[str], cap: int = MAX_RECORDS):
    records: list[dict[str, str]] = []
    truncated = False
    failed_status = None
    for status, identifier_ds in assoc.send_c_find(
        identifier, StudyRootQueryRetrieveInformationModelFind
    ):
        if not status:
            failed_status = "No C-FIND response (timeout, abort, or invalid PDU)."
            break
        code = int(status.Status)
        if code in PENDING and identifier_ds is not None:
            if len(records) >= cap:
                truncated = True
                continue
            records.append(record_from_dataset(identifier_ds, columns))
        elif code == 0x0000:
            continue
        else:
            failed_status = f"C-FIND status 0x{code:04X}"
            break
    return records, truncated, failed_status


def _move_status_message(code: int) -> str:
    hint = MOVE_HINTS.get(code)
    if hint:
        return f"C-MOVE status 0x{code:04X}. {hint}"
    return f"C-MOVE status 0x{code:04X}"


class CFindAdvancedTool(BaseTool):
    id = "c-find-advanced"
    name = "Vue PACS Database Analytics"
    description = (
        "Query Vue PACS (and other Study Root Q/R SCPs) at Study, Series, or Image "
        "with every searchable key, including optional Tamar / ELSCINT1 tags. "
        "Series and Image follow hierarchical FIND: parent Unique keys must be present. "
        "List Structured Report series (Modality SR) from Study results, then C-MOVE "
        "those series to this AE and parse the DICOM Content Sequence. No language model. "
        "Results are a column-aligned table with copy, CSV, and JSON export. "
        "This searches stored studies, not the modality worklist."
    )
    category = "dimse"
    template = "c_find_advanced.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node first.",
            )

        try:
            parsed = options_from_payload(options)
        except ValueError as exc:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=str(exc),
                remote_id=remote.id,
                remote_name=remote.name,
            )

        follow = str((options or {}).get("follow") or parsed.get("follow") or "").strip()
        if follow == "sr_series":
            return self._list_sr_series(local, remote, options or {}, parsed)
        if follow == "retrieve_sr":
            return self._retrieve_sr(local, remote, options or {}, parsed)

        level = parsed["level"]
        values = parsed["values"]
        return_keys = parsed["return_keys"]
        blocked = validate_query(level, values)
        if blocked:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=blocked,
                remote_id=remote.id,
                remote_name=remote.name,
            )

        columns = selected_keywords(level, values, return_keys)
        if not columns:
            columns = [key.keyword for key in keys_for_level(level)]

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, str]] = []
        truncated = False
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                identifier = build_identifier(level, values, columns)
            except (ValueError, TypeError, KeyError) as exc:
                return ToolResult(
                    tool_id=self.id,
                    tool_name=self.name,
                    ok=False,
                    summary=f"Could not build the C-FIND identifier: {exc}",
                    remote_id=remote.id,
                    remote_name=remote.name,
                )
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(
                    local, remote, [StudyRootQueryRetrieveInformationModelFind]
                )
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Study Root Query/Retrieve FIND",
                    "This is not a Q/R SCP (and it is not MWL).",
                )
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or (
                                "Association opened, but Study Root Query/Retrieve FIND "
                                "was not accepted. This is not a Q/R SCP (and it is not MWL)."
                            ),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message=f"Study Root FIND SOP Class accepted · {LEVEL_LABELS[level]} level",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    find_started = time.perf_counter()
                    records, truncated, failed_status = _collect_c_find(assoc, identifier, columns)
                    if failed_status:
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=False,
                                message=failed_status,
                                duration_ms=_elapsed_ms(find_started),
                            )
                        )
                    else:
                        extra = f" (first {MAX_RECORDS})" if truncated else ""
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=True,
                                message=(
                                    f"{len(records)} {_noun(level, len(records))} at "
                                    f"{LEVEL_LABELS[level]} level{extra}"
                                ),
                                duration_ms=_elapsed_ms(find_started),
                                details={
                                    "count": len(records),
                                    "level": level,
                                    "truncated": truncated,
                                },
                            )
                        )
                    assoc.release()
                    steps.append(ToolStep(name="Release", ok=True, message="Association released"))
            except Exception as exc:  # noqa: BLE001
                steps.append(
                    ToolStep(
                        name="C-FIND",
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                        duration_ms=_elapsed_ms(started),
                    )
                )
            finally:
                if assoc is not None and getattr(assoc, "is_established", False):
                    try:
                        assoc.abort()
                    except Exception:  # noqa: BLE001
                        pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        extra = f" (stopped at {MAX_RECORDS})" if truncated else ""
        summary = (
            f"Study Root C-FIND ({LEVEL_LABELS[level]}) returned {len(records)} "
            f"{_noun(level, len(records))} from {remote.ae_title}{extra}"
            if ok
            else (failed.message if failed else "C-FIND Advanced failed")
        )
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )

    def _list_sr_series(
        self,
        local: LocalAE,
        remote: RemoteNode,
        options: Mapping[str, Any],
        parsed: Mapping[str, Any],
    ) -> ToolResult:
        studies = _rows_from_options({**parsed, **options})
        if not studies:
            uid = str(parsed.get("values", {}).get("StudyInstanceUID") or "").strip()
            if uid:
                studies = [{"StudyInstanceUID": uid}]
        studies = [row for row in studies if _cell(row, "StudyInstanceUID")]
        if not studies:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="No Study Instance UID in the result list. Run a Study query first, then List SR reports.",
                remote_id=remote.id,
                remote_name=remote.name,
            )

        truncated_studies = False
        if len(studies) > MAX_SR_STUDIES:
            studies = studies[:MAX_SR_STUDIES]
            truncated_studies = True

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, str]] = []
        failed_studies = 0
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(
                    local, remote, [StudyRootQueryRetrieveInformationModelFind]
                )
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Study Root Query/Retrieve FIND",
                    "This is not a Q/R SCP (and it is not MWL).",
                )
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or "Study Root Query/Retrieve FIND was not accepted.",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message="Study Root FIND SOP Class accepted · Series level, Modality SR",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    find_started = time.perf_counter()
                    for study in studies:
                        uid = _cell(study, "StudyInstanceUID")
                        identifier = build_identifier(
                            "SERIES",
                            {"StudyInstanceUID": uid, "Modality": "SR"},
                            SR_SERIES_RETURN,
                        )
                        series_rows, _trunc, failed_status = _collect_c_find(
                            assoc, identifier, SR_SERIES_RETURN
                        )
                        if failed_status:
                            failed_studies += 1
                            continue
                        for series in series_rows:
                            merged = {key: "" for key in SR_LIST_COLUMNS}
                            for key in SR_LIST_COLUMNS:
                                merged[key] = _cell(series, key) or _cell(study, key)
                            records.append(merged)
                    extra = f"; first {MAX_SR_STUDIES} studies" if truncated_studies else ""
                    skipped = f"; {failed_studies} study queries failed" if failed_studies else ""
                    steps.append(
                        ToolStep(
                            name="C-FIND",
                            ok=failed_studies < len(studies),
                            message=(
                                f"{len(records)} Structured Report series across "
                                f"{len(studies)} studies{extra}{skipped}"
                            ),
                            duration_ms=_elapsed_ms(find_started),
                            details={
                                "count": len(records),
                                "level": "SERIES",
                                "kind": "sr_series",
                                "studies": len(studies),
                                "failed_studies": failed_studies,
                                "truncated": truncated_studies,
                            },
                        )
                    )
                    assoc.release()
                    steps.append(ToolStep(name="Release", ok=True, message="Association released"))
            except Exception as exc:  # noqa: BLE001
                steps.append(
                    ToolStep(
                        name="C-FIND",
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                        duration_ms=_elapsed_ms(started),
                    )
                )
            finally:
                if assoc is not None and getattr(assoc, "is_established", False):
                    try:
                        assoc.abort()
                    except Exception:  # noqa: BLE001
                        pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        if ok:
            extra = f" (first {MAX_SR_STUDIES} studies)" if truncated_studies else ""
            summary = (
                f"Listed {len(records)} Structured Report series (Modality SR) "
                f"from {len(studies)} studies on {remote.ae_title}{extra}"
            )
        else:
            summary = failed.message if failed else "Could not list Structured Report series"
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )

    def _retrieve_sr(
        self,
        local: LocalAE,
        remote: RemoteNode,
        options: Mapping[str, Any],
        parsed: Mapping[str, Any],
    ) -> ToolResult:
        storage_enabled = bool(options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False)))
        storage_running = bool(options.get("_storage_running", False))
        storage_error = str(options.get("_storage_error") or "").strip()
        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        calling_ae = local.ae_title
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae,
            port=int(local.port),
            calling_ae=calling_ae,
            enabled=storage_enabled,
            running=storage_running,
            storage_error=storage_error,
        )
        if blocked:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=blocked,
                remote_id=remote.id,
                remote_name=remote.name,
            )

        series_rows = [
            row
            for row in _rows_from_options({**parsed, **options})
            if _cell(row, "StudyInstanceUID") and _cell(row, "SeriesInstanceUID")
        ]
        series_rows = [
            row
            for row in series_rows
            if (not _cell(row, "Modality")) or _cell(row, "Modality").upper() == "SR"
        ]
        if not series_rows:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=(
                    "No SR series in this list. From Study results click List SR reports "
                    "(Modality SR), then Retrieve report text."
                ),
                remote_id=remote.id,
                remote_name=remote.name,
            )

        truncated = False
        if len(series_rows) > MAX_SR_RETRIEVE:
            series_rows = series_rows[:MAX_SR_RETRIEVE]
            truncated = True

        move_local = local.model_copy(
            update={"timeout_seconds": max(float(local.timeout_seconds), 60.0)}
        )
        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        assoc = None
        STORAGE_INBOX.begin()
        with capture_pynetdicom_log() as log_stream:
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(
                    move_local, remote, [StudyRootQueryRetrieveInformationModelMove]
                )
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Study Root Query/Retrieve MOVE",
                    "Vue must accept C-MOVE (not C-GET) and know this AE as a destination.",
                )
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or "Study Root Query/Retrieve MOVE was not accepted.",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message=(
                                f"Study Root MOVE SOP Class accepted · destination {dest_ae}"
                            ),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    move_started = time.perf_counter()
                    failed_status = None
                    moved = 0
                    for row in series_rows:
                        identifier = Dataset()
                        identifier.QueryRetrieveLevel = "SERIES"
                        identifier.StudyInstanceUID = _cell(row, "StudyInstanceUID")
                        identifier.SeriesInstanceUID = _cell(row, "SeriesInstanceUID")
                        sop_uid = _cell(row, "SOPInstanceUID")
                        if sop_uid:
                            identifier.QueryRetrieveLevel = "IMAGE"
                            identifier.SOPInstanceUID = sop_uid
                        for status, _remaining in assoc.send_c_move(
                            identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove
                        ):
                            if not status:
                                failed_status = (
                                    "No C-MOVE response (timeout, abort, or invalid PDU)."
                                )
                                break
                            code = int(status.Status)
                            if code in PENDING:
                                continue
                            if code == 0x0000:
                                moved += 1
                                break
                            failed_status = _move_status_message(code)
                            if code == 0xA801:
                                failed_status += (
                                    f" Destination AE Title is {dest_ae}. Vue must list it "
                                    "as a C-MOVE destination at this workstation’s host:port."
                                )
                            break
                        if failed_status:
                            break
                    datasets = STORAGE_INBOX.finish()
                    if failed_status and not datasets:
                        steps.append(
                            ToolStep(
                                name="C-MOVE",
                                ok=False,
                                message=failed_status,
                                duration_ms=_elapsed_ms(move_started),
                            )
                        )
                    else:
                        parsed_count = 0
                        skipped = 0
                        for dataset in datasets:
                            if not is_structured_report(dataset):
                                skipped += 1
                                continue
                            parsed_sr = parse_sr(dataset)
                            parent = next(
                                (
                                    row
                                    for row in series_rows
                                    if _cell(row, "SeriesInstanceUID")
                                    == parsed_sr["series_instance_uid"]
                                ),
                                {},
                            )
                            record: dict[str, Any] = {key: "" for key in SR_CONTENT_COLUMNS}
                            for key in (
                                "PatientName",
                                "PatientID",
                                "StudyDate",
                                "AccessionNumber",
                                "StudyDescription",
                            ):
                                record[key] = _stringify_attr(dataset, key) or _cell(parent, key)
                            record["DocumentTitle"] = str(parsed_sr["document_title"])
                            record["Findings"] = str(parsed_sr["findings"])
                            record["Impression"] = str(parsed_sr["impression"])
                            record["CompletionFlag"] = str(parsed_sr["completion_flag"])
                            record["VerificationFlag"] = str(parsed_sr["verification_flag"])
                            record["StudyInstanceUID"] = str(parsed_sr["study_instance_uid"])
                            record["SeriesInstanceUID"] = str(parsed_sr["series_instance_uid"])
                            record["SOPInstanceUID"] = str(parsed_sr["sop_instance_uid"])
                            record["SOPClass"] = uid_name(parsed_sr["sop_class_uid"])
                            record["sr_text"] = str(parsed_sr["text"])
                            record["sr_items"] = parsed_sr["items"]
                            records.append(record)
                            parsed_count += 1
                        extra = f" (first {MAX_SR_RETRIEVE} series)" if truncated else ""
                        skip_note = f"; skipped {skipped} non-SR objects" if skipped else ""
                        move_ok = failed_status is None
                        steps.append(
                            ToolStep(
                                name="C-MOVE",
                                ok=move_ok or parsed_count > 0,
                                message=(
                                    failed_status
                                    or (
                                        f"Retrieved {parsed_count} Structured Reports from "
                                        f"{moved} series moves to {dest_ae}{extra}{skip_note}"
                                    )
                                ),
                                duration_ms=_elapsed_ms(move_started),
                                details={
                                    "count": parsed_count,
                                    "level": "SERIES",
                                    "kind": "sr_content",
                                    "truncated": truncated,
                                    "destination_ae": dest_ae,
                                },
                            )
                        )
                    assoc.release()
                    steps.append(ToolStep(name="Release", ok=True, message="Association released"))
            except Exception as exc:  # noqa: BLE001
                steps.append(
                    ToolStep(
                        name="C-MOVE",
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                        duration_ms=_elapsed_ms(started),
                    )
                )
            finally:
                STORAGE_INBOX.finish()
                if assoc is not None and getattr(assoc, "is_established", False):
                    try:
                        assoc.abort()
                    except Exception:  # noqa: BLE001
                        pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        if ok:
            extra = f" (first {MAX_SR_RETRIEVE} series)" if truncated else ""
            summary = (
                f"Parsed {len(records)} Structured Reports from {remote.ae_title}{extra}. "
                "Values come from the DICOM Content Sequence, not a language model."
            )
        else:
            summary = failed.message if failed else "Could not retrieve Structured Reports"
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )


def _stringify_attr(dataset: Dataset, keyword: str) -> str:
    value = getattr(dataset, keyword, None)
    if value is None:
        return ""
    return str(value).strip()


register(CFindAdvancedTool())
