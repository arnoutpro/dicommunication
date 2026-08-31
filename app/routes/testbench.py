"""Quick-service testbench page and run route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import ToolResult
from app.routes._shared import _hx, execute_tool, page, templates

router = APIRouter()

TESTBENCH_SERVICES = {
    "c-echo": "C-ECHO (Verification)",
    "c-store": "C-STORE (Secondary Capture test image)",
    "c-find": "C-FIND (Study Root Query/Retrieve)",
    "mwl-find": "MWL C-FIND (Modality Worklist)",
}


def _testbench_options(
    service: str,
    patient_name: str,
    patient_id: str,
    accession_number: str,
    study_date: str,
    modality: str,
    station_ae_title: str,
    scheduled_date: str,
) -> dict[str, Any]:
    if service == "c-find":
        return {
            "patient_name": patient_name,
            "patient_id": patient_id,
            "accession_number": accession_number,
            "study_date": study_date,
            "modality": modality,
        }
    if service == "mwl-find":
        return {
            "patient_name": patient_name,
            "patient_id": patient_id,
            "accession_number": accession_number,
            "modality": modality,
            "station_ae_title": station_ae_title,
            "scheduled_date": scheduled_date,
        }
    return {}


@router.get("/testbench", response_class=HTMLResponse)
def testbench_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "testbench.html",
        page(request, nav="testbench", result=None, service="c-echo"),
    )


@router.post("/testbench/run")
def testbench_run(
    request: Request,
    remote_id: str = Form(...),
    service: str = Form(...),
    patient_name: str = Form(""),
    patient_id: str = Form(""),
    accession_number: str = Form(""),
    study_date: str = Form(""),
    modality: str = Form(""),
    station_ae_title: str = Form(""),
    scheduled_date: str = Form(""),
    identity_id: str = Form(""),
):
    if service not in TESTBENCH_SERVICES:
        raise HTTPException(status_code=400, detail="Unknown testbench service")
    options = _testbench_options(
        service,
        patient_name,
        patient_id,
        accession_number,
        study_date,
        modality,
        station_ae_title,
        scheduled_date,
    )
    try:
        result = execute_tool(request, service, remote_id, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(
            tool_id=service,
            tool_name=TESTBENCH_SERVICES[service],
            ok=False,
            summary=str(exc.detail),
        )
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": failure},
                status_code=exc.status_code,
            )
        raise
    if _hx(request):
        return templates.TemplateResponse(
            request,
            "partials/result.html",
            {"request": request, "result": result},
        )
    return templates.TemplateResponse(
        request,
        "testbench.html",
        page(request, nav="testbench", result=result, service=service),
    )
