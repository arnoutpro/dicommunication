"""Simulated C-STORE: send a tiny Secondary Capture instance to a Storage SCP."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import PYDICOM_IMPLEMENTATION_UID, ImplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import SecondaryCaptureImageStorage

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


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def build_test_instance() -> Dataset:
    now = datetime.now(timezone.utc)
    sop_uid = generate_uid()
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.FileMetaInformationVersion = b"\x00\x01"
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = "ARNPRO^TESTBENCH"
    ds.PatientID = "ARNPRO-TEST"
    ds.PatientBirthDate = "19700101"
    ds.PatientSex = "O"
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.StudyID = "TESTBENCH"
    ds.AccessionNumber = "TB" + now.strftime("%H%M%S")
    ds.Modality = "OT"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.Manufacturer = "Arnout.pro"
    ds.InstitutionName = "Dicommunication Tool"
    ds.Rows = 16
    ds.Columns = 16
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = bytes(range(256))
    return ds


class CStoreTool(BaseTool):
    id = "c-store"
    name = "C-STORE"
    description = (
        "Send a simulated Secondary Capture instance (16×16 test image, patient "
        "ARNPRO^TESTBENCH) to the remote Storage SCP. This is not C-ECHO: the peer "
        "must accept the Secondary Capture Image Storage SOP Class."
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

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                instance = build_test_instance()
                assoc_started = time.perf_counter()
                _ae, assoc = associate(local, remote, [SecondaryCaptureImageStorage])
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Secondary Capture Image Storage",
                    "This node is not a Storage SCP for that SOP Class.",
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
                                "Association opened, but Secondary Capture Image Storage "
                                "was not accepted. This node is not a Storage SCP for that SOP Class."
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
                            message="Storage SOP Class accepted",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    store_started = time.perf_counter()
                    status = assoc.send_c_store(instance)
                    if status:
                        code = int(status.Status)
                        ok = code == 0x0000
                        steps.append(
                            ToolStep(
                                name="C-STORE",
                                ok=ok,
                                message=(
                                    f"DIMSE status 0x{code:04X} Success"
                                    if ok
                                    else f"DIMSE status 0x{code:04X}"
                                ),
                                duration_ms=_elapsed_ms(store_started),
                                details={"status": f"0x{code:04X}"},
                            )
                        )
                        records.append(
                            {
                                "patient_name": str(instance.PatientName),
                                "patient_id": str(instance.PatientID),
                                "accession_number": str(instance.AccessionNumber),
                                "sop_instance_uid": str(instance.SOPInstanceUID),
                                "study_instance_uid": str(instance.StudyInstanceUID),
                                "sop_class": "Secondary Capture Image Storage",
                            }
                        )
                    else:
                        steps.append(
                            ToolStep(
                                name="C-STORE",
                                ok=False,
                                message="No C-STORE response (timeout, abort, or invalid PDU).",
                                duration_ms=_elapsed_ms(store_started),
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
            f"Stored test instance {records[0]['sop_instance_uid']} on {remote.ae_title}"
            if ok and records
            else (failed.message if failed else "C-STORE failed")
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


register(CStoreTool())
