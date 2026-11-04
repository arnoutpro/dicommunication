"""Send Image to PACS page and run route.

Reached at ``/tools/send-image``, same combined-form shape as Dicom
Anonymizer and Dicom Cleaner: "query" runs a Study Root C-FIND and
re-renders the page with a pickable study table; "run" reads the picked
study (carried across as JSON, since there is no re-query-by-UID needed),
the chosen modality, the additional tags, and the destination from the
same page, builds the placeholder image, and C-STOREs it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.dicom_image import IMAGE_SIZE
from app.models import ToolResult
from app.routes._shared import execute_tool, page, templates
from app.tools import get_tool
from app.tools.send_image import MODALITIES

router = APIRouter()


def _study_query_from_form(form: Any) -> dict[str, str]:
    return {
        "patient_name": str(form.get("patient_name") or "").strip(),
        "patient_id": str(form.get("patient_id") or "").strip(),
        "accession_number": str(form.get("accession_number") or "").strip(),
        "modality": str(form.get("query_modality") or "").strip(),
        "study_date": str(form.get("study_date") or "").strip(),
    }


def _parse_orders(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _send_image_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    action: str = "query",
    nav: str = "tools",
    status_code: int = 200,
    remote_id: str = "",
    identity_id: str = "",
    query: dict[str, str] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "send_image.html",
        page(
            request,
            nav=nav,
            tool=get_tool("send-image"),
            tool_id="send-image",
            result=result,
            action=action,
            remote_id=remote_id,
            identity_id=identity_id,
            query=query or {},
            send_image_modalities=MODALITIES,
            send_image_size=IMAGE_SIZE,
        ),
        status_code=status_code,
    )


@router.post("/tools/send-image/run")
async def send_image_run(request: Request) -> HTMLResponse:
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    action = str(form.get("action") or "query").strip()
    query = _study_query_from_form(form)

    options: dict[str, Any] = {"action": action, **query}
    if action == "run":
        order_index_raw = str(form.get("order_index") or "").strip()
        orders = _parse_orders(str(form.get("orders_json") or ""))
        order: dict[str, Any] = {}
        if order_index_raw.isdigit():
            index = int(order_index_raw)
            if 0 <= index < len(orders):
                order = orders[index]
        options["order"] = order
        options["modality"] = str(form.get("modality") or "").strip()
        options["study_description"] = str(form.get("study_description") or "").strip()
        options["body_part_examined"] = str(form.get("body_part_examined") or "").strip()
        options["series_description"] = str(form.get("series_description") or "").strip()
        options["destination_remote_id"] = str(form.get("destination_remote_id") or "")

    try:
        result = execute_tool(request, "send-image", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(
            tool_id="send-image", tool_name=get_tool("send-image").name, ok=False, summary=str(exc.detail),
        )
        return _send_image_page(
            request, result=failure, action=action, status_code=exc.status_code,
            remote_id=remote_id, identity_id=identity_id, query=query,
        )
    return _send_image_page(
        request, result=result, action=action,
        remote_id=remote_id, identity_id=identity_id, query=query,
    )
