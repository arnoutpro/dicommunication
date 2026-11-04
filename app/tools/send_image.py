"""Find a study (by Study Root C-FIND, or typed in by hand), then build and
C-STORE a small solid-black placeholder image referencing that study's
Patient/Study/Accession identifiers.

Same query/pick/run shape as Dicom Anonymizer and Dicom Cleaner. The lookup
is Study Root C-FIND (reusing app/tools/find_keys.py, same keys/identifier
building as Dicom Cleaner's own study query) rather than Modality Worklist,
since a stored study is found this way whether its order is still open or
already closed — MWL only lists orders that haven't been performed yet, so
it can't find one after the event that created it.

That still isn't enough for an order whose study has no image yet stored at
all: Study Root C-FIND only finds a study that already has at least one
stored instance, so a closed-but-empty order is invisible to both lookups.
For that case ``_manual`` takes typed-in Patient/Accession/Study Instance
UID details as-is, skipping any network lookup, and builds the same single-
record result shape ``_query`` would have returned. Exactly one study is
picked per run (one image is sent, not a batch).
"""

from __future__ import annotations

import time
from typing import Any

from pynetdicom.sop_class import SecondaryCaptureImageStorage, StudyRootQueryRetrieveInformationModelFind

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.dicom_image import build_black_image_dataset
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool, elapsed_ms
from app.tools.find_keys import build_identifier, normalize_da, record_from_dataset
from app.tools.registry import register

MODALITIES = ["CR", "CT", "DX", "MG", "MR", "NM", "OT", "PT", "RF", "SC", "SR", "US", "XA"]

PENDING = {0xFF00, 0xFF01}
MAX_QUERY_RESULTS = 500
QUERY_COLUMNS = [
    "StudyInstanceUID",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "ModalitiesInStudy",
]


