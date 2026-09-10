"""Dicom Cleaner page and run route.

Reached at ``/cleaner/`` (see app.shell); the middleware strips that
prefix, so this module just handles the plain ``/tools/dicom-cleaner`` and
``/tools/dicom-cleaner/run`` paths underneath it. Same combined-form shape
as Dicom Anonymizer: "query" runs the Study-level C-FIND and re-renders the
page with a checkable study table; "run" reads the checked studies plus
the redact region / UID mode / destination from the same page and does the
retrieve + redact + send-back.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.fs_dialog import dialogs_available
from app.models import ToolResult
from app.routes._shared import execute_tool, page, templates
from app.tools import get_tool
from app.tools.cleaner import LEVELS, UID_MODES
from app.tools.find_keys import normalize_da

router = APIRouter()


def _cleaner_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    level: str = "STUDY",
    action: str = "query",
    nav: str = "tools",
    status_code: int = 200,
    remote_id: str = "",
    identity_id: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "cleaner.html",
        page(
            request,
            nav=nav,
            tool=get_tool("dicom-cleaner"),
            tool_id="dicom-cleaner",
            result=result,
            level=level if level in LEVELS else "STUDY",
            action=action,
            remote_id=remote_id,
            identity_id=identity_id,
            dialogs_available=dialogs_available(),
        ),
        status_code=status_code,
    )


@router.post("/tools/dicom-cleaner/run")
async def cleaner_run(request: Request) -> HTMLResponse:
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    action = str(form.get("action") or "query").strip()
    level = str(form.get("level") or "STUDY").strip().upper()
    if level not in LEVELS:
        level = "STUDY"

    options: dict = {"action": action, "level": level}
    if action == "run":
        options["study_uids"] = [uid for uid in form.getlist("study_uid") if uid]
        options["region_x"] = str(form.get("region_x") or "0")
        options["region_y"] = str(form.get("region_y") or "0")
        options["region_width"] = str(form.get("region_width") or "0")
        options["region_height"] = str(form.get("region_height") or "100")
        uid_mode = str(form.get("uid_mode") or "new").strip()
        options["uid_mode"] = uid_mode if uid_mode in UID_MODES else "new"
        options["destination_remote_id"] = str(form.get("destination_remote_id") or "")
    else:
        options["patient_id"] = str(form.get("patient_id") or "")
        options["accession_number"] = str(form.get("accession_number") or "")
        options["study_date"] = normalize_da(str(form.get("study_date") or ""))
        options["modality"] = "\\".join(v for v in form.getlist("modality") if v)

    try:
        result = execute_tool(request, "dicom-cleaner", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(
            tool_id="dicom-cleaner", tool_name=get_tool("dicom-cleaner").name, ok=False, summary=str(exc.detail),
        )
        return _cleaner_page(
            request, result=failure, level=level, action=action, status_code=exc.status_code,
            remote_id=remote_id, identity_id=identity_id,
        )
    return _cleaner_page(request, result=result, level=level, action=action, remote_id=remote_id, identity_id=identity_id)
