"""Dicom Router page: scheduled C-FIND rules, with optional retrieve/forward."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import log
from app.models import RouteRule
from app.routes._shared import _first_error, page, templates

router = APIRouter()


def _router_view(
    request: Request,
    *,
    editing: RouteRule | None = None,
    saved: str | None = None,
    error: str | None = None,
    nav: str = "router",
    status_code: int = 200,
) -> HTMLResponse:
    store = request.app.state.store
    scheduler = request.app.state.router_scheduler
    rules = store.list_route_rules()
    return templates.TemplateResponse(
        request,
        "router.html",
        page(
            request,
            nav=nav,
            rules=rules,
            running_ids={rule.id for rule in rules if scheduler.is_running(rule.id)},
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
def router_runs_page(request: Request, rule_id: str, saved: str | None = None) -> HTMLResponse:
    store = request.app.state.store
    rule = store.get_route_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Route rule not found")
    scheduler = request.app.state.router_scheduler
    return templates.TemplateResponse(
        request,
        "router_runs.html",
        page(
            request,
            nav="router",
            rule=rule,
            running=scheduler.is_running(rule_id),
            runs=store.list_route_runs(rule_id=rule_id, limit=50),
            saved=saved,
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


@router.post("/router/{rule_id}/start")
def start_route_rule(request: Request, rule_id: str):
    """Mark active and run right now — resumes wherever a paused schedule left off."""
    scheduler = request.app.state.router_scheduler
    try:
        outcome = scheduler.start_rule(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Route rule not found") from None
    log.info("Started route rule %s (%s)", rule_id, outcome)
    saved = "already-running" if outcome == "already_running" else "started"
    return RedirectResponse(f"/router?saved={saved}", status_code=303)


@router.post("/router/{rule_id}/pause")
def pause_route_rule(request: Request, rule_id: str):
    """Stop scheduling; interrupt a run in progress after its current target."""
    scheduler = request.app.state.router_scheduler
    try:
        scheduler.request_pause(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Route rule not found") from None
    log.info("Paused route rule %s", rule_id)
    return RedirectResponse("/router?saved=paused", status_code=303)


@router.post("/router/{rule_id}/stop")
def stop_route_rule(request: Request, rule_id: str):
    """Stop scheduling and clear the next run; interrupt a run in progress."""
    scheduler = request.app.state.router_scheduler
    try:
        scheduler.request_stop(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Route rule not found") from None
    log.info("Stopped route rule %s", rule_id)
    return RedirectResponse("/router?saved=stopped", status_code=303)


@router.post("/router/{rule_id}/run-now")
def run_route_rule_now(request: Request, rule_id: str):
    """Run once in the background without touching the rule's status/schedule."""
    scheduler = request.app.state.router_scheduler
    try:
        outcome = scheduler.run_now(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Route rule not found") from None
    log.info("Ran route rule %s on demand (%s)", rule_id, outcome)
    saved = "already-running" if outcome == "already_running" else "running"
    return RedirectResponse(f"/router/{rule_id}/runs?saved={saved}", status_code=303)
