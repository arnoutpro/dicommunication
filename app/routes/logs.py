"""Log viewer and logging-settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import clear_log, format_size, log_path, read_tail, viewer_lines
from app.models import LoggingSettings
from app.routes._shared import _first_error, apply_logging, page, templates

router = APIRouter()


def _log_view_payload(request: Request) -> dict[str, object]:
    path = log_path(request.app.state.store.data_dir)
    text = read_tail(path)
    size = path.stat().st_size if path.is_file() else 0
    return {
        "log_path": str(path),
        "log_text": text,
        "log_lines": viewer_lines(text),
        "log_size": size,
        "log_empty": not text.strip(),
        "log_size_label": format_size(size),
    }


def _logs_page(
    request: Request,
    *,
    saved: str | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "logs.html",
        page(request, nav="logs", saved=saved, error=error, **_log_view_payload(request)),
        status_code=status_code,
    )


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, saved: str | None = None) -> HTMLResponse:
    return _logs_page(request, saved=saved)


@router.get("/logs/live", response_class=HTMLResponse)
def logs_live(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/log_view.html",
        {**page(request, nav="logs"), **_log_view_payload(request)},
    )


@router.get("/logs/download")
def logs_download(request: Request):
    path = log_path(request.app.state.store.data_dir)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(path, filename="dicommunication.log", media_type="text/plain")


@router.post("/logs")
def save_logging(
    request: Request,
    level: str = Form(...),
    max_megabytes: int = Form(...),
    backup_count: int = Form(3),
):
    try:
        settings = LoggingSettings(
            level=level,  # type: ignore[arg-type]
            max_bytes=max_megabytes * 1024 * 1024,
            backup_count=backup_count,
        )
    except ValidationError as exc:
        return _logs_page(request, error=_first_error(exc), status_code=400)
    apply_logging(request, settings)
    return RedirectResponse("/logs?saved=logging", status_code=303)


@router.post("/logs/clear")
def logs_clear(request: Request):
    settings = request.app.state.store.load().logging
    clear_log(request.app.state.store.data_dir, settings)
    return RedirectResponse("/logs?saved=cleared", status_code=303)
