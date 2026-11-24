"""Status dashboard: one tile per Router rule, from the rule's newest run."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from datetime import date, timedelta

from app.lite_status import HISTORY_RANGES, history_for_rule, tile_for_rule
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


@router.get("/lite/api/history")
def lite_history(request: Request, days: int = 7) -> JSONResponse:
    """Exams per day for each rule, from the daily counts the Router keeps (counts only)."""
    days = days if days in HISTORY_RANGES else HISTORY_RANGES[0]
    store = request.app.state.store
    today = date.today()
    rules = [history_for_rule(rule, store.route_daily(rule.id), days, today) for rule in store.list_route_rules()]
    return JSONResponse(
        {"days": [(today - timedelta(days=d)).isoformat() for d in range(days - 1, -1, -1)], "ranges": list(HISTORY_RANGES), "rules": rules}
    )
