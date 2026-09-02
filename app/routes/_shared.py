"""Shared helpers used by more than one router module.

Route handlers no longer close over `app`, `store`, or `templates` from
`create_app`; instead they take `request: Request` and reach the app state
through `request.app.state`.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app import __version__
from app.applog import configure as configure_logging, log
from app.models import LoggingSettings, ToolResult
from app.paths import package_dir
from app.shell import (
    PRODUCT_NAMES,
    SHELL_DICOMM,
    display_tool_name,
    public_href,
    tool_groups_for_shell,
    tools_for_shell,
)
from app.tools import get_tool

BASE_DIR = package_dir()

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    loc = error.get("loc") or ("config",)
    return f"{loc[-1]}: {error.get('msg', 'invalid value')}"


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _as_bool(value: str | None) -> bool:
    return (value or "").lower() in {"on", "true", "1", "yes"}


def page(request: Request, **extra: object) -> dict:
    app = request.app
    config = app.state.store.load()
    scp = getattr(app.state, "mwl_scp", None)
    shell = getattr(request.state, "shell", SHELL_DICOMM)
    return {
        "request": request,
        "config": config,
        "shell": shell,
        "product_name": PRODUCT_NAMES[shell],
        "href": lambda path: public_href(path, shell=shell),
        "display_tool_name": display_tool_name,
        "tools": tools_for_shell(shell),
        "tool_groups": tool_groups_for_shell(shell),
        "results": app.state.store.list_results(10),
        "mwl_scp_running": bool(scp and scp.running and config.local.mwl_scp_enabled),
        "mwl_scp_error": getattr(scp, "last_error", None) if scp else None,
        "storage_scp_running": bool(scp and scp.running and config.local.storage_scp_enabled),
        "app_version": __version__,
        **extra,
    }


def execute_tool(
    request: Request,
    tool_id: str,
    remote_id: str | None = None,
    options: dict[str, Any] | None = None,
    identity_id: str | None = None,
) -> ToolResult:
    app = request.app
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
    if tool_id in ("c-find-advanced", "tag-editor"):
        options = dict(options or {})
        scp = getattr(app.state, "mwl_scp", None)
        options.setdefault("_listen_ae", config.local.ae_title)
        options.setdefault("_storage_enabled", config.local.storage_scp_enabled)
        options.setdefault(
            "_storage_running",
            bool(scp and scp.running and config.local.storage_scp_enabled),
        )
        options.setdefault("_storage_error", getattr(scp, "last_error", None) if scp else None)
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


def apply_logging(request: Request, settings: LoggingSettings) -> LoggingSettings:
    app = request.app
    app.state.store.save_logging(settings)
    configure_logging(app.state.store.data_dir, settings)
    log.info(
        "Logging set to %s, rotate at %s MB, keep %s files",
        settings.level,
        settings.max_megabytes,
        settings.backup_count,
    )
    return settings
