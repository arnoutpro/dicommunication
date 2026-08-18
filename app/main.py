"""FastAPI application: config screens, tool runner, and a small JSON API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from app.models import LocalAE, RemoteNode, ToolResult
from app.store import ConfigStore
from app.tools import get_tool, list_tools

BASE_DIR = Path(__file__).resolve().parent


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = error.get("loc", ("field",))[-1]
    return f"{location}: {error.get('msg', 'invalid value')}"


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


class RunRequest(BaseModel):
    remote_id: str


def create_app(store: ConfigStore | None = None) -> FastAPI:
    app = FastAPI(
        title="Dicommunication",
        description="Low-code DICOM communication validator and PACS admin toolkit.",
        version="0.1.0",
    )
    app.state.store = store or ConfigStore()
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def page(request: Request, **extra: object) -> dict:
        config = app.state.store.load()
        return {
            "request": request,
            "config": config,
            "tools": list_tools(),
            "results": app.state.store.list_results(10),
            **extra,
        }

    def execute_tool(tool_id: str, remote_id: str) -> ToolResult:
        config = app.state.store.load()
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        remote = config.get_remote(remote_id)
        if tool.requires_remote and remote is None:
            raise HTTPException(status_code=400, detail="A remote DICOM node is required")
        result = tool.run(config.local, remote)
        app.state.store.add_result(result)
        return result

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse("index.html", page(request, nav="home"))

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request, edit: str | None = None, saved: str | None = None) -> HTMLResponse:
        config = app.state.store.load()
        editing = config.get_remote(edit) if edit else None
        return templates.TemplateResponse(
            "config.html",
            page(
                request,
                nav="config",
                editing=editing,
                saved=saved,
                error=None,
            ),
        )

    @app.post("/config/local")
    def save_local(
        request: Request,
        ae_title: str = Form(...),
        host: str = Form(...),
        port: int = Form(...),
        timeout_seconds: float = Form(...),
        max_pdu: int = Form(...),
        implementation_version: str = Form(""),
    ):
        try:
            local = LocalAE(
                ae_title=ae_title,
                host=host,
                port=port,
                timeout_seconds=timeout_seconds,
                max_pdu=max_pdu,
                implementation_version=implementation_version,
            )
        except ValidationError as exc:
            return templates.TemplateResponse(
                "config.html",
                page(request, nav="config", editing=None, saved=None, error=_first_error(exc)),
                status_code=400,
            )
        app.state.store.save_local(local)
        return RedirectResponse("/config?saved=local", status_code=303)

    @app.post("/config/remotes")
    def add_or_update_remote(
        request: Request,
        name: str = Form(...),
        ae_title: str = Form(...),
        host: str = Form(...),
        port: int = Form(...),
        notes: str = Form(""),
        remote_id: str = Form(""),
    ):
        try:
            remote = RemoteNode(
                name=name,
                ae_title=ae_title,
                host=host,
                port=port,
                notes=notes,
            )
        except ValidationError as exc:
            config = app.state.store.load()
            editing = config.get_remote(remote_id) if remote_id else None
            return templates.TemplateResponse(
                "config.html",
                page(
                    request,
                    nav="config",
                    editing=editing,
                    saved=None,
                    error=_first_error(exc),
                ),
                status_code=400,
            )
        if remote_id:
            try:
                app.state.store.update_remote(remote_id, remote)
            except KeyError:
                raise HTTPException(status_code=404, detail="Remote node not found") from None
            return RedirectResponse("/config?saved=remote", status_code=303)
        app.state.store.add_remote(remote)
        return RedirectResponse("/config?saved=remote", status_code=303)

    @app.post("/config/remotes/{remote_id}/delete")
    def delete_remote(remote_id: str):
        app.state.store.delete_remote(remote_id)
        return RedirectResponse("/config?saved=deleted", status_code=303)

    @app.get("/tools/{tool_id}", response_class=HTMLResponse)
    def tool_page(request: Request, tool_id: str) -> HTMLResponse:
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            "tool.html",
            page(request, nav="tools", tool=tool, tool_id=tool.id, result=None),
        )

    @app.post("/tools/{tool_id}/run")
    def run_tool_form(request: Request, tool_id: str, remote_id: str = Form(...)):
        try:
            result = execute_tool(tool_id, remote_id)
        except HTTPException as exc:
            failure = ToolResult(
                tool_id=tool_id,
                tool_name=tool_id,
                ok=False,
                summary=str(exc.detail),
            )
            if _hx(request):
                return templates.TemplateResponse(
                    "partials/result.html",
                    {"request": request, "result": failure},
                    status_code=exc.status_code,
                )
            raise
        if _hx(request):
            return templates.TemplateResponse(
                "partials/result.html",
                {"request": request, "result": result},
            )
        try:
            tool = get_tool(tool_id)
        except KeyError:
            tool = None
        return templates.TemplateResponse(
            "tool.html",
            page(request, nav="tools", tool=tool, tool_id=tool_id, result=result),
        )

    @app.get("/api/config")
    def api_config():
        return app.state.store.load()

    @app.put("/api/config/local")
    def api_put_local(local: LocalAE):
        return app.state.store.save_local(local)

    @app.get("/api/remotes")
    def api_remotes():
        return app.state.store.load().remotes

    @app.post("/api/remotes", status_code=201)
    def api_add_remote(remote: RemoteNode):
        app.state.store.add_remote(remote)
        return remote

    @app.put("/api/remotes/{remote_id}")
    def api_update_remote(remote_id: str, remote: RemoteNode):
        try:
            return app.state.store.update_remote(remote_id, remote)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Remote node not found") from exc

    @app.delete("/api/remotes/{remote_id}")
    def api_delete_remote(remote_id: str):
        app.state.store.delete_remote(remote_id)
        return JSONResponse({"ok": True, "id": remote_id})

    @app.get("/api/tools")
    def api_tools():
        return [
            {
                "id": tool.id,
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
            }
            for tool in list_tools()
        ]

    @app.post("/api/tools/{tool_id}/run")
    def api_run_tool(tool_id: str, body: RunRequest):
        return execute_tool(tool_id, body.remote_id)

    return app


app = create_app()
