"""DICOM Import: scan a folder for a DICOMDIR and/or DICOM files, summarise them,
change patient and exam details, and send them to a PACS.

The work is in ``app/dicom_import.py`` (engine), ``app/import_session.py``
(background scan and send) and ``app/routes/dicom_import.py`` (the page). This
class only puts the tool in the sidebar; the page is not a one-shot run.
"""

from __future__ import annotations

from typing import Any

from app.models import LocalAE, RemoteNode, ToolResult
from app.tools.base import BaseTool
from app.tools.registry import register


class DicomImportTool(BaseTool):
    id = "dicom-import"
    name = "DICOM Import"
    description = (
        "Scan a folder for a DICOMDIR and/or DICOM files, see what is in it, change "
        "patient and exam details if needed, and send the images to a PACS. "
        "Nothing is stored: the files stay where they are and changes are applied while sending."
    )
    category = "dimse"
    template = "dicom_import.html"
    requires_remote = False

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=False,
            summary="DICOM Import is used from its page: choose a folder, review it, then send.",
        )


register(DicomImportTool())
