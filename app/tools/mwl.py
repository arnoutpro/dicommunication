"""Modality Worklist C-FIND SCU against a remote DMWL node."""

from __future__ import annotations

from typing import Any

from app.models import LocalAE, RemoteNode, ToolResult, ToolStep, WorklistQuery
from app.mwl import query_remote
from app.tools.base import BaseTool
from app.tools.registry import register


class MwlFindTool(BaseTool):
    id = "mwl-find"
    name = "MWL C-FIND"
    description = (
        "Query a Modality Worklist SCP with MWL C-FIND (SOP Class "
        "1.2.840.10008.5.1.4.31). This is not Study Root C-FIND: it returns scheduled "
        "procedures, not stored studies. Use the Worklist page for a table view."
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
                summary="Select a remote DICOM node that provides Modality Worklist.",
            )
        query = WorklistQuery.model_validate(options or {})
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
                "station_ae_title": entry.station_ae_title,
                "requested_procedure_description": entry.requested_procedure_description,
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


register(MwlFindTool())
