"""C-ECHO board page and run routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.applog import log
from app.echo_board import run_all as run_echo_board
from app.echo_board import snapshot as echo_board_snapshot
from app.routes._shared import _hx, page, templates

router = APIRouter()


@router.get("/echo-board", response_class=HTMLResponse)
def echo_board_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "echo_board.html",
        page(
            request,
            nav="echo-board",
            echo_board=echo_board_snapshot(request.app.state.store),
        ),
    )


@router.post("/echo-board/run")
def echo_board_run(request: Request):
    board = run_echo_board(request.app.state.store)
    log.info(
        "C-ECHO board: %s passed, %s failed, %s unknown of %s",
        board.passed,
        board.failed,
        board.unknown,
        board.total,
    )
    if _hx(request):
        return templates.TemplateResponse(
            request,
            "partials/echo_board.html",
            {"request": request, "echo_board": board, "config": request.app.state.store.load()},
        )
    return templates.TemplateResponse(
        request,
        "echo_board.html",
        page(request, nav="echo-board", echo_board=board),
    )
