"""FastAPI application: config screens, tool runner, and a small JSON API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.applog import configure as configure_logging, log, should_skip_http_log
from app.mwl_scp import WorklistSCP
from app.paths import package_dir
from app.routes import api, config, echo_board, logs, misc, testbench, tools, worklist
from app.shell import (
    SHELL_DICOMM,
    SHELL_VUE,
    is_vue_public_path,
    prefix_redirect_location,
    strip_vue_prefix,
    vue_path_allowed,
)
from app.store import ConfigStore

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

    app.include_router(misc.router)
    app.include_router(logs.router)
    app.include_router(echo_board.router)
    app.include_router(config.router)
    app.include_router(testbench.router)
    app.include_router(worklist.router)
    app.include_router(tools.router)
    app.include_router(api.router)

    return app


app = create_app()