class SendImageTool(BaseTool):
    id = "send-image"
    name = "Send Image to PACS"
    description = (
        "Find a study — by Study Root C-FIND (works whether its order is "
        "still open or already closed) or, for an order with no image yet "
        "stored at all, typed in by hand — pick it, choose a modality, and "
        "C-STORE a small solid-black placeholder image (Secondary Capture) "
        "carrying that study's Patient/Study/Accession identifiers. The image "
        "has no real pixel content; it exists to exercise the storage path, "
        "not for review."
    )
    category = "dimse"
    template = "send_image.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        options = options or {}
        action = str(options.get("action") or "query").strip()
        if action == "run":
            return self._run(local, remote, options)
        if action == "manual":
            return self._manual(remote, options)
        return self._query(local, remote, options)

    def _manual(
        self, remote: RemoteNode | None, options: dict[str, Any]
    ) -> ToolResult:
        patient_name = str(options.get("patient_name") or "").strip()
        patient_id = str(options.get("patient_id") or "").strip()
        if not patient_name or not patient_id:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Patient Name and Patient ID are required.",
            )
        record = {
            "PatientName": patient_name,
            "PatientID": patient_id,
            "AccessionNumber": str(options.get("accession_number") or "").strip(),
            "StudyDate": "",
            "StudyDescription": "",
            "StudyInstanceUID": str(options.get("study_instance_uid") or "").strip(),
            "ModalitiesInStudy": "",
        }
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=True,
            summary="Using manually entered details — not looked up in the PACS or Worklist.",
            remote_id=remote.id if remote else None,
            remote_name=remote.name if remote else None,
            records=[record],
        )

    def _query(
        self, local: LocalAE, remote: RemoteNode | None, options: dict[str, Any]
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node that provides Query/Retrieve (Study Root C-FIND).",
            )
        values = {
            "PatientName": str(options.get("patient_name") or "").strip(),
            "PatientID": str(options.get("patient_id") or "").strip(),
            "AccessionNumber": str(options.get("accession_number") or "").strip(),
            "StudyDate": normalize_da(str(options.get("study_date") or "")),
            "ModalitiesInStudy": str(options.get("modality") or "").strip().upper(),
        }
        values = {keyword: value for keyword, value in values.items() if value}
        identifier = build_identifier("STUDY", values, QUERY_COLUMNS)

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
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
                    "This node is not a Q/R SCP.",
                )
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=elapsed_ms(assoc_started),
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
                                "was not accepted."
                            ),
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message="Study Root FIND SOP Class accepted",
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                    find_started = time.perf_counter()
                    failed_status = None
                    for status, identifier_ds in assoc.send_c_find(
                        identifier, StudyRootQueryRetrieveInformationModelFind
                    ):
                        if not status:
                            failed_status = "No C-FIND response (timeout, abort, or invalid PDU)."
                            break
                        code = int(status.Status)
                        if code in PENDING and identifier_ds is not None:
                            if len(records) < MAX_QUERY_RESULTS:
                                records.append(record_from_dataset(identifier_ds, QUERY_COLUMNS))
                        elif code == 0x0000:
                            continue
                        else:
                            failed_status = f"C-FIND status 0x{code:04X}"
                            break
                    if failed_status:
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=False,
                                message=failed_status,
                                duration_ms=elapsed_ms(find_started),
                            )
                        )
                    else:
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=True,
                                message=f"{len(records)} stud{'y' if len(records) == 1 else 'ies'} found",
                                duration_ms=elapsed_ms(find_started),
                                details={"count": len(records)},
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
                        duration_ms=elapsed_ms(started),
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
        summary = (
            f"Study Root C-FIND found {len(records)} stud{'y' if len(records) == 1 else 'ies'} on {remote.ae_title}"
            if ok
            else (failed.message if failed else "C-FIND failed")
        )
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )

    def _run(
        self, local: LocalAE, remote: RemoteNode | None, options: dict[str, Any]
    ) -> ToolResult:
        order = options.get("order") or {}
        patient_name = str(order.get("PatientName") or "").strip()
        patient_id = str(order.get("PatientID") or "").strip()
        if not patient_name or not patient_id:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary="Pick a study first."
            )
        modality = str(options.get("modality") or "").strip().upper()
        if not modality:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary="Choose a modality."
            )

        config_remotes: dict[str, RemoteNode] = options.get("_remotes") or {}
        destination_id = str(options.get("destination_remote_id") or (remote.id if remote else "")).strip()
        destination = config_remotes.get(destination_id) or remote
        if destination is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a destination PACS node.",
            )

        dataset = build_black_image_dataset(
            patient_name=patient_name,
            patient_id=patient_id,
            modality=modality,
            accession_number=str(order.get("AccessionNumber") or ""),
            study_instance_uid=str(order.get("StudyInstanceUID") or ""),
            study_description=str(
                options.get("study_description") or order.get("StudyDescription") or ""
            ),
            body_part_examined=str(options.get("body_part_examined") or ""),
            series_description=str(options.get("series_description") or ""),
        )

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        record = {
            "patient_name": str(dataset.PatientName),
            "patient_id": str(dataset.PatientID),
            "accession_number": str(dataset.AccessionNumber or ""),
            "study_instance_uid": str(dataset.StudyInstanceUID),
            "sop_instance_uid": str(dataset.SOPInstanceUID),
            "modality": str(dataset.Modality),
            "status": "pending",
        }
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(local, destination, [SecondaryCaptureImageStorage])
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(contexts, "Secondary Capture Image Storage")
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or "Secondary Capture Image Storage was not accepted.",
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message="Secondary Capture Image Storage accepted",
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                    store_started = time.perf_counter()
                    status = assoc.send_c_store(dataset)
                    if status:
                        code = int(status.Status)
                        ok = code == 0x0000
                        record["status"] = "stored" if ok else f"0x{code:04X}"
                        steps.append(
                            ToolStep(
                                name="C-STORE",
                                ok=ok,
                                message=(
                                    f"DIMSE status 0x{code:04X} Success"
                                    if ok
                                    else f"DIMSE status 0x{code:04X}"
                                ),
                                duration_ms=elapsed_ms(store_started),
                                details={"status": f"0x{code:04X}"},
                            )
                        )
                    else:
                        record["status"] = "no response"
                        steps.append(
                            ToolStep(
                                name="C-STORE",
                                ok=False,
                                message="No C-STORE response (timeout, abort, or invalid PDU).",
                                duration_ms=elapsed_ms(store_started),
                            )
                        )
                    assoc.release()
                    steps.append(ToolStep(name="Release", ok=True, message="Association released"))
            except Exception as exc:  # noqa: BLE001
                steps.append(
                    ToolStep(
                        name="C-STORE",
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                        duration_ms=elapsed_ms(started),
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
        if ok:
            summary = f"Stored 1 {modality} image on {destination.ae_title}"
        else:
            failed = next((step for step in steps if not step.ok), None)
            summary = failed.message if failed else "Send failed"
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id if remote else None,
            remote_name=remote.name if remote else None,
            duration_ms=elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=[record],
        )


register(SendImageTool())
