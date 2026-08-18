"""Study Root Query/Retrieve C-FIND — not the same as Modality Worklist C-FIND."""

from __future__ import annotations

import time
from typing import Any

from pydicom.dataset import Dataset
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register

PENDING = {0xFF00, 0xFF01}


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _text(dataset: Dataset, name: str) -> str:
    value = getattr(dataset, name, "")
    return "" if value is None else str(value).strip()


def _record(dataset: Dataset) -> dict[str, str]:
    return {
        "patient_name": _text(dataset, "PatientName"),
        "patient_id": _text(dataset, "PatientID"),
        "accession_number": _text(dataset, "AccessionNumber"),
        "study_date": _text(dataset, "StudyDate"),
        "study_description": _text(dataset, "StudyDescription"),
        "study_instance_uid": _text(dataset, "StudyInstanceUID"),
        "modalities": _text(dataset, "ModalitiesInStudy"),
    }


class CFindTool(BaseTool):
    id = "c-find"
    name = "C-FIND"
    description = (
        "Query/Retrieve Study Root C-FIND against a PACS. This searches stored studies, "
        "not the modality worklist. MWL C-FIND uses a different SOP Class "
        "(1.2.840.10008.5.1.4.31)."
    )
    category = "dimse"

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

        options = options or {}
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
                            message="Study Root FIND SOP Class accepted",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    identifier = Dataset()
                    identifier.QueryRetrieveLevel = "STUDY"
                    identifier.PatientName = str(options.get("patient_name") or "")
                    identifier.PatientID = str(options.get("patient_id") or "")
                    identifier.AccessionNumber = str(options.get("accession_number") or "")
                    identifier.StudyDate = str(options.get("study_date") or "").replace("-", "")
                    identifier.StudyTime = ""
                    identifier.StudyID = ""
                    identifier.StudyInstanceUID = ""
                    identifier.StudyDescription = ""
                    identifier.ModalitiesInStudy = str(options.get("modality") or "")

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
                            records.append(_record(identifier_ds))
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
                                duration_ms=_elapsed_ms(find_started),
                            )
                        )
                    else:
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=True,
                                message=f"{len(records)} stud{'y' if len(records) == 1 else 'ies'} at STUDY level",
                                duration_ms=_elapsed_ms(find_started),
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
        summary = (
            f"Study Root C-FIND returned {len(records)} stud{'y' if len(records) == 1 else 'ies'} from {remote.ae_title}"
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
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )


register(CFindTool())
