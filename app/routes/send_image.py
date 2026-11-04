"""Send Image to PACS page and run route.

Reached at ``/tools/send-image``, same combined-form shape as Dicom
Anonymizer and Dicom Cleaner, with a choice of how Step 1 finds its study:
"query" runs a Study Root C-FIND against the PACS; "patient" runs a
Patient Root C-FIND instead, for an order whose study has no image yet
stored (Study Root C-FIND only finds a study that already has one); and
"manual" skips any lookup and takes typed-in Patient Name/ID as-is, for
when even the patient isn't registered in the PACS. All three leave
Accession Number and Study Instance UID for Step 3 to supply when a
lookup didn't return them (Study Root C-FIND's own values are still used
unless Step 3 overrides them). Either way the page re-renders with a
pickable study table; "run" reads the picked study (carried across as
JSON, since there is no re-query-by-UID needed), the chosen modality, the
Step 3 Accession Number / Study Instance UID overrides, the additional
tags, and the destination from the same page, builds the placeholder
image, and C-STOREs it.
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


def _patient_lookup_from_form(form: Any) -> dict[str, str]:
    return {
        "patient_name": str(form.get("patient_lookup_name") or "").strip(),
        "patient_id": str(form.get("patient_lookup_id") or "").strip(),
    }


def _manual_entry_from_form(form: Any) -> dict[str, str]:
    return {
        "patient_name": str(form.get("manual_patient_name") or "").strip(),
        "patient_id": str(form.get("manual_patient_id") or "").strip(),
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
    patient: dict[str, str] | None = None,
    manual: dict[str, str] | None = None,
    find_mode: str = "lookup",
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
            patient=patient or {},
            manual=manual or {},
            find_mode=find_mode,
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
    find_mode = str(form.get("find_mode") or "lookup").strip()
    query = _study_query_from_form(form)
    patient = _patient_lookup_from_form(form)
    manual = _manual_entry_from_form(form)

    options: dict[str, Any] = {"action": action}
    if action == "manual":
        find_mode = "manual"
        options.update(manual)
    elif action == "patient":
        find_mode = "patient"
        options.update(patient)
    else:
        options.update(query)
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
        options["accession_number"] = str(form.get("image_accession_number") or "").strip()
        options["study_instance_uid"] = str(form.get("image_study_instance_uid") or "").strip()
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
            remote_id=remote_id, identity_id=identity_id, query=query, patient=patient, manual=manual,
            find_mode=find_mode,
        )
    return _send_image_page(
        request, result=result, action=action,
        remote_id=remote_id, identity_id=identity_id, query=query, patient=patient, manual=manual,
        find_mode=find_mode,
    )
