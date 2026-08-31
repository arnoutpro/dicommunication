"""JSON API routes under /api/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydicom.uid import generate_uid

from app.applog import log_path, read_tail
from app.echo_board import run_all as run_echo_board
from app.echo_board import snapshot as echo_board_snapshot
from app.models import (
    Hl7Message,
    LocalAE,
    LoggingSettings,
    RemoteNode,
    VirtualAE,
    WorklistEntry,
    WorklistQuery,
    new_record_id,
    utc_now,
)
from app.mwl import query_worklist
from app.routes._shared import apply_logging, execute_tool
from app.tools import list_tools

router = APIRouter()


class RunRequest(BaseModel):
    remote_id: str | None = None
    options: dict[str, Any] | None = None
    identity_id: str | None = None


class WorklistApiQuery(WorklistQuery):
    source: str = "local"
    identity_id: str = ""


@router.get("/api/echo-board")
def api_echo_board(request: Request):
    return echo_board_snapshot(request.app.state.store)


@router.post("/api/echo-board/run")
def api_echo_board_run(request: Request):
    return run_echo_board(request.app.state.store)


@router.post("/api/worklist/query")
def api_worklist_query(request: Request, body: WorklistApiQuery):
    return query_worklist(request.app.state.store, body.source, body, body.identity_id or None)


@router.get("/api/worklist")
def api_worklist_local(request: Request):
    return request.app.state.store.list_worklist()


@router.post("/api/worklist", status_code=201)
def api_add_worklist_entry(request: Request, entry: WorklistEntry):
    updates: dict[str, Any] = {"id": new_record_id()}
    if not entry.study_instance_uid:
        updates["study_instance_uid"] = str(generate_uid())
    return request.app.state.store.add_worklist_entry(entry.model_copy(update=updates))


@router.delete("/api/worklist/{entry_id}")
def api_delete_worklist_entry(request: Request, entry_id: str):
    request.app.state.store.delete_worklist_entry(entry_id)
    return JSONResponse({"ok": True, "id": entry_id})


@router.get("/api/hl7/messages")
def api_hl7_messages(request: Request):
    return request.app.state.store.list_hl7_messages()


@router.post("/api/hl7/messages", status_code=201)
def api_add_hl7_message(request: Request, message: Hl7Message):
    return request.app.state.store.add_hl7_message(
        message.model_copy(update={"id": new_record_id(), "created_at": utc_now()})
    )


@router.delete("/api/hl7/messages/{message_id}")
def api_delete_hl7_message(request: Request, message_id: str):
    request.app.state.store.delete_hl7_message(message_id)
    return JSONResponse({"ok": True, "id": message_id})


@router.get("/api/config")
def api_config(request: Request):
    return request.app.state.store.load()


@router.put("/api/config/local")
def api_put_local(request: Request, local: LocalAE):
    saved = request.app.state.store.save_local(local)
    request.app.state.mwl_scp.restart()
    return saved


@router.get("/api/logging")
def api_logging(request: Request):
    return request.app.state.store.load().logging


@router.put("/api/logging")
def api_put_logging(request: Request, settings: LoggingSettings):
    return apply_logging(request, settings)


@router.get("/api/logs")
def api_logs(request: Request):
    path = log_path(request.app.state.store.data_dir)
    text = read_tail(path)
    return {
        "path": str(path),
        "size": path.stat().st_size if path.is_file() else 0,
        "text": text,
    }


@router.get("/api/remotes")
def api_remotes(request: Request):
    return request.app.state.store.load().remotes


@router.post("/api/remotes", status_code=201)
def api_add_remote(request: Request, remote: RemoteNode):
    remote = remote.model_copy(update={"id": new_record_id(), "created_at": utc_now()})
    request.app.state.store.add_remote(remote)
    return remote


@router.put("/api/remotes/{remote_id}")
def api_update_remote(request: Request, remote_id: str, remote: RemoteNode):
    try:
        return request.app.state.store.update_remote(remote_id, remote)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Remote node not found") from exc


@router.delete("/api/remotes/{remote_id}")
def api_delete_remote(request: Request, remote_id: str):
    request.app.state.store.delete_remote(remote_id)
    return JSONResponse({"ok": True, "id": remote_id})


@router.get("/api/identities")
def api_identities(request: Request):
    return request.app.state.store.load().identities


@router.post("/api/identities", status_code=201)
def api_add_identity(request: Request, identity: VirtualAE):
    identity = identity.model_copy(update={"id": new_record_id()})
    request.app.state.store.add_identity(identity)
    return identity


@router.put("/api/identities/{identity_id}")
def api_update_identity(request: Request, identity_id: str, identity: VirtualAE):
    try:
        return request.app.state.store.update_identity(identity_id, identity)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Virtual local AE not found") from exc


@router.delete("/api/identities/{identity_id}")
def api_delete_identity(request: Request, identity_id: str):
    request.app.state.store.delete_identity(identity_id)
    return JSONResponse({"ok": True, "id": identity_id})


@router.get("/api/tools")
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


@router.post("/api/tools/{tool_id}/run")
def api_run_tool(request: Request, tool_id: str, body: RunRequest):
    return execute_tool(request, tool_id, body.remote_id, body.options, body.identity_id)
