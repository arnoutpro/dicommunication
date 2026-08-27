"""FastAPI application: config screens, tool runner, and a small JSON API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from pydicom.uid import generate_uid

from app import __version__
from app.applog import (
    configure as configure_logging,
    clear_log,
    format_size,
    log,
    log_path,
    read_tail,
    should_skip_http_log,
    viewer_lines,
)
from app.hl7 import DEFAULT_PORT, display_hl7, sample_adt_a01
from app.echo_board import run_all as run_echo_board
from app.echo_board import snapshot as echo_board_snapshot
from app.models import (
    Hl7Message,
    LocalAE,
    LoggingSettings,
    RemoteNode,
    ToolResult,
    WorklistEntry,
    WorklistQuery,
    VirtualAE,
    new_record_id,
    utc_now,
)
from app.mwl import query_worklist
from app.mwl_scp import WorklistSCP
from app.fs_dialog import dialogs_available, pick_directory
from app.pdf_dicom import (
    CollectError,
    MAX_FILES,
    MAX_PDF_BYTES,
    MAX_ZIP_BYTES,
    is_pdf_filename,
    list_directory_pdfs,
)
from app.paths import package_dir
from app.shell import (
    PRODUCT_NAMES,
    SHELL_DICOMM,
    SHELL_VUE,
    is_vue_public_path,
    prefix_redirect_location,
    public_href,
    strip_vue_prefix,
    tool_groups_for_shell,
    tools_for_shell,
    vue_path_allowed,
)
from app.store import ConfigStore
from app.tools import get_tool, list_tools
from app.tools.find_keys import catalog_payload, column_labels, options_from_form

BASE_DIR = package_dir()

LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def http_publish_note(environ: Mapping[str, str] | None = None) -> str | None:
    """Say which host address the UI was published on, for `docker compose logs`.

    A process inside a container cannot see its own host-side port mapping, so
    if Compose publishes the UI on loopback the app has no way to notice that a
    browser on another machine is being refused. Compose passes the address it
    used in DICOMM_HTTP_BIND purely so this line can be logged. Returns None
    when that is unset, which is every non-Compose run.
    """
    environ = os.environ if environ is None else environ
    bind = (environ.get("DICOMM_HTTP_BIND") or "").strip()
    if not bind:
        return None
    port = (environ.get("PORT") or "8080").strip() or "8080"
    if bind in LOOPBACK_BINDS:
        return (
            f"Web UI published on {bind}:{port} — reachable from the Docker host only. "
            "The UI has no login, so this is the default. To reach it from another "
            "machine, restart with DICOMM_HTTP_BIND=0.0.0.0 and put an authenticating "
            "reverse proxy in front of it."
        )
    return (
        f"Web UI published on {bind}:{port} — reachable from the network. "
        "The UI has no login; serve it through an authenticating reverse proxy."
    )


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    loc = error.get("loc") or ("config",)
    return f"{loc[-1]}: {error.get('msg', 'invalid value')}"


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _as_bool(value: str | None) -> bool:
    return (value or "").lower() in {"on", "true", "1", "yes"}


def _upload_filename(upload: UploadFile | None) -> str:
    raw = (getattr(upload, "filename", None) or "").replace("\\", "/").strip()
    return raw.rsplit("/", 1)[-1] if raw else ""


def _read_upload(upload: UploadFile | None, limit: int) -> bytes | None:
    if upload is None or not _upload_filename(upload):
        return None
    return upload.file.read(limit + 1)


def _pdf_store_options(
    *,
    patient_name: str,
    patient_id: str,
    accession_number: str,
    study_description: str,
    document_title: str,
    directory: str,
    same_study: bool,
    send: bool,
    pdfs: list[UploadFile] | None,
    zip_file: UploadFile | None,
    folder: list[UploadFile] | None,
    generate_name: bool = False,
    generate_id: bool = False,
    unique_patient: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    accepted = 0
    for upload in list(pdfs or []) + list(folder or []):
        name = _upload_filename(upload) or "document.pdf"
        if not is_pdf_filename(name):
            items.append({"filename": name, "skip": "not-pdf", "origin": "upload"})
            continue
        if accepted >= MAX_FILES:
            # collect_pdfs enforces the same cap, but only after every upload has
            # been read. Stop pulling bytes here so a request with hundreds of
            # large PDFs cannot pin MAX_PDF_BYTES x N in memory before it fails.
            items.append({"filename": name, "skip": "too-many", "origin": "upload"})
            continue
        data = _read_upload(upload, MAX_PDF_BYTES)
        if data is None:
            continue
        accepted += 1
        items.append(
            {
                "filename": name,
                "content": data,
                "origin": "upload",
            }
        )
    options: dict[str, Any] = {
        "patient_name": patient_name,
        "patient_id": patient_id,
        "accession_number": accession_number,
        "study_description": study_description,
        "document_title": document_title,
        "directory": directory,
        "same_study": same_study,
        "send": send,
        "generate_name": generate_name,
        "generate_id": generate_id,
        "unique_patient": unique_patient,
        "pdfs": items,
    }
    zip_bytes = _read_upload(zip_file, MAX_ZIP_BYTES)
    if zip_bytes is not None:
        options["zip_bytes"] = zip_bytes
    return options


class RunRequest(BaseModel):
    remote_id: str | None = None
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
    configure_logging(store.data_dir, store.load().logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = store.load().logging
        configure_logging(store.data_dir, settings)
        log.info(
            "Dicommunication %s started (data dir %s, log level %s)",
            __version__,
            store.data_dir,
            settings.level,
        )
        publish_note = http_publish_note()
        if publish_note:
            log.info("%s", publish_note)
        scp.start()
        if scp.running:
            log.info("MWL SCP is listening")
        elif scp.last_error:
            log.warning("%s", scp.last_error)
        yield
        log.info("Dicommunication stopping")
        scp.stop()

    app = FastAPI(
        title="Arnout.pro Dicommunication Tool",
        description="Low-code DICOM communication validator and PACS admin toolkit.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.mwl_scp = scp
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def vue_shell_middleware(request: Request, call_next):
        original = request.scope.get("path") or "/"
        vue = is_vue_public_path(original)
        if vue:
            request.state.shell = SHELL_VUE
            new_path = strip_vue_prefix(original)
            request.scope["path"] = new_path
            request.scope["raw_path"] = new_path.encode("utf-8")
            if not vue_path_allowed(new_path):
                response = RedirectResponse("/", status_code=303)
            else:
                response = await call_next(request)
            location = response.headers.get("location")
            if location:
                response.headers["location"] = prefix_redirect_location(location)
            return response
        request.state.shell = SHELL_DICOMM
        return await call_next(request)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        path = request.url.path
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("%s %s failed", request.method, path)
            raise
        if not should_skip_http_log(path):
            duration_ms = round((perf_counter() - started) * 1000, 1)
            message = "%s %s -> %s (%.1f ms)" % (
                request.method,
                path,
                response.status_code,
                duration_ms,
            )
            if response.status_code >= 500:
                log.error(message)
            elif response.status_code >= 400:
                log.warning(message)
            else:
                log.debug(message)
        return response

    def page(request: Request, **extra: object) -> dict:
        config = app.state.store.load()
        scp = getattr(app.state, "mwl_scp", None)
        shell = getattr(request.state, "shell", SHELL_DICOMM)
        return {
            "request": request,
            "config": config,
            "shell": shell,
            "product_name": PRODUCT_NAMES[shell],
            "href": lambda path: public_href(path, shell=shell),
            "tools": tools_for_shell(shell),
            "tool_groups": tool_groups_for_shell(shell),
            "results": app.state.store.list_results(10),
            "mwl_scp_running": bool(scp and scp.running),
            "mwl_scp_error": getattr(scp, "last_error", None) if scp else None,
            "app_version": __version__,
            **extra,
        }

    def execute_tool(
        tool_id: str,
        remote_id: str | None = None,
        options: dict[str, Any] | None = None,
        identity_id: str | None = None,
    ) -> ToolResult:
        config = app.state.store.load()
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        remote = config.get_remote(remote_id) if remote_id else None
        if tool.requires_remote and remote is None:
            raise HTTPException(status_code=400, detail="A remote DICOM node is required")
        try:
            local = config.calling_ae(identity_id or None)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="Virtual local AE not found") from exc
        result = tool.run(local, remote, options)
        if tool.id != "hl7-send":
            result.calling_ae = local.ae_title
        app.state.store.add_result(result)
        target = remote.name if remote else (result.remote_name or "local")
        message = "Run %s as %s → %s: %s" % (tool_id, local.ae_title, target, result.summary)
        if result.ok:
            log.info(message)
        else:
            log.warning(message)
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

    def _log_view_payload() -> dict[str, object]:
        path = log_path(app.state.store.data_dir)
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
            page(request, nav="logs", saved=saved, error=error, **_log_view_payload()),
            status_code=status_code,
        )

    def _apply_logging(settings: LoggingSettings) -> LoggingSettings:
        app.state.store.save_logging(settings)
        configure_logging(app.state.store.data_dir, settings)
        log.info(
            "Logging set to %s, rotate at %s MB, keep %s files",
            settings.level,
            settings.max_megabytes,
            settings.backup_count,
        )
        return settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request, saved: str | None = None) -> HTMLResponse:
        return _logs_page(request, saved=saved)

    @app.get("/logs/live", response_class=HTMLResponse)
    def logs_live(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "partials/log_view.html",
            {**page(request, nav="logs"), **_log_view_payload()},
        )

    @app.get("/logs/download")
    def logs_download():
        path = log_path(app.state.store.data_dir)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")
        return FileResponse(path, filename="dicommunication.log", media_type="text/plain")

    @app.post("/logs")
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
        _apply_logging(settings)
        return RedirectResponse("/logs?saved=logging", status_code=303)

    @app.post("/logs/clear")
    def logs_clear():
        settings = app.state.store.load().logging
        clear_log(app.state.store.data_dir, settings)
        return RedirectResponse("/logs?saved=cleared", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        if getattr(request.state, "shell", SHELL_DICOMM) == SHELL_VUE:
            return _c_find_advanced_page(request, nav="home")
        return templates.TemplateResponse(
            request,
            "index.html",
            page(request, nav="home", echo_board=echo_board_snapshot(app.state.store)),
        )

    @app.get("/about", response_class=HTMLResponse)
    def about_page(request: Request) -> HTMLResponse:
        vue = getattr(request.state, "shell", SHELL_DICOMM) == SHELL_VUE
        return templates.TemplateResponse(
            request,
            "about_vue.html" if vue else "about.html",
            page(request, nav="about", data_dir=str(app.state.store.data_dir)),
        )

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request) -> HTMLResponse:
        vue = getattr(request.state, "shell", SHELL_DICOMM) == SHELL_VUE
        return templates.TemplateResponse(
            request,
            "help_vue.html" if vue else "help.html",
            page(request, nav="help", data_dir=str(app.state.store.data_dir)),
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
        log.info(
            "Saved local AE %s on %s:%s (MWL SCP %s)",
            local.ae_title,
            local.host,
            local.port,
            "on" if local.mwl_scp_enabled else "off",
        )
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
            log.info("Updated remote %s (%s %s)", remote.name, remote.ae_title, remote.endpoint)
            return RedirectResponse("/config/remotes?saved=remote", status_code=303)
        app.state.store.add_remote(remote)
        log.info("Added remote %s (%s %s)", remote.name, remote.ae_title, remote.endpoint)
        return RedirectResponse("/config/remotes?saved=remote", status_code=303)

    @app.post("/config/remotes/{remote_id}/delete")
    def delete_remote(remote_id: str):
        app.state.store.delete_remote(remote_id)
        log.info("Deleted remote %s", remote_id)
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
            log.info("Updated virtual AE %s (%s)", identity.name, identity.ae_title)
            return RedirectResponse("/config/identities?saved=identity", status_code=303)
        app.state.store.add_identity(identity)
        log.info("Added virtual AE %s (%s)", identity.name, identity.ae_title)
        return RedirectResponse("/config/identities?saved=identity", status_code=303)

    @app.post("/config/identities/{identity_id}/delete")
    def delete_identity(identity_id: str):
        app.state.store.delete_identity(identity_id)
        log.info("Deleted virtual AE %s", identity_id)
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

    def _hl7_page(
        request: Request,
        *,
        result: ToolResult | None = None,
        error: str | None = None,
        saved: str | None = None,
        host: str = "",
        port: str = "",
        message: str = "",
        mllp: bool = True,
        save_name: str = "",
        remote_id: str = "",
        new_control_id: bool = True,
        change_order: bool = True,
        obr_reason_ce: bool = True,
        obr_in_progress: bool = True,
        orc_control: str = "SC",
        status_code: int = 200,
    ):
        tool = get_tool("hl7-send")
        if not message:
            message = display_hl7(
                sample_adt_a01(sending_app=app.state.store.load().local.ae_title)
            )
        return templates.TemplateResponse(
            request,
            tool.template,
            page(
                request,
                nav="tools",
                tool=tool,
                tool_id=tool.id,
                result=result,
                error=error,
                saved=saved,
                messages=app.state.store.list_hl7_messages(),
                hl7_host=host,
                hl7_port=str(port or DEFAULT_PORT),
                hl7_message=message,
                hl7_mllp=mllp,
                hl7_save_name=save_name,
                hl7_new_control_id=new_control_id,
                hl7_change_order=change_order,
                hl7_obr_reason_ce=obr_reason_ce,
                hl7_obr_in_progress=obr_in_progress,
                hl7_orc_control=orc_control,
                remote_id=remote_id,
            ),
            status_code=status_code,
        )

    @app.post("/tools/hl7-send/run")
    def hl7_send_run(
        request: Request,
        host: str = Form(""),
        port: str = Form(str(DEFAULT_PORT)),
        message: str = Form(""),
        mllp: str = Form("mllp"),
        remote_id: str = Form(""),
        name: str = Form(""),
        new_control_id: str | None = Form(None),
        change_order: str | None = Form(None),
        obr_reason_ce: str | None = Form(None),
        obr_in_progress: str | None = Form(None),
        orc_control: str = Form("SC"),
    ):
        options = {
            "host": host,
            "port": port,
            "message": message,
            "mllp": mllp,
            "new_control_id": _as_bool(new_control_id),
            "change_order": _as_bool(change_order),
            "obr_reason_ce": _as_bool(obr_reason_ce),
            "obr_in_progress": _as_bool(obr_in_progress),
            "orc_control": orc_control,
        }
        try:
            result = execute_tool("hl7-send", remote_id or None, options)
        except HTTPException as exc:
            failure = ToolResult(
                tool_id="hl7-send",
                tool_name="HL7 send",
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
            return _hl7_page(
                request,
                result=failure,
                host=host,
                port=port,
                message=display_hl7(message),
                mllp=mllp != "raw",
                save_name=name,
                remote_id=remote_id,
                new_control_id=_as_bool(new_control_id),
                change_order=_as_bool(change_order),
                obr_reason_ce=_as_bool(obr_reason_ce),
                obr_in_progress=_as_bool(obr_in_progress),
                orc_control=orc_control,
                status_code=exc.status_code,
            )
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": result},
            )
        return _hl7_page(
            request,
            result=result,
            host=host,
            port=port,
            message=display_hl7(message),
            mllp=mllp != "raw",
            save_name=name,
            remote_id=remote_id,
            new_control_id=_as_bool(new_control_id),
            change_order=_as_bool(change_order),
            obr_reason_ce=_as_bool(obr_reason_ce),
            obr_in_progress=_as_bool(obr_in_progress),
            orc_control=orc_control,
        )

    @app.post("/tools/hl7-send/messages")
    def save_hl7_message(
        request: Request,
        name: str = Form(""),
        message: str = Form(""),
        host: str = Form(""),
        port: str = Form(""),
        mllp: str = Form("mllp"),
        remote_id: str = Form(""),
    ):
        try:
            entry = Hl7Message(name=name, body=message)
        except ValidationError as exc:
            return _hl7_page(
                request,
                error=_first_error(exc),
                host=host,
                port=port,
                message=display_hl7(message) if message.strip() else "",
                mllp=mllp != "raw",
                save_name=name,
                remote_id=remote_id,
                status_code=400,
            )
        saved = app.state.store.add_hl7_message(entry)
        log.info("Saved HL7 draft %s", saved.name)
        return RedirectResponse(f"/tools/hl7-send?load={saved.id}&saved=1", status_code=303)

    @app.post("/tools/hl7-send/messages/{message_id}/delete")
    def delete_hl7_message(message_id: str):
        app.state.store.delete_hl7_message(message_id)
        log.info("Deleted HL7 draft %s", message_id)
        return RedirectResponse("/tools/hl7-send?saved=deleted", status_code=303)

    def _find_advanced_extras(result: ToolResult | None, level: str = "STUDY") -> dict[str, Any]:
        records = result.records if result else []
        columns = list(records[0].keys()) if records else []
        resolved = level
        if result:
            for step in result.steps:
                step_level = step.details.get("level")
                if step_level:
                    resolved = str(step_level)
                    break
        return {
            "find_catalog": catalog_payload(),
            "find_level": resolved,
            "find_labels": dict(zip(columns, column_labels(columns))),
        }

    def _c_find_advanced_page(
        request: Request,
        *,
        result: ToolResult | None = None,
        remote_id: str = "",
        identity_id: str = "",
        level: str = "STUDY",
        status_code: int = 200,
        nav: str = "home",
    ):
        tool = get_tool("c-find-advanced")
        return templates.TemplateResponse(
            request,
            tool.template,
            page(
                request,
                nav=nav,
                tool=tool,
                tool_id=tool.id,
                result=result,
                remote_id=remote_id,
                identity_id=identity_id,
                **_find_advanced_extras(result, level),
            ),
            status_code=status_code,
        )

    def _pdf_store_page(
        request: Request,
        *,
        result: ToolResult | None = None,
        remote_id: str = "",
        identity_id: str = "",
        patient_name: str = "",
        patient_id: str = "",
        accession_number: str = "",
        study_description: str = "",
        document_title: str = "",
        directory: str = "",
        same_study: bool = True,
        send: bool | None = None,
        generate_name: bool = False,
        generate_id: bool = False,
        unique_patient: bool = False,
        scan: dict | None = None,
        status_code: int = 200,
    ):
        tool = get_tool("pdf-store")
        config = app.state.store.load()
        if send is None:
            send = bool(config.remotes)
        return templates.TemplateResponse(
            request,
            tool.template,
            page(
                request,
                nav="tools",
                tool=tool,
                tool_id=tool.id,
                result=result,
                remote_id=remote_id,
                identity_id=identity_id,
                patient_name=patient_name,
                patient_id=patient_id,
                accession_number=accession_number,
                study_description=study_description,
                document_title=document_title,
                directory=directory,
                same_study=same_study,
                send=send,
                generate_name=generate_name,
                generate_id=generate_id,
                unique_patient=unique_patient,
                scan=scan,
            ),
            status_code=status_code,
        )

    @app.post("/tools/pdf-store/run")
    def pdf_store_run(
        request: Request,
        remote_id: str = Form(""),
        identity_id: str = Form(""),
        patient_name: str = Form(""),
        patient_id: str = Form(""),
        accession_number: str = Form(""),
        study_description: str = Form(""),
        document_title: str = Form(""),
        directory: str = Form(""),
        same_study: str | None = Form(None),
        send: str | None = Form(None),
        generate_name: str | None = Form(None),
        generate_id: str | None = Form(None),
        unique_patient: str | None = Form(None),
        pdfs: list[UploadFile] = File(default=[]),
        zip_file: UploadFile | None = File(default=None),
        folder: list[UploadFile] = File(default=[]),
    ):
        options = _pdf_store_options(
            patient_name=patient_name,
            patient_id=patient_id,
            accession_number=accession_number,
            study_description=study_description,
            document_title=document_title,
            directory=directory,
            same_study=_as_bool(same_study),
            send=_as_bool(send),
            generate_name=_as_bool(generate_name),
            generate_id=_as_bool(generate_id),
            unique_patient=_as_bool(unique_patient),
            pdfs=pdfs,
            zip_file=zip_file,
            folder=folder,
        )
        try:
            result = execute_tool("pdf-store", remote_id or None, options, identity_id or None)
        except HTTPException as exc:
            failure = ToolResult(
                tool_id="pdf-store",
                tool_name="PDF to DICOM",
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
            return _pdf_store_page(
                request,
                result=failure,
                remote_id=remote_id,
                identity_id=identity_id,
                patient_name=patient_name,
                patient_id=patient_id,
                accession_number=accession_number,
                study_description=study_description,
                document_title=document_title,
                directory=directory,
                same_study=_as_bool(same_study),
                send=_as_bool(send),
                generate_name=_as_bool(generate_name),
                generate_id=_as_bool(generate_id),
                unique_patient=_as_bool(unique_patient),
                status_code=exc.status_code,
            )
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": result},
            )
        return _pdf_store_page(
            request,
            result=result,
            remote_id=remote_id,
            identity_id=identity_id,
            patient_name=patient_name,
            patient_id=patient_id,
            accession_number=accession_number,
            study_description=study_description,
            document_title=document_title,
            directory=directory,
            same_study=_as_bool(same_study),
            send=_as_bool(send),
            generate_name=_as_bool(generate_name),
            generate_id=_as_bool(generate_id),
            unique_patient=_as_bool(unique_patient),
        )

    @app.post("/tools/pdf-store/scan")
    def pdf_store_scan(request: Request, directory: str = Form("")):
        try:
            scan = list_directory_pdfs(directory)
        except CollectError as exc:
            scan = {"ok": False, "error": str(exc), "files": [], "pdf_count": 0}
        if _hx(request) or request.headers.get("HX-Request") == "true":
            return templates.TemplateResponse(
                request,
                "partials/pdf_scan.html",
                {"request": request, "scan": scan},
            )
        return scan

    @app.get("/api/tools/pdf-store/scan")
    def api_pdf_store_scan(directory: str = ""):
        try:
            return list_directory_pdfs(directory)
        except CollectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/fs/pick-directory")
    def api_pick_directory():
        if not dialogs_available():
            raise HTTPException(
                status_code=503,
                detail="No desktop folder dialog in this session. Type the path, or use Folder of PDFs.",
            )
        path = pick_directory()
        if not path:
            raise HTTPException(status_code=400, detail="No folder selected.")
        return {"path": path}

    @app.get("/tools/{tool_id}", response_class=HTMLResponse)
    def tool_page(
        request: Request,
        tool_id: str,
        load: str | None = None,
        saved: str | None = None,
    ) -> HTMLResponse:
        try:
            tool = get_tool(tool_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if tool_id == "hl7-send":
            host = request.query_params.get("host", "")
            port = request.query_params.get("port", str(DEFAULT_PORT))
            message = ""
            save_name = ""
            if load:
                stored = app.state.store.get_hl7_message(load)
                if stored:
                    message = display_hl7(stored.body)
                    save_name = stored.name
            return _hl7_page(
                request,
                saved=saved,
                host=host,
                port=port,
                message=message,
                mllp=request.query_params.get("mllp", "mllp") != "raw",
                save_name=save_name,
                remote_id=request.query_params.get("remote_id", ""),
            )
        if tool_id == "pdf-store":
            return _pdf_store_page(request)
        if tool_id == "c-find-advanced":
            if getattr(request.state, "shell", SHELL_DICOMM) != SHELL_VUE:
                return RedirectResponse("/vue/", status_code=303)
            return _c_find_advanced_page(request)
        return templates.TemplateResponse(
            request,
            tool.template,
            page(request, nav="tools", tool=tool, tool_id=tool.id, result=None),
        )

    @app.post("/tools/c-find-advanced/run")
    async def c_find_advanced_run(request: Request):
        form = await request.form()
        remote_id = str(form.get("remote_id") or "")
        identity_id = str(form.get("identity_id") or "")
        try:
            options = options_from_form(form)
            level = str(options.get("level") or "STUDY")
        except ValueError as exc:
            failure = ToolResult(
                tool_id="c-find-advanced",
                tool_name=get_tool("c-find-advanced").name,
                ok=False,
                summary=str(exc),
            )
            extras = _find_advanced_extras(failure, "STUDY")
            if _hx(request):
                return templates.TemplateResponse(
                    request,
                    "partials/find_advanced_result.html",
                    {"request": request, "result": failure, **extras},
                    status_code=400,
                )
            return _c_find_advanced_page(
                request,
                result=failure,
                remote_id=remote_id,
                identity_id=identity_id,
                status_code=400,
            )
        try:
            result = execute_tool(
                "c-find-advanced",
                remote_id or None,
                options,
                identity_id or None,
            )
        except HTTPException as exc:
            failure = ToolResult(
                tool_id="c-find-advanced",
                tool_name=get_tool("c-find-advanced").name,
                ok=False,
                summary=str(exc.detail),
            )
            extras = _find_advanced_extras(failure, level)
            if _hx(request):
                return templates.TemplateResponse(
                    request,
                    "partials/find_advanced_result.html",
                    {"request": request, "result": failure, **extras},
                    status_code=exc.status_code,
                )
            return _c_find_advanced_page(
                request,
                result=failure,
                remote_id=remote_id,
                identity_id=identity_id,
                level=level,
                status_code=exc.status_code,
            )
        extras = _find_advanced_extras(result, level)
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/find_advanced_result.html",
                {"request": request, "result": result, **extras},
            )
        return _c_find_advanced_page(
            request,
            result=result,
            remote_id=remote_id,
            identity_id=identity_id,
            level=level,
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
            tool.template if tool else "tool.html",
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
        updates: dict[str, Any] = {"id": new_record_id()}
        if not entry.study_instance_uid:
            updates["study_instance_uid"] = str(generate_uid())
        return app.state.store.add_worklist_entry(entry.model_copy(update=updates))

    @app.delete("/api/worklist/{entry_id}")
    def api_delete_worklist_entry(entry_id: str):
        app.state.store.delete_worklist_entry(entry_id)
        return JSONResponse({"ok": True, "id": entry_id})

    @app.get("/api/hl7/messages")
    def api_hl7_messages():
        return app.state.store.list_hl7_messages()

    @app.post("/api/hl7/messages", status_code=201)
    def api_add_hl7_message(message: Hl7Message):
        return app.state.store.add_hl7_message(
            message.model_copy(update={"id": new_record_id(), "created_at": utc_now()})
        )

    @app.delete("/api/hl7/messages/{message_id}")
    def api_delete_hl7_message(message_id: str):
        app.state.store.delete_hl7_message(message_id)
        return JSONResponse({"ok": True, "id": message_id})

    @app.get("/api/config")
    def api_config():
        return app.state.store.load()

    @app.put("/api/config/local")
    def api_put_local(local: LocalAE):
        return app.state.store.save_local(local)

    @app.get("/api/logging")
    def api_logging():
        return app.state.store.load().logging

    @app.put("/api/logging")
    def api_put_logging(settings: LoggingSettings):
        return _apply_logging(settings)

    @app.get("/api/logs")
    def api_logs():
        path = log_path(app.state.store.data_dir)
        text = read_tail(path)
        return {
            "path": str(path),
            "size": path.stat().st_size if path.is_file() else 0,
            "text": text,
        }

    @app.get("/api/remotes")
    def api_remotes():
        return app.state.store.load().remotes

    @app.post("/api/remotes", status_code=201)
    def api_add_remote(remote: RemoteNode):
        remote = remote.model_copy(update={"id": new_record_id(), "created_at": utc_now()})
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
        identity = identity.model_copy(update={"id": new_record_id()})
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
