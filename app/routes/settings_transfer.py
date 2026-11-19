"""Configuration > Import / export: move the app's settings between computers.

Export downloads one JSON file of the sections you tick. Import is two steps:
upload the file and read the preview, then confirm. The uploaded file is held in
memory between the two steps (never written to disk) and dropped after use or
after a while. See app/settings_transfer.py for what is and is not included.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.applog import log
from app.routes._shared import page, templates
from app.settings_transfer import (
    ADD,
    DEFAULT_EXPORT,
    DEFAULT_IMPORT,
    MAX_BYTES,
    SECTION_LABELS,
    SECTION_RULES,
    SECTIONS,
    SAME,
    SettingsError,
    UPDATE,
    apply_import,
    export_bytes,
    export_filename,
    parse_settings,
    plan_import,
    section_counts,
)

router = APIRouter()
BASE = "/config/transfer"
PENDING_SECONDS = 20 * 60
MAX_PENDING = 5


def _pending(request: Request) -> dict[str, dict[str, Any]]:
    held = getattr(request.app.state, "settings_pending", None)
    if held is None:
        held = request.app.state.settings_pending = {}
    now = time.monotonic()
    for token in [t for t, item in held.items() if now - item["at"] > PENDING_SECONDS]:
        del held[token]
    return held


def _render(
    request: Request,
    *,
    flash: str = "",
    flash_ok: bool = False,
    token: str = "",
    export_checked: tuple[str, ...] = DEFAULT_EXPORT,
    status_code: int = 200,
) -> HTMLResponse:
    store = request.app.state.store
    preview = None
    held = _pending(request).get(token) if token else None
    if held is not None:
        parsed = held["parsed"]
        present = parsed.present()
        plan = plan_import(store, parsed, present)
        preview = {
            "token": token,
            "filename": held["filename"],
            "parsed": parsed,
            "plan": plan,
            "present": present,
            "checked": [section for section in present if section in DEFAULT_IMPORT],
            "groups": [
                {"section": section, "label": SECTION_LABELS[section], "rows": [item for item in plan.items if item.section == section]}
                for section in plan.sections()
            ],
        }
    return templates.TemplateResponse(
        request,
        "config_transfer.html",
        page(
            request,
            nav="config-transfer",
            flash=flash,
            flash_ok=flash_ok,
            sections=SECTIONS,
            labels=SECTION_LABELS,
            counts=section_counts(store),
            export_checked=export_checked,
            preview=preview,
            max_mb=MAX_BYTES // (1024 * 1024),
            ADD=ADD,
            UPDATE=UPDATE,
            SAME=SAME,
            base=BASE,
        ),
        status_code=status_code,
    )


@router.get(BASE, response_class=HTMLResponse)
def transfer_page(request: Request) -> HTMLResponse:
    return _render(request)


@router.post(BASE + "/export")
async def transfer_export(request: Request):
    form = await request.form()
    chosen = tuple(section for section in SECTIONS if section in form.getlist("section"))
    if not chosen:
        return _render(request, flash="Tick at least one thing to export.", export_checked=(), status_code=400)
    log.info("Settings exported: %s", ", ".join(chosen))  # which sections only, never the content
    return Response(
        content=export_bytes(request.app.state.store, chosen),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{export_filename()}"', "Cache-Control": "no-store"},
    )


@router.post(BASE + "/preview")
async def transfer_preview(request: Request, file: UploadFile | None = None):
    if file is None or not file.filename:
        return _render(request, flash="Choose a settings file first.", status_code=400)
    data = await file.read(MAX_BYTES + 1)
    try:
        parsed = parse_settings(data)
    except SettingsError as exc:
        return _render(request, flash=str(exc), status_code=400)
    if not parsed.present():
        message = "The file has nothing to import."
        if parsed.problems:
            message += " " + " ".join(parsed.problems)
        return _render(request, flash=message, status_code=400)
    held = _pending(request)
    while len(held) >= MAX_PENDING:
        del held[min(held, key=lambda t: held[t]["at"])]
    token = secrets.token_urlsafe(12)
    held[token] = {"parsed": parsed, "filename": file.filename, "at": time.monotonic()}
    return _render(request, token=token)


@router.post(BASE + "/apply")
async def transfer_apply(request: Request):
    form = await request.form()
    token = str(form.get("token") or "")
    held = _pending(request).get(token)
    if held is None:
        return _render(request, flash="That file is no longer held. Choose it again.", status_code=404)
    parsed = held["parsed"]
    chosen = [section for section in SECTIONS if section in form.getlist("section") and section in parsed.present()]
    if not chosen:
        return _render(request, token=token, flash="Tick at least one part of the file to import.", status_code=400)
    store = request.app.state.store
    plan = plan_import(store, parsed, chosen)
    try:
        result = apply_import(store, plan, on_error=lambda exc: log.exception("Settings import failed"))
    except SettingsError as exc:
        return _render(request, token=token, flash=str(exc), status_code=500)
    _pending(request).pop(token, None)
    log.info("Settings imported: %s added, %s updated, %s unchanged", result.added, result.updated, result.unchanged)
    parts = [f"{result.added} added", f"{result.updated} updated", f"{result.unchanged} already the same"]
    message = "Imported: " + ", ".join(parts) + "."
    if SECTION_RULES in chosen and plan.count(SECTION_RULES, ADD):
        message += " New router rules were added stopped; start them from the Dicom Router page when you are ready."
    return _render(request, flash=message, flash_ok=True)


@router.post(BASE + "/cancel")
async def transfer_cancel(request: Request):
    form = await request.form()
    _pending(request).pop(str(form.get("token") or ""), None)
    return RedirectResponse(BASE, status_code=303)
