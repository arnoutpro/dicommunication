"""Health check, dashboard, about, and help pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import __version__
from app.echo_board import snapshot as echo_board_snapshot
from app.routes._shared import page, templates
from app.routes.anonymize import _anonymize_page
from app.routes.tools import _c_find_advanced_page
from app.shell import SHELL_ANONYMIZE, SHELL_DICOMM, SHELL_VUE

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    shell = getattr(request.state, "shell", SHELL_DICOMM)
    if shell == SHELL_VUE:
        return _c_find_advanced_page(request, nav="home")
    if shell == SHELL_ANONYMIZE:
        return _anonymize_page(request, nav="home")
    return templates.TemplateResponse(
        request,
        "index.html",
        page(request, nav="home", echo_board=echo_board_snapshot(request.app.state.store)),
    )


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request) -> HTMLResponse:
    vue = getattr(request.state, "shell", SHELL_DICOMM) == SHELL_VUE
    return templates.TemplateResponse(
        request,
        "about_vue.html" if vue else "about.html",
        page(request, nav="about", data_dir=str(request.app.state.store.data_dir)),
    )


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    vue = getattr(request.state, "shell", SHELL_DICOMM) == SHELL_VUE
    return templates.TemplateResponse(
        request,
        "help_vue.html" if vue else "help.html",
        page(request, nav="help", data_dir=str(request.app.state.store.data_dir)),
    )
