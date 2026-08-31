"""Local AE, remote node, and virtual identity configuration pages."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import log
from app.models import LocalAE, RemoteNode, VirtualAE
from app.routes._shared import _as_bool, _first_error, page, templates

router = APIRouter()


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


@router.get("/config", response_class=HTMLResponse)
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


@router.get("/config/local", response_class=HTMLResponse)
def config_local_page(request: Request, saved: str | None = None) -> HTMLResponse:
    return config_view(request, page_id="local", saved=saved)


@router.get("/config/identities", response_class=HTMLResponse)
def config_identities_page(
    request: Request,
    edit: str | None = None,
    saved: str | None = None,
) -> HTMLResponse:
    config = request.app.state.store.load()
    return config_view(
        request,
        page_id="identities",
        editing_identity=config.get_identity(edit),
        saved=saved,
    )


@router.get("/config/remotes", response_class=HTMLResponse)
def config_remotes_page(
    request: Request,
    edit: str | None = None,
    saved: str | None = None,
) -> HTMLResponse:
    config = request.app.state.store.load()
    return config_view(
        request,
        page_id="remotes",
        editing=config.get_remote(edit) if edit else None,
        saved=saved,
    )


@router.post("/config/local")
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
    storage_scp_enabled: str | None = Form(None),
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
            storage_scp_enabled=_as_bool(storage_scp_enabled),
        )
    except ValidationError as exc:
        return config_view(request, page_id="local", error=_first_error(exc), status_code=400)
    request.app.state.store.save_local(local)
    log.info(
        "Saved local AE %s on %s:%s (MWL SCP %s, SR C-STORE %s)",
        local.ae_title,
        local.host,
        local.port,
        "on" if local.mwl_scp_enabled else "off",
        "on" if local.storage_scp_enabled else "off",
    )
    request.app.state.mwl_scp.restart()
    return RedirectResponse("/config/local?saved=local", status_code=303)


@router.post("/config/remotes")
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
        config = request.app.state.store.load()
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
            request.app.state.store.update_remote(remote_id, remote)
        except KeyError:
            raise HTTPException(status_code=404, detail="Remote node not found") from None
        log.info("Updated remote %s (%s %s)", remote.name, remote.ae_title, remote.endpoint)
        return RedirectResponse("/config/remotes?saved=remote", status_code=303)
    request.app.state.store.add_remote(remote)
    log.info("Added remote %s (%s %s)", remote.name, remote.ae_title, remote.endpoint)
    return RedirectResponse("/config/remotes?saved=remote", status_code=303)


@router.post("/config/remotes/{remote_id}/delete")
def delete_remote(request: Request, remote_id: str):
    request.app.state.store.delete_remote(remote_id)
    log.info("Deleted remote %s", remote_id)
    return RedirectResponse("/config/remotes?saved=deleted", status_code=303)


@router.post("/config/identities")
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
        config = request.app.state.store.load()
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
            request.app.state.store.update_identity(identity_id, identity)
        except KeyError:
            raise HTTPException(status_code=404, detail="Virtual local AE not found") from None
        log.info("Updated virtual AE %s (%s)", identity.name, identity.ae_title)
        return RedirectResponse("/config/identities?saved=identity", status_code=303)
    request.app.state.store.add_identity(identity)
    log.info("Added virtual AE %s (%s)", identity.name, identity.ae_title)
    return RedirectResponse("/config/identities?saved=identity", status_code=303)


@router.post("/config/identities/{identity_id}/delete")
def delete_identity(request: Request, identity_id: str):
    request.app.state.store.delete_identity(identity_id)
    log.info("Deleted virtual AE %s", identity_id)
    return RedirectResponse("/config/identities?saved=deleted", status_code=303)
