"""Health check, dashboard, about, and help pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import __version__
from app.echo_board import snapshot as echo_board_snapshot
from app.routes._shared import page, templates

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        page(request, nav="home", echo_board=echo_board_snapshot(request.app.state.store)),
    )


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "about.html",
        page(request, nav="about", data_dir=str(request.app.state.store.data_dir)),
    )


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "help.html",
        page(request, nav="help", data_dir=str(request.app.state.store.data_dir)),
    )
