"""FastAPI application: config screens, tool runner, and a small JSON API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from pydicom.uid import generate_uid

from app.echo_board import run_all as run_echo_board
from app.echo_board import snapshot as echo_board_snapshot
from app.models import LocalAE, RemoteNode, ToolResult, WorklistEntry, WorklistQuery, VirtualAE
from app.mwl import query_worklist
from app.mwl_scp import WorklistSCP
from app.store import ConfigStore
from app.tools import get_tool, list_tools, list_tools_by_category

BASE_DIR = Path(__file__).resolve().parent


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    loc = error.get("loc") or ("config",)
    return f"{loc[-1]}: {error.get('msg', 'invalid value')}"


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _as_bool(value: str | None) -> bool:
    return (value or "").lower() in {"on", "true", "1", "yes"}


class RunRequest(BaseModel):
    remote_id: str
    options: dict[str, Any] | None = None
    identity_id: str | None = None


TESTBENCH_SERVICES = {
    "c-echo": "C-ECHO (Verification)",
    "c-store": "C-STORE (Secondary Capture test image)",
    "c-find": "C-FIND (Study Root Query/Retrieve)",
    "mwl-find": "MWL C-FIND (Modality Worklist)",
}


class WorklistApiQuery(WorklistQuery):
    source: str = "local"
    identity_id: str = ""


def create_app(store: ConfigStore | None = None) -> FastAPI:
    store = store or ConfigStore()
    scp = WorklistSCP(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scp.start()
        yield
        scp.stop()

    app = FastAPI(
        title="Arnout.pro Dicommunication Tool",
        description="Low-code DICOM communication validator and PACS admin toolkit.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.mwl_scp = scp
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def page(request: Request, **extra: object) -> dict:
        config = app.state.store.load()
        scp = getattr(app.state, "mwl_scp", None)
        return {
            "request": request,
            "config": config,
            "tools": list_tools(),
            "tool_groups": list_tools_by_category(),
            "results": app.state.store.list_results(10),
            "mwl_scp_running": bool(scp and scp.running),
            "mwl_scp_error": getattr(scp, "last_error", None) if scp else None,
            **extra,
        }

    def execute_tool(
        tool_id: str,
        remote_id: str,
        options: dict[str, Any] | None = None,
        identity_id: str | None = None,
    ) -> ToolResult:
        config = app.state.store.load()
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        remote = config.get_remote(remote_id)
        if tool.requires_remote and remote is None:
            raise HTTPException(status_code=400, detail="A remote DICOM node is required")
        try:
            local = config.calling_ae(identity_id or None)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="Virtual local AE not found") from exc
        result = tool.run(local, remote, options)
        result.calling_ae = local.ae_title
        app.state.store.add_result(result)
        return result

    def config_view(
        request: Request,
        *,
        page_id: str = "overview",
        editing: RemoteNode | None = None,
        editing_identity: VirtualAE | None = None,
        saved: str | None = None,
        error: str | None = None,
        status_code: int = 200,
    ):
        templates_by_page = {
            "overview": ("config.html", "config"),
            "local": ("config_local.html", "config-local"),
            "identities": ("config_identities.html", "config-identities"),
            "remotes": ("config_remotes.html", "config-remotes"),
        }
        template_name, nav = templates_by_page[page_id]
        return templates.TemplateResponse(
            request,
            template_name,
            page(
                request,
                nav=nav,
                editing=editing,
                editing_identity=editing_identity,
                saved=saved,
                error=error,
            ),
            status_code=status_code,
        )

    def _testbench_options(
        service: str,
        patient_name: str,
        patient_id: str,
        accession_number: str,
        study_date: str,
        modality: str,
        station_ae_title: str,
        scheduled_date: str,
    ) -> dict[str, Any]:
        if service == "c-find":
            return {
                "patient_name": patient_name,
                "patient_id": patient_id,
                "accession_number": accession_number,
                "study_date": study_date,
                "modality": modality,
            }
        if service == "mwl-find":
            return {
                "patient_name": patient_name,
                "patient_id": patient_id,
                "accession_number": accession_number,
                "modality": modality,
                "station_ae_title": station_ae_title,
                "scheduled_date": scheduled_date,
            }
        return {}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            page(request, nav="home", echo_board=echo_board_snapshot(app.state.store)),
        )

    @app.get("/echo-board", response_class=HTMLResponse)
    def echo_board_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "echo_board.html",
            page(
                request,
                nav="echo-board",
                echo_board=echo_board_snapshot(app.state.store),
            ),
        )

    @app.post("/echo-board/run")
    def echo_board_run(request: Request):
        board = run_echo_board(app.state.store)
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/echo_board.html",
                {"request": request, "echo_board": board, "config": app.state.store.load()},
            )
        return templates.TemplateResponse(
            request,
            "echo_board.html",
            page(request, nav="echo-board", echo_board=board),
        )

    @app.get("/config", response_class=HTMLResponse)
    def config_page(
        request: Request,
        edit: str | None = None,
        identity: str | None = None,
        saved: str | None = None,
    ):
        if edit:
            return RedirectResponse(f"/config/remotes?edit={edit}", status_code=303)
        if identity:
            return RedirectResponse(f"/config/identities?edit={identity}", status_code=303)
        return config_view(request, page_id="overview", saved=saved)

    @app.get("/config/local", response_class=HTMLResponse)
    def config_local_page(request: Request, saved: str | None = None) -> HTMLResponse:
        return config_view(request, page_id="local", saved=saved)

    @app.get("/config/identities", response_class=HTMLResponse)
    def config_identities_page(
        request: Request,
        edit: str | None = None,
        saved: str | None = None,
    ) -> HTMLResponse:
        config = app.state.store.load()
        return config_view(
            request,
            page_id="identities",
            editing_identity=config.get_identity(edit),
            saved=saved,
        )

    @app.get("/config/remotes", response_class=HTMLResponse)
    def config_remotes_page(
        request: Request,
        edit: str | None = None,
        saved: str | None = None,
    ) -> HTMLResponse:
        config = app.state.store.load()
        return config_view(
            request,
            page_id="remotes",
            editing=config.get_remote(edit) if edit else None,
            saved=saved,
        )

    @app.post("/config/local")
    def save_local(
        request: Request,
        ae_title: str = Form(...),
        host: str = Form(...),
        hostname: str = Form(""),
        port: int = Form(...),
        timeout_seconds: float = Form(10),
        max_pdu: int = Form(16382),
        implementation_version: str = Form(""),
        station_ae_title: str = Form(""),
        mwl_scp_enabled: str | None = Form(None),
    ):
        try:
            local = LocalAE(
                ae_title=ae_title,
                host=host,
                hostname=hostname,
                port=port,
                timeout_seconds=timeout_seconds,
                max_pdu=max_pdu,
                implementation_version=implementation_version,
                station_ae_title=station_ae_title,
                mwl_scp_enabled=_as_bool(mwl_scp_enabled),
            )
        except ValidationError as exc:
            return config_view(request, page_id="local", error=_first_error(exc), status_code=400)
        app.state.store.save_local(local)
        app.state.mwl_scp.restart()
        return RedirectResponse("/config/local?saved=local", status_code=303)

    @app.post("/config/remotes")
    def add_or_update_remote(
        request: Request,
        name: str = Form(...),
        ae_title: str = Form(...),
        host: str = Form(""),
        hostname: str = Form(""),
        port: int = Form(...),
        notes: str = Form(""),
        remote_id: str = Form(""),
        kind: str = Form("other"),
        provides_mwl: str | None = Form(None),
    ):
        try:
            remote = RemoteNode(
                name=name,
                ae_title=ae_title,
                host=host,
                hostname=hostname,
                port=port,
                notes=notes,
                kind=kind,  # type: ignore[arg-type]
                provides_mwl=_as_bool(provides_mwl),
            )
        except ValidationError as exc:
            config = app.state.store.load()
            editing = config.get_remote(remote_id) if remote_id else None
            return config_view(
                request,
                page_id="remotes",
                editing=editing,
                error=_first_error(exc),
                status_code=400,
            )
        if remote_id:
            try:
                app.state.store.update_remote(remote_id, remote)
            except KeyError:
                raise HTTPException(status_code=404, detail="Remote node not found") from None
            return RedirectResponse("/config/remotes?saved=remote", status_code=303)
        app.state.store.add_remote(remote)
        return RedirectResponse("/config/remotes?saved=remote", status_code=303)

    @app.post("/config/remotes/{remote_id}/delete")
    def delete_remote(remote_id: str):
        app.state.store.delete_remote(remote_id)
        return RedirectResponse("/config/remotes?saved=deleted", status_code=303)

    @app.post("/config/identities")
    def add_or_update_identity(
        request: Request,
        name: str = Form(...),
        ae_title: str = Form(...),
        station_ae_title: str = Form(""),
        modality: str = Form(""),
        notes: str = Form(""),
        identity_id: str = Form(""),
    ):
        try:
            identity = VirtualAE(
                name=name,
                ae_title=ae_title,
                station_ae_title=station_ae_title,
                modality=modality,
                notes=notes,
            )
        except ValidationError as exc:
            config = app.state.store.load()
            editing_identity = config.get_identity(identity_id) if identity_id else None
            return config_view(
                request,
                page_id="identities",
                editing_identity=editing_identity,
                error=_first_error(exc),
                status_code=400,
            )
        if identity_id:
            try:
                app.state.store.update_identity(identity_id, identity)
            except KeyError:
                raise HTTPException(status_code=404, detail="Virtual local AE not found") from None
            return RedirectResponse("/config/identities?saved=identity", status_code=303)
        app.state.store.add_identity(identity)
        return RedirectResponse("/config/identities?saved=identity", status_code=303)

    @app.post("/config/identities/{identity_id}/delete")
    def delete_identity(identity_id: str):
        app.state.store.delete_identity(identity_id)
        return RedirectResponse("/config/identities?saved=deleted", status_code=303)

    @app.get("/testbench", response_class=HTMLResponse)
    def testbench_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "testbench.html",
            page(request, nav="testbench", result=None, service="c-echo"),
        )

    @app.post("/testbench/run")
    def testbench_run(
        request: Request,
        remote_id: str = Form(...),
        service: str = Form(...),
        patient_name: str = Form(""),
        patient_id: str = Form(""),
        accession_number: str = Form(""),
        study_date: str = Form(""),
        modality: str = Form(""),
        station_ae_title: str = Form(""),
        scheduled_date: str = Form(""),
        identity_id: str = Form(""),
    ):
        if service not in TESTBENCH_SERVICES:
            raise HTTPException(status_code=400, detail="Unknown testbench service")
        options = _testbench_options(
            service,
            patient_name,
            patient_id,
            accession_number,
            study_date,
            modality,
            station_ae_title,
            scheduled_date,
        )
        try:
            result = execute_tool(service, remote_id, options, identity_id or None)
        except HTTPException as exc:
            failure = ToolResult(
                tool_id=service,
                tool_name=TESTBENCH_SERVICES[service],
                ok=False,
                summary=str(exc.detail),
            )
            if _hx(request):
                return templates.TemplateResponse(
                    request,
                    "partials/result.html",
                    {"request": request, "result": failure},
                    status_code=exc.status_code,
                )
            raise
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": result},
            )
        return templates.TemplateResponse(
            request,
            "testbench.html",
            page(request, nav="testbench", result=result, service=service),
        )

    @app.get("/worklist", response_class=HTMLResponse)
    def worklist_page(request: Request, saved: str | None = None) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "worklist.html",
            page(
                request,
                nav="worklist",
                query=WorklistQuery(),
                result=None,
                source="local",
                identity_id="",
                local_entries=app.state.store.list_worklist(),
                saved=saved,
            ),
        )

    @app.post("/worklist/query")
    def worklist_query(
        request: Request,
        source: str = Form("local"),
        patient_name: str = Form(""),
        patient_id: str = Form(""),
        accession_number: str = Form(""),
        modality: str = Form(""),
        station_ae_title: str = Form(""),
        scheduled_date: str = Form(""),
        identity_id: str = Form(""),
    ):
        query = WorklistQuery(
            patient_name=patient_name,
            patient_id=patient_id,
            accession_number=accession_number,
            modality=modality,
            station_ae_title=station_ae_title,
            scheduled_date=scheduled_date,
        )
        result = query_worklist(app.state.store, source, query, identity_id or None)
        context = page(
            request,
            nav="worklist",
            query=query,
            result=result,
            source=source,
            identity_id=identity_id,
            local_entries=app.state.store.list_worklist(),
        )
        if _hx(request):
            return templates.TemplateResponse(request, "partials/worklist_results.html", context)
        return templates.TemplateResponse(request, "worklist.html", context)

    @app.post("/worklist/entries")
    def add_worklist_entry(
        request: Request,
        patient_name: str = Form(...),
        patient_id: str = Form(...),
        patient_birth_date: str = Form(""),
        patient_sex: str = Form(""),
        accession_number: str = Form(""),
        requested_procedure_id: str = Form(""),
        requested_procedure_description: str = Form(""),
        modality: str = Form("CT"),
        station_ae_title: str = Form(""),
        station_name: str = Form(""),
        scheduled_date: str = Form(""),
        scheduled_time: str = Form(""),
        scheduled_physician: str = Form(""),
    ):
        config = app.state.store.load()
        try:
            entry = WorklistEntry(
                patient_name=patient_name,
                patient_id=patient_id,
                patient_birth_date=patient_birth_date,
                patient_sex=patient_sex,
                accession_number=accession_number,
                requested_procedure_id=requested_procedure_id,
                requested_procedure_description=requested_procedure_description,
                modality=modality,
                station_ae_title=station_ae_title or config.local.station_ae_title,
                station_name=station_name,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                scheduled_physician=scheduled_physician,
                study_instance_uid=str(generate_uid()),
            )
        except ValidationError as exc:
            return templates.TemplateResponse(
                request,
                "worklist.html",
                page(
                    request,
                    nav="worklist",
                    query=WorklistQuery(),
                    result=None,
                    source="local",
                    identity_id="",
                    local_entries=app.state.store.list_worklist(),
                    error=_first_error(exc),
                ),
                status_code=400,
            )
        app.state.store.add_worklist_entry(entry)
        return RedirectResponse("/worklist?saved=entry", status_code=303)

    @app.post("/worklist/entries/{entry_id}/delete")
    def delete_worklist_entry(entry_id: str):
        app.state.store.delete_worklist_entry(entry_id)
        return RedirectResponse("/worklist?saved=deleted", status_code=303)

    @app.get("/tools/{tool_id}", response_class=HTMLResponse)
    def tool_page(request: Request, tool_id: str) -> HTMLResponse:
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "tool.html",
            page(request, nav="tools", tool=tool, tool_id=tool.id, result=None),
        )

    @app.post("/tools/{tool_id}/run")
    def run_tool_form(
        request: Request,
        tool_id: str,
        remote_id: str = Form(...),
        identity_id: str = Form(""),
    ):
        try:
            result = execute_tool(tool_id, remote_id, identity_id=identity_id or None)
        except HTTPException as exc:
            failure = ToolResult(
                tool_id=tool_id,
                tool_name=tool_id,
                ok=False,
                summary=str(exc.detail),
            )
            if _hx(request):
                return templates.TemplateResponse(
                    request,
                    "partials/result.html",
                    {"request": request, "result": failure},
                    status_code=exc.status_code,
                )
            raise
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": result},
            )
        try:
            tool = get_tool(tool_id)
        except KeyError:
            tool = None
        return templates.TemplateResponse(
            request,
            "tool.html",
            page(request, nav="tools", tool=tool, tool_id=tool_id, result=result),
        )

    @app.get("/api/echo-board")
    def api_echo_board():
        return echo_board_snapshot(app.state.store)

    @app.post("/api/echo-board/run")
    def api_echo_board_run():
        return run_echo_board(app.state.store)

    @app.post("/api/worklist/query")
    def api_worklist_query(body: WorklistApiQuery):
        return query_worklist(app.state.store, body.source, body, body.identity_id or None)

    @app.get("/api/worklist")
    def api_worklist_local():
        return app.state.store.list_worklist()

    @app.post("/api/worklist", status_code=201)
    def api_add_worklist_entry(entry: WorklistEntry):
        if not entry.study_instance_uid:
            entry = entry.model_copy(update={"study_instance_uid": str(generate_uid())})
        return app.state.store.add_worklist_entry(entry)

    @app.delete("/api/worklist/{entry_id}")
    def api_delete_worklist_entry(entry_id: str):
        app.state.store.delete_worklist_entry(entry_id)
        return JSONResponse({"ok": True, "id": entry_id})

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

    @app.get("/api/identities")
    def api_identities():
        return app.state.store.load().identities

    @app.post("/api/identities", status_code=201)
    def api_add_identity(identity: VirtualAE):
        app.state.store.add_identity(identity)
        return identity

    @app.put("/api/identities/{identity_id}")
    def api_update_identity(identity_id: str, identity: VirtualAE):
        try:
            return app.state.store.update_identity(identity_id, identity)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Virtual local AE not found") from exc

    @app.delete("/api/identities/{identity_id}")
    def api_delete_identity(identity_id: str):
        app.state.store.delete_identity(identity_id)
        return JSONResponse({"ok": True, "id": identity_id})

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
        return execute_tool(tool_id, body.remote_id, body.options, body.identity_id)

    return app


app = create_app()
