"""Dicom Send page and run route.

Reached at ``/tools/dicom-send``. Same combined-form shape as Dicom Cleaner: "query" runs the
Study-level C-FIND on the source archive and re-renders the page with a checkable study table;
"run" reads the checked studies plus the destination from the same page and does the retrieve
and the send.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import ToolResult
from app.routes._shared import execute_tool, page, study_query_from_form, templates
from app.tools import get_tool
from app.tools.find_keys import normalize_da

router = APIRouter()

LEVELS = ("STUDY", "SERIES", "IMAGE")


def dicom_send_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    level: str = "STUDY",
    action: str = "query",
    status_code: int = 200,
    remote_id: str = "",
    identity_id: str = "",
    destination_id: str = "",
    query: dict[str, Any] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dicom_send.html",
        page(
            request,
            nav="tools",
            tool=get_tool("dicom-send"),
            tool_id="dicom-send",
            result=result,
            level=level if level in LEVELS else "STUDY",
            action=action,
            remote_id=remote_id,
            identity_id=identity_id,
            destination_id=destination_id,
            query=query or {},
        ),
        status_code=status_code,
    )


@router.post("/tools/dicom-send/run")
async def dicom_send_run(request: Request) -> HTMLResponse:
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    destination_id = str(form.get("destination_remote_id") or "")
    action = str(form.get("action") or "query").strip()
    level = str(form.get("level") or "STUDY").strip().upper()
    if level not in LEVELS:
        level = "STUDY"
    # The query fields sit in the same form as the run options, so they come back with either action
    # and can be put into the page again afterwards.
    query = study_query_from_form(form)

    options: dict = {"action": action, "level": level}
    if action == "run":
        options["study_uids"] = [uid for uid in form.getlist("study_uid") if uid]
        options["destination_remote_id"] = destination_id
    else:
        options["patient_id"] = query["patient_id"]
        options["accession_number"] = query["accession_number"]
        options["study_date"] = normalize_da(query["study_date"])
        options["modality"] = "\\".join(query["modalities"])

    common = dict(level=level, action=action, remote_id=remote_id, identity_id=identity_id, destination_id=destination_id, query=query)
    try:
        result = execute_tool(request, "dicom-send", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(tool_id="dicom-send", tool_name=get_tool("dicom-send").name, ok=False, summary=str(exc.detail))
        return dicom_send_page(request, result=failure, status_code=exc.status_code, **common)
    return dicom_send_page(request, result=result, **common)
