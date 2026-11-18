"""Status dashboard: one tile per Router rule that has a status check key and an
export folder, read from the .status.json file the rule writes each run."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.lite_status import read_status_file
from app.router_scheduler import status_file_name
from app.routes._shared import templates

router = APIRouter()


@router.get("/lite", response_class=HTMLResponse)
def lite_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "lite.html", {})


@router.get("/lite/api/status")
def lite_status(request: Request) -> JSONResponse:
    tiles = []
    for rule in request.app.state.store.list_route_rules():
        if not (rule.export_dir and rule.status_key):
            continue
        path = Path(rule.export_dir).expanduser() / status_file_name(rule)
        tiles.append(
            read_status_file(path, rule.name, rule_status=rule.status, daily=rule.schedule_mode == "daily")
        )
    return JSONResponse({"tiles": tiles})
