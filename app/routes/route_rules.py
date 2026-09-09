"""Dicom Router page: scheduled C-FIND rules, with optional retrieve/forward."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import log
from app.models import RouteRule
from app.routes._shared import _as_bool, _first_error, page, templates

router = APIRouter()


def _router_view(
    request: Request,
    *,
    editing: RouteRule | None = None,
    saved: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    store = request.app.state.store
    return templates.TemplateResponse(
        request,
        "router.html",
        page(
            request,
            nav="router",
            rules=store.list_route_rules(),
            editing=editing,
            saved=saved,
            error=error,
        ),
        status_code=status_code,
    )


@router.get("/router", response_class=HTMLResponse)
def router_page(request: Request, edit: str | None = None, saved: str | None = None) -> HTMLResponse:
    editing = request.app.state.store.get_route_rule(edit) if edit else None
    return _router_view(request, editing=editing, saved=saved)


@router.get("/router/{rule_id}/runs", response_class=HTMLResponse)
def router_runs_page(request: Request, rule_id: str) -> HTMLResponse:
    store = request.app.state.store
    rule = store.get_route_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Route rule not found")
    return templates.TemplateResponse(
        request,
        "router_runs.html",
        page(
            request,
            nav="router",
            rule=rule,
            runs=store.list_route_runs(rule_id=rule_id, limit=50),
        ),
    )


@router.post("/router")
async def add_or_update_route_rule(request: Request):
    form = await request.form()
    rule_id = str(form.get("rule_id") or "")
    daily_times = [
        part.strip() for part in str(form.get("daily_times") or "").split(",") if part.strip()
    ]
    days_of_week = [int(value) for value in form.getlist("days_of_week")]
    destination_remote_ids = [value for value in form.getlist("destination_remote_ids") if value]

    try:
        rule = RouteRule(
            name=str(form.get("name") or ""),
            enabled=_as_bool(str(form.get("enabled") or "")),
            source_remote_id=str(form.get("source_remote_id") or ""),
            level=str(form.get("level") or "STUDY"),  # type: ignore[arg-type]
            modality=str(form.get("modality") or ""),
            date_scope=str(form.get("date_scope") or "today"),  # type: ignore[arg-type]
            date_last_n_days=int(form.get("date_last_n_days") or 1),
            station_ae_title=str(form.get("station_ae_title") or ""),
            schedule_mode=str(form.get("schedule_mode") or "interval"),  # type: ignore[arg-type]
            interval_minutes=int(form.get("interval_minutes") or 15),
            daily_times=daily_times,
            days_of_week=days_of_week,
            destination_remote_ids=destination_remote_ids,
        )
    except (ValidationError, ValueError) as exc:
        store = request.app.state.store
        editing = store.get_route_rule(rule_id) if rule_id else None
        message = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
        return _router_view(request, editing=editing, error=message, status_code=400)

    if rule_id:
        try:
            request.app.state.store.update_route_rule(rule_id, rule)
        except KeyError:
            raise HTTPException(status_code=404, detail="Route rule not found") from None
        log.info("Updated route rule %s", rule.name)
        return RedirectResponse("/router?saved=rule", status_code=303)
    request.app.state.store.add_route_rule(rule)
    log.info("Added route rule %s", rule.name)
    return RedirectResponse("/router?saved=rule", status_code=303)


@router.post("/router/{rule_id}/delete")
def delete_route_rule(request: Request, rule_id: str):
    request.app.state.store.delete_route_rule(rule_id)
    log.info("Deleted route rule %s", rule_id)
    return RedirectResponse("/router?saved=deleted", status_code=303)


@router.post("/router/{rule_id}/toggle")
def toggle_route_rule(request: Request, rule_id: str):
    store = request.app.state.store
    rule = store.get_route_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Route rule not found")
    rule.enabled = not rule.enabled
    store.update_route_rule(rule_id, rule)
    log.info("Route rule %s %s", rule.name, "enabled" if rule.enabled else "disabled")
    return RedirectResponse("/router?saved=rule", status_code=303)


@router.post("/router/{rule_id}/run-now")
def run_route_rule_now(request: Request, rule_id: str):
    store = request.app.state.store
    rule = store.get_route_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Route rule not found")
    scheduler = request.app.state.router_scheduler
    run = scheduler.run_rule(rule)
    log.info(
        "Ran route rule %s on demand: matched %s, new %s",
        rule.name,
        run.matched_count,
        run.new_count,
    )
    return RedirectResponse(f"/router/{rule_id}/runs?saved=run", status_code=303)
