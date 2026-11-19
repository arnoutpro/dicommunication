"""Status dashboard: one tile per Router rule, from the rule's newest run."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.lite_status import tile_for_rule
from app.routes._shared import templates

router = APIRouter()


@router.get("/lite", response_class=HTMLResponse)
def lite_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "lite.html", {})


@router.get("/lite/api/status")
def lite_status(request: Request) -> JSONResponse:
    store = request.app.state.store
    latest = store.latest_route_runs()
    return JSONResponse({"tiles": [tile_for_rule(rule, latest.get(rule.id)) for rule in store.list_route_rules()]})
