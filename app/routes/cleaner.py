"""Dicom Cleaner page and run route.

Reached at ``/cleaner/`` (see app.shell); the middleware strips that
prefix, so this module just handles the plain ``/tools/dicom-cleaner`` and
``/tools/dicom-cleaner/run`` paths underneath it. Same combined-form shape
as Dicom Anonymizer: "query" runs the Study-level C-FIND and re-renders the
page with a checkable study table; "run" reads the checked studies plus
the redact region / UID mode / destination from the same page and does the
retrieve + redact + send-back.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.fs_dialog import dialogs_available
from app.models import LocalAE, RemoteNode, ToolResult
from app.routes._shared import execute_tool, page, templates
from app.shell import SHELL_DICOMM, public_href
from app.tools import get_tool
from app.tools.cleaner import (
    LEVELS,
    UID_MODES,
    browse_study_images,
    retrieve_preview_instance,
    storage_gate_message,
)
from app.tools.dicom_preview import PreviewError, render_preview_png
from app.tools.find_keys import normalize_da

router = APIRouter()


def _partial_context(request: Request, **extra: object) -> dict:
    """The bare minimum `page()` provides that these HTMX-fragment partials
    need — just `href`, since they don't render the sidebar/nav chrome.
    """
    shell = getattr(request.state, "shell", SHELL_DICOMM)
    return {"request": request, "href": lambda path: public_href(path, shell=shell), **extra}


def _resolve_local_remote(request: Request, remote_id: str, identity_id: str) -> tuple[LocalAE, RemoteNode] | str:
    """Same resolution execute_tool does, for the two preview endpoints
    below that don't go through a ToolResult. Returns an error string
    instead of raising, since callers render it straight into a partial.
    """
    config = request.app.state.store.load()
    remote = config.get_remote(remote_id) if remote_id else None
    if remote is None:
        return "Select a remote DICOM node first."
    try:
        local = config.calling_ae(identity_id or None)
    except KeyError:
        return "Virtual local AE not found."
    return local, remote


def _preview_options(request: Request) -> dict:
    config = request.app.state.store.load()
    scp = getattr(request.app.state, "mwl_scp", None)
    return {
        "_listen_ae": config.local.ae_title,
        "_storage_enabled": config.local.storage_scp_enabled,
        "_storage_running": bool(scp and scp.running and config.local.storage_scp_enabled),
        "_storage_error": getattr(scp, "last_error", None) if scp else None,
    }


def _cleaner_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    level: str = "STUDY",
    action: str = "query",
    nav: str = "tools",
    status_code: int = 200,
    remote_id: str = "",
    identity_id: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "cleaner.html",
        page(
            request,
            nav=nav,
            tool=get_tool("dicom-cleaner"),
            tool_id="dicom-cleaner",
            result=result,
            level=level if level in LEVELS else "STUDY",
            action=action,
            remote_id=remote_id,
            identity_id=identity_id,
            dialogs_available=dialogs_available(),
        ),
        status_code=status_code,
    )


@router.post("/tools/dicom-cleaner/run")
async def cleaner_run(request: Request) -> HTMLResponse:
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    action = str(form.get("action") or "query").strip()
    level = str(form.get("level") or "STUDY").strip().upper()
    if level not in LEVELS:
        level = "STUDY"

    options: dict = {"action": action, "level": level}
    if action == "run":
        options["study_uids"] = [uid for uid in form.getlist("study_uid") if uid]
        options["region_x"] = str(form.get("region_x") or "0")
        options["region_y"] = str(form.get("region_y") or "0")
        options["region_width"] = str(form.get("region_width") or "0")
        options["region_height"] = str(form.get("region_height") or "100")
        uid_mode = str(form.get("uid_mode") or "new").strip()
        options["uid_mode"] = uid_mode if uid_mode in UID_MODES else "new"
        options["destination_remote_id"] = str(form.get("destination_remote_id") or "")
    else:
        options["patient_id"] = str(form.get("patient_id") or "")
        options["accession_number"] = str(form.get("accession_number") or "")
        options["study_date"] = normalize_da(str(form.get("study_date") or ""))
        options["modality"] = "\\".join(v for v in form.getlist("modality") if v)

    try:
        result = execute_tool(request, "dicom-cleaner", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(
            tool_id="dicom-cleaner", tool_name=get_tool("dicom-cleaner").name, ok=False, summary=str(exc.detail),
        )
        return _cleaner_page(
            request, result=failure, level=level, action=action, status_code=exc.status_code,
            remote_id=remote_id, identity_id=identity_id,
        )
    return _cleaner_page(request, result=result, level=level, action=action, remote_id=remote_id, identity_id=identity_id)


@router.post("/tools/dicom-cleaner/preview-images")
async def cleaner_preview_images(request: Request) -> HTMLResponse:
    """List the images in the first checked study, for the region picker."""
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    study_uid = next((uid for uid in form.getlist("study_uid") if uid), "")

    if not study_uid:
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_images.html", _partial_context(request, error="Check a study above first.")
        )

    resolved = _resolve_local_remote(request, remote_id, identity_id)
    if isinstance(resolved, str):
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_images.html", _partial_context(request, error=resolved)
        )
    local, remote = resolved

    rows, error, _contexts = browse_study_images(local, remote, study_uid)
    if not rows:
        return templates.TemplateResponse(
            request,
            "partials/cleaner_preview_images.html",
            _partial_context(request, error=error or "No images found for this study."),
        )
    return templates.TemplateResponse(
        request,
        "partials/cleaner_preview_images.html",
        _partial_context(
            request,
            images=rows,
            study_uid=study_uid,
            remote_id=remote_id,
            identity_id=identity_id,
        ),
    )


@router.post("/tools/dicom-cleaner/preview-image")
async def cleaner_preview_image(request: Request) -> HTMLResponse:
    """Retrieve one instance and render it as a PNG for the region picker."""
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    study_uid = str(form.get("study_uid") or "")
    series_uid, _sep, sop_uid = str(form.get("image") or "").partition("|")

    if not (study_uid and series_uid and sop_uid):
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_canvas.html", _partial_context(request, error="Pick an image first.")
        )

    resolved = _resolve_local_remote(request, remote_id, identity_id)
    if isinstance(resolved, str):
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_canvas.html", _partial_context(request, error=resolved)
        )
    local, remote = resolved

    options = _preview_options(request)
    blocked = storage_gate_message(local, options)
    if blocked:
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_canvas.html", {"request": request, "error": blocked}
        )

    dest_ae = str(options.get("_listen_ae") or local.ae_title)
    ds, error = retrieve_preview_instance(local, remote, study_uid, series_uid, sop_uid, dest_ae)
    if ds is None:
        return templates.TemplateResponse(
            request,
            "partials/cleaner_preview_canvas.html",
            {"request": request, "error": error or "Could not retrieve that image."},
        )

    try:
        png_bytes, orig_rows, orig_cols, png_rows, png_cols = render_preview_png(ds)
    except PreviewError as exc:
        return templates.TemplateResponse(
            request, "partials/cleaner_preview_canvas.html", {"request": request, "error": str(exc)}
        )

    return templates.TemplateResponse(
        request,
        "partials/cleaner_preview_canvas.html",
        {
            "request": request,
            "png_b64": base64.b64encode(png_bytes).decode("ascii"),
            "orig_rows": orig_rows,
            "orig_cols": orig_cols,
            "png_rows": png_rows,
            "png_cols": png_cols,
        },
    )
