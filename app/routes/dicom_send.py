"""Dicom Send page and run route.

Reached at ``/tools/dicom-send``. Same combined-form shape as Dicom Cleaner: "query" runs the
Study-level C-FIND on the source archive and re-renders the page with a checkable study table;
"run" reads the checked studies plus the destination from the same page and does the retrieve
and the send.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import ToolResult
from app.routes._shared import execute_tool, page, templates
from app.tools import get_tool
from app.tools.find_keys import catalog_payload, column_labels, extra_rows, is_extra_keyword, options_from_form

router = APIRouter()


class _AtStudyLevel:
    """The submitted form, always read at Study level: the Send at choice of step 3 is not a query level here."""

    def __init__(self, form: Any) -> None:
        self._form = form

    def get(self, name: str, default: Any = None) -> Any:
        return "STUDY" if name == "level" else self._form.get(name, default)

    def getlist(self, name: str) -> list:
        return [] if name == "level" else self._form.getlist(name)

    def keys(self):
        return self._form.keys()

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
    query = query or {}
    prefill = SimpleNamespace(values=query.get("values") or {}, include=query.get("include") or []) if query else None
    extras = [kw for kw in [*(query.get("include") or []), *(query.get("values") or {})] if is_extra_keyword(kw)]
    columns = list(result.records[0].keys()) if result is not None and result.ok and result.records else []
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
            query=query,
            find_catalog=catalog_payload(),
            prefill=prefill,
            date_preset=query.get("date_preset", ""),
            find_extra_rows=extra_rows(extras, query.get("include") or [], query.get("values") or {}) if extras else [],
            find_extra_keywords=extras,
            column_labels=dict(zip(columns, column_labels(columns))),
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
    # The query form is the Dicomtag Analytics one (at Study level), and sits in the same form as the run
    # options, so it comes back with either action and can be put into the page again afterwards.
    fields = options_from_form(_AtStudyLevel(form))
    query = {
        "values": {keyword: str(value) for keyword, value in fields["values"].items()},
        "include": [str(item) for item in form.getlist("include") if item],
        "date_preset": str(form.get("date_preset") or ""),
    }

    options: dict = {"action": action, "level": level}
    if action == "run":
        options["study_uids"] = [uid for uid in form.getlist("study_uid") if uid]
        options["destination_remote_id"] = destination_id
    else:
        options["values"] = fields["values"]
        options["return_keys"] = fields["return_keys"]

    common = dict(level=level, action=action, remote_id=remote_id, identity_id=identity_id, destination_id=destination_id, query=query)
    try:
        result = execute_tool(request, "dicom-send", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(tool_id="dicom-send", tool_name=get_tool("dicom-send").name, ok=False, summary=str(exc.detail))
        return dicom_send_page(request, result=failure, status_code=exc.status_code, **common)
    return dicom_send_page(request, result=result, **common)
