"""Dicom Router page: scheduled C-FIND rules, with optional retrieve/forward."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import log
from app.models import RouteRule, SavedQuery
from app.routes._shared import page, templates

router = APIRouter()


def _draft_from_saved_query(saved_query: SavedQuery) -> RouteRule:
    """A new rule's starting point, copied once from a saved Dicomtag Analytics
    query — not a live link, so editing the saved query afterwards has no
    effect on rules already loaded from it.

    Study Date is left to the rule's own "Study date" control (a schedule
    re-derives "today" on every run; the saved query's own date value was a
    one-off snapshot). Modality maps onto the rule's existing field; every
    other filter value the query had (accession number, a private Vue tag,
    ...) carries over as an extra matching key.
    """
    extra_query = {
        keyword: value
        for keyword, value in saved_query.values.items()
        if keyword not in ("StudyDate", "ModalitiesInStudy") and value
    }
    return RouteRule.model_construct(
        name=f"{saved_query.name} (scheduled)",
        source_remote_id=saved_query.remote_id,
        modality=saved_query.values.get("ModalitiesInStudy", ""),
        extra_query=extra_query,
    )


def _router_view(
    request: Request,
    *,
    editing: RouteRule | None = None,
    adding: bool = False,
    draft: RouteRule | None = None,
    error_field: str | None = None,
    saved: str | None = None,
    error: str | None = None,
    nav: str = "router",
    status_code: int = 200,
) -> HTMLResponse:
    # The page leads with the rule list; the form shows only when asked for,
    # when a save failed, or when there are no rules yet to list.
    show_form = bool(editing or adding or error or not request.app.state.store.list_route_rules())
    values = draft or editing
    return templates.TemplateResponse(
        request,
        "router.html",
        page(
            request,
            nav=nav,
            editing=editing,
            show_form=show_form,
            values=values,
            extra_query_json=json.dumps(values.extra_query) if values else "{}",
            error_field=error_field,
            saved=saved,
            error=error,
        ),
        status_code=status_code,
    )


@router.get("/router", response_class=HTMLResponse)
def router_page(
    request: Request,
    edit: str | None = None,
    new: str | None = None,
    saved: str | None = None,
    load_query: str | None = None,
) -> HTMLResponse:
    editing = request.app.state.store.get_route_rule(edit) if edit else None
    draft = None
    error = None
    if load_query and not editing:
        saved_query = request.app.state.store.get_saved_query(load_query)
        if saved_query is None:
            error = "That saved query no longer exists."
        elif saved_query.level != "STUDY":
            error = "Only a Study-level saved query can be loaded into a route rule."
        else:
            draft = _draft_from_saved_query(saved_query)
    return _router_view(
        request, editing=editing, adding=bool(new) or draft is not None, draft=draft, saved=saved, error=error
    )


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


def _int_or_raw(value: object, default: int) -> int | str:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return text


def _extra_query_from_form(raw: object) -> dict[str, str]:
    """The hidden ``extra_query_json`` field: filters copied in when a saved
    query was loaded, round-tripped through edits/saves as plain text (the
    form has no per-field inputs for an open-ended set of DICOM keywords).
    """
    try:
        parsed = json.loads(str(raw or "") or "{}")
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value).strip() for key, value in parsed.items() if str(value).strip()}


def _rule_error(exc: ValueError, fields: dict) -> tuple[str, str | None]:
    """The message to show and the form field it belongs to (None: the whole form)."""
    if not isinstance(exc, ValidationError):
        return str(exc), None
    error = exc.errors()[0]
    message = str(error.get("msg") or "Invalid value").removeprefix("Value error, ")
    loc = error.get("loc") or ()
    if loc:
        return message, str(loc[0])
    # The only whole-model check is a daily schedule without times.
    return message, "daily_times" if fields.get("schedule_mode") == "daily" else None


@router.post("/router")
async def add_or_update_route_rule(request: Request):
    form = await request.form()
    rule_id = str(form.get("rule_id") or "")
    fields = {
        "name": str(form.get("name") or ""),
        "source_remote_id": str(form.get("source_remote_id") or ""),
        "level": str(form.get("level") or "STUDY"),
        "modality": str(form.get("modality") or ""),
        "date_scope": str(form.get("date_scope") or "today"),
        "date_last_n_days": _int_or_raw(form.get("date_last_n_days"), 1),
        "station_ae_title": str(form.get("station_ae_title") or ""),
        "schedule_mode": str(form.get("schedule_mode") or "interval"),
        "interval_minutes": _int_or_raw(form.get("interval_minutes"), 15),
        "daily_times": [part.strip() for part in str(form.get("daily_times") or "").split(",") if part.strip()],
        "days_of_week": [_int_or_raw(value, 0) for value in form.getlist("days_of_week")],
        "destination_remote_ids": [value for value in form.getlist("destination_remote_ids") if value],
        "export_dir": str(form.get("export_dir") or ""),
        "extra_query": _extra_query_from_form(form.get("extra_query_json")),
    }

    try:
        rule = RouteRule(**fields)
    except (ValidationError, ValueError) as exc:
        message, error_field = _rule_error(exc, fields)
        # Put back what was typed, unvalidated, so a mistake costs one field
        # rather than the whole form.
        draft = RouteRule.model_construct(**fields)
        if rule_id:
            draft.id = rule_id
        return _router_view(
            request,
            editing=draft if rule_id else None,
            draft=draft,
            error=message,
            error_field=error_field,
            status_code=400,
        )

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
