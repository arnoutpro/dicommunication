"""Local modality worklist page and query routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydicom.uid import generate_uid
from pydantic import ValidationError

from app.models import WorklistEntry, WorklistQuery
from app.mwl import query_worklist
from app.routes._shared import _first_error, _hx, page, templates

router = APIRouter()


@router.get("/worklist", response_class=HTMLResponse)
def worklist_page(request: Request, saved: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "worklist.html",
        page(
            request,
            nav="worklist",
            query=WorklistQuery(),
            result=None,
            source="local",
            identity_id="",
            local_entries=request.app.state.store.list_worklist(),
            saved=saved,
        ),
    )


@router.post("/worklist/query")
def worklist_query(
    request: Request,
    source: str = Form("local"),
    patient_name: str = Form(""),
    patient_id: str = Form(""),
    accession_number: str = Form(""),
    modality: str = Form(""),
    station_ae_title: str = Form(""),
    scheduled_date: str = Form(""),
    identity_id: str = Form(""),
):
    query = WorklistQuery(
        patient_name=patient_name,
        patient_id=patient_id,
        accession_number=accession_number,
        modality=modality,
        station_ae_title=station_ae_title,
        scheduled_date=scheduled_date,
    )
    result = query_worklist(request.app.state.store, source, query, identity_id or None)
    context = page(
        request,
        nav="worklist",
        query=query,
        result=result,
        source=source,
        identity_id=identity_id,
        local_entries=request.app.state.store.list_worklist(),
    )
    if _hx(request):
        return templates.TemplateResponse(request, "partials/worklist_results.html", context)
    return templates.TemplateResponse(request, "worklist.html", context)


@router.post("/worklist/entries")
def add_worklist_entry(
    request: Request,
    patient_name: str = Form(...),
    patient_id: str = Form(...),
    patient_birth_date: str = Form(""),
    patient_sex: str = Form(""),
    accession_number: str = Form(""),
    requested_procedure_id: str = Form(""),
    requested_procedure_description: str = Form(""),
    modality: str = Form("CT"),
    station_ae_title: str = Form(""),
    station_name: str = Form(""),
    scheduled_date: str = Form(""),
    scheduled_time: str = Form(""),
    scheduled_physician: str = Form(""),
):
    config = request.app.state.store.load()
    try:
        entry = WorklistEntry(
            patient_name=patient_name,
            patient_id=patient_id,
            patient_birth_date=patient_birth_date,
            patient_sex=patient_sex,
            accession_number=accession_number,
            requested_procedure_id=requested_procedure_id,
            requested_procedure_description=requested_procedure_description,
            modality=modality,
            station_ae_title=station_ae_title or config.local.station_ae_title,
            station_name=station_name,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            scheduled_physician=scheduled_physician,
            study_instance_uid=str(generate_uid()),
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "worklist.html",
            page(
                request,
                nav="worklist",
                query=WorklistQuery(),
                result=None,
                source="local",
                identity_id="",
                local_entries=request.app.state.store.list_worklist(),
                error=_first_error(exc),
            ),
            status_code=400,
        )
    request.app.state.store.add_worklist_entry(entry)
    return RedirectResponse("/worklist?saved=entry", status_code=303)


@router.post("/worklist/entries/{entry_id}/delete")
def delete_worklist_entry(request: Request, entry_id: str):
    request.app.state.store.delete_worklist_entry(entry_id)
    return RedirectResponse("/worklist?saved=deleted", status_code=303)
