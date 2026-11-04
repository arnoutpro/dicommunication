"""Query Modality Worklist for a scheduled order, then build and C-STORE a
small solid-black placeholder image referencing that order's Patient/
Study/Accession identifiers — simulating a modality completing the exam.

Same query/pick/run shape as Dicom Anonymizer and Dicom Cleaner, except the
query is MWL C-FIND (reusing app/mwl.py, same as the MWL C-FIND test tool)
rather than Study Root C-FIND, since an "order" is a scheduled procedure
step, not a stored study. Exactly one order is picked per run (one image is
sent, not a batch).
"""

from __future__ import annotations

import time
from typing import Any

from pynetdicom.sop_class import SecondaryCaptureImageStorage

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.dicom_image import build_black_image_dataset
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep, WorklistQuery
from app.mwl import query_remote
from app.tools.base import BaseTool, elapsed_ms
from app.tools.registry import register

MODALITIES = ["CR", "CT", "DX", "MG", "MR", "NM", "OT", "PT", "RF", "SC", "SR", "US", "XA"]


class SendImageTool(BaseTool):
    id = "send-image"
    name = "Send Image to PACS"
    description = (
        "Query a Modality Worklist SCP for a scheduled order, pick one, choose a "
        "modality, and C-STORE a small solid-black placeholder image (Secondary "
        "Capture) carrying that order's Patient/Study/Accession identifiers — "
        "simulating a modality completing the exam. The image has no real pixel "
        "content; it exists to exercise the storage path, not for review."
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
        return self._query(local, remote, options)

    def _query(
        self, local: LocalAE, remote: RemoteNode | None, options: dict[str, Any]
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node that provides Modality Worklist.",
            )
        query = WorklistQuery.model_validate(options)
        if not query.station_ae_title and local.station_ae_title:
            query = query.model_copy(update={"station_ae_title": local.station_ae_title})
        result = query_remote(local, remote, query)
        steps = [
            ToolStep(
                name="MWL C-FIND",
                ok=result.ok,
                message=result.summary,
                duration_ms=result.duration_ms,
                details={"count": len(result.entries)},
            )
        ]
        records = [
            {
                "patient_name": entry.patient_name,
                "patient_id": entry.patient_id,
                "accession_number": entry.accession_number,
                "modality": entry.modality,
                "scheduled_date": entry.scheduled_date,
                "requested_procedure_description": entry.requested_procedure_description,
                "study_instance_uid": entry.study_instance_uid,
            }
            for entry in result.entries
        ]
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=result.ok,
            summary=result.summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=result.duration_ms,
            steps=steps,
            log=result.log,
            contexts=result.contexts,
            records=records,
        )

    def _run(
        self, local: LocalAE, remote: RemoteNode | None, options: dict[str, Any]
    ) -> ToolResult:
        order = options.get("order") or {}
        patient_name = str(order.get("patient_name") or "").strip()
        patient_id = str(order.get("patient_id") or "").strip()
        if not patient_name or not patient_id:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary="Pick an order first."
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
            accession_number=str(order.get("accession_number") or ""),
            study_instance_uid=str(order.get("study_instance_uid") or ""),
            study_description=str(
                options.get("study_description") or order.get("requested_procedure_description") or ""
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
