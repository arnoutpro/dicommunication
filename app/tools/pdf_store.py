"""Encapsulate PDFs as DICOM Encapsulated PDF Storage and C-STORE them."""

from __future__ import annotations

import time
from typing import Any

from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian
from pynetdicom.sop_class import EncapsulatedPDFStorage

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.pdf_dicom import (
    CollectError,
    collect_pdfs,
    encapsulate_sources,
    resolve_patient_identities,
)
from app.tools.base import BaseTool, elapsed_ms
from app.tools.registry import register

PDF_TRANSFER_SYNTAXES = [ExplicitVRLittleEndian, ImplicitVRLittleEndian]


def _flag(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


class PdfStoreTool(BaseTool):
    id = "pdf-store"
    name = "PDF to DICOM"
    description = (
        "Import PDF files, a ZIP of PDFs, or a local directory, wrap each as "
        "Encapsulated PDF Storage (modality DOC), and C-STORE the instances to a PACS."
    )
    category = "dimse"
    requires_remote = False
    template = "pdf_store.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        options = options or {}
        started = time.perf_counter()
        steps: list[ToolStep] = []
        generate_name = _flag(options.get("generate_name"))
        generate_id = _flag(options.get("generate_id"))
        unique_patient = _flag(options.get("unique_patient"))
        patient_name = str(options.get("patient_name") or "").strip()
        patient_id = str(options.get("patient_id") or "").strip()
        if unique_patient:
            generate_name = True
            generate_id = True
        if not unique_patient and not generate_name and not patient_name:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Patient Name is required, or enable Generate Patient Name.",
                duration_ms=elapsed_ms(started),
            )
        if not unique_patient and not generate_id and not patient_id:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Patient ID is required, or enable Generate Patient ID.",
                duration_ms=elapsed_ms(started),
            )

        send = _flag(options["send"], default=True) if "send" in options else True
        if send and remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node to store on PACS, or uncheck Store on PACS.",
                duration_ms=elapsed_ms(started),
            )

        try:
            collect_started = time.perf_counter()
            sources, warnings = collect_pdfs(options)
        except CollectError as exc:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=str(exc),
                duration_ms=elapsed_ms(started),
                steps=[
                    ToolStep(
                        name="Collect",
                        ok=False,
                        message=str(exc),
                        duration_ms=elapsed_ms(started),
                    )
                ],
            )

        if not sources:
            detail = "No PDF files found."
            if warnings:
                detail = f"{detail} {warnings[0]}"
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=detail,
                duration_ms=elapsed_ms(started),
                steps=[
                    ToolStep(
                        name="Collect",
                        ok=False,
                        message="; ".join(warnings) if warnings else detail,
                        duration_ms=elapsed_ms(started),
                    )
                ],
            )

        collect_message = f"{len(sources)} PDF{'s' if len(sources) != 1 else ''}"
        if warnings:
            collect_message = f"{collect_message}; {len(warnings)} skipped"
        steps.append(
            ToolStep(
                name="Collect",
                ok=True,
                message=collect_message,
                duration_ms=elapsed_ms(collect_started),
                details={"warnings": warnings} if warnings else {},
            )
        )
        if warnings:
            steps.append(
                ToolStep(
                    name="Skipped",
                    ok=True,
                    message="; ".join(warnings[:8])
                    + (f"; {len(warnings) - 8} more" if len(warnings) > 8 else ""),
                )
            )

        encapsulate_started = time.perf_counter()
        try:
            identities = resolve_patient_identities(
                sources,
                patient_name=patient_name,
                patient_id=patient_id,
                generate_name=generate_name,
                generate_id=generate_id,
                unique_patient=unique_patient,
            )
        except CollectError as exc:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=str(exc),
                duration_ms=elapsed_ms(started),
            )
        same_study = _flag(options.get("same_study"), default=not unique_patient)
        instances = encapsulate_sources(
            sources,
            patient_name=identities[0][0],
            patient_id=identities[0][1],
            accession_number=str(options.get("accession_number") or ""),
            study_description=str(options.get("study_description") or ""),
            document_title=str(options.get("document_title") or ""),
            same_study=same_study,
            identities=identities,
        )
        steps.append(
            ToolStep(
                name="Encapsulate",
                ok=True,
                message=(
                    f"{len(instances)} Encapsulated PDF Storage instance"
                    f"{'s' if len(instances) != 1 else ''}"
                ),
                duration_ms=elapsed_ms(encapsulate_started),
            )
        )

        records = [
            {
                "source": source.name,
                "patient_name": str(instance.PatientName),
                "patient_id": str(instance.PatientID),
                "accession_number": str(instance.AccessionNumber or ""),
                "document_title": str(instance.DocumentTitle),
                "sop_instance_uid": str(instance.SOPInstanceUID),
                "study_instance_uid": str(instance.StudyInstanceUID),
                "sop_class": "Encapsulated PDF Storage",
                "status": "encapsulated",
            }
            for source, instance in zip(sources, instances)
        ]

        if not send:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=True,
                summary=(
                    f"Encapsulated {len(instances)} PDF"
                    f"{'s' if len(instances) != 1 else ''} (not sent)"
                ),
                duration_ms=elapsed_ms(started),
                steps=steps,
                records=records,
            )

        contexts: list[dict[str, Any]] = []
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(
                    local,
                    remote,
                    [EncapsulatedPDFStorage],
                    transfer_syntaxes=PDF_TRANSFER_SYNTAXES,
                )
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Encapsulated PDF Storage",
                    "This node is not a Storage SCP for DICOM PDFs. C-ECHO can still succeed.",
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
                                "Association opened, but Encapsulated PDF Storage was not "
                                "accepted. This node is not a Storage SCP for DICOM PDFs."
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
                            message="Encapsulated PDF Storage accepted",
                            duration_ms=elapsed_ms(assoc_started),
                        )
                    )
                    for record, instance in zip(records, instances):
                        store_started = time.perf_counter()
                        status = assoc.send_c_store(instance)
                        if status:
                            code = int(status.Status)
                            ok = code == 0x0000
                            record["status"] = "stored" if ok else f"0x{code:04X}"
                            steps.append(
                                ToolStep(
                                    name=f"C-STORE {record['source']}",
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
                                    name=f"C-STORE {record['source']}",
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
        failed = next((step for step in steps if not step.ok), None)
        stored_count = sum(1 for row in records if row.get("status") == "stored")
        if ok:
            summary = f"Stored {stored_count} Encapsulated PDF{'s' if stored_count != 1 else ''} on {remote.ae_title}"
        else:
            summary = failed.message if failed else "PDF to DICOM failed"
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


register(PdfStoreTool())
