"""HL7 send, PDF-to-DICOM, advanced C-FIND, and the generic tool routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.applog import log
from app.fs_dialog import dialogs_available, pick_directory
from app.hl7 import DEFAULT_PORT, display_hl7, sample_adt_a01
from app.models import Hl7Message, ToolResult
from app.pdf_dicom import (
    CollectError,
    MAX_FILES,
    MAX_PDF_BYTES,
    MAX_ZIP_BYTES,
    is_pdf_filename,
    list_directory_pdfs,
)
from app.routes._shared import _as_bool, _first_error, _hx, execute_tool, page, templates
from app.routes.anonymize import _anonymize_page
from app.shell import SHELL_ANONYMIZE, SHELL_DICOMM, SHELL_VUE, display_tool_name
from app.tools import get_tool
from app.tools.find_keys import catalog_payload, column_labels, options_from_form

router = APIRouter()


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
            sample_adt_a01(sending_app=request.app.state.store.load().local.ae_title)
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
            messages=request.app.state.store.list_hl7_messages(),
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


@router.post("/tools/hl7-send/run")
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
        result = execute_tool(request, "hl7-send", remote_id or None, options)
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
                {"request": request, "result": failure, "display_tool_name": display_tool_name},
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
            {"request": request, "result": result, "display_tool_name": display_tool_name},
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


@router.post("/tools/hl7-send/messages")
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
    saved = request.app.state.store.add_hl7_message(entry)
    log.info("Saved HL7 draft %s", saved.name)
    return RedirectResponse(f"/tools/hl7-send?load={saved.id}&saved=1", status_code=303)


@router.post("/tools/hl7-send/messages/{message_id}/delete")
def delete_hl7_message(request: Request, message_id: str):
    request.app.state.store.delete_hl7_message(message_id)
    log.info("Deleted HL7 draft %s", message_id)
    return RedirectResponse("/tools/hl7-send?saved=deleted", status_code=303)


def _find_advanced_extras(
    request: Request, result: ToolResult | None, level: str = "STUDY"
) -> dict[str, Any]:
    records = result.records if result else []
    columns = [key for key in (list(records[0].keys()) if records else []) if key != "sr_items"]
    resolved = level
    kind = "table"
    truncated = False
    if result:
        for step in result.steps:
            step_level = step.details.get("level")
            if step_level:
                resolved = str(step_level)
            step_kind = step.details.get("kind")
            if step_kind:
                kind = str(step_kind)
            if step.details.get("truncated"):
                truncated = True
    labels = dict(zip(columns, column_labels(columns)))
    labels.update(
        {
            "DocumentTitle": "Document Title",
            "Findings": "Findings",
            "Impression": "Impression",
            "CompletionFlag": "Completion Flag",
            "VerificationFlag": "Verification Flag",
            "SOPClass": "SOP Class",
            "sr_text": "Report text",
        }
    )
    config = request.app.state.store.load()
    scp = getattr(request.app.state, "mwl_scp", None)
    return {
        "find_catalog": catalog_payload(),
        "find_level": resolved,
        "find_kind": kind,
        "find_truncated": truncated,
        "find_labels": labels,
        "find_columns": columns,
        "storage_scp_enabled": config.local.storage_scp_enabled,
        "storage_scp_running": bool(scp and scp.running and config.local.storage_scp_enabled),
        "listen_ae": config.local.ae_title,
        "listen_port": config.local.port,
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
            **_find_advanced_extras(request, result, level),
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
    config = request.app.state.store.load()
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


@router.post("/tools/pdf-store/run")
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
        result = execute_tool(request, "pdf-store", remote_id or None, options, identity_id or None)
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
                {"request": request, "result": failure, "display_tool_name": display_tool_name},
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
            {"request": request, "result": result, "display_tool_name": display_tool_name},
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


@router.post("/tools/pdf-store/scan")
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


@router.get("/api/tools/pdf-store/scan")
def api_pdf_store_scan(directory: str = ""):
    try:
        return list_directory_pdfs(directory)
    except CollectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/fs/pick-directory")
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


@router.get("/tools/{tool_id}", response_class=HTMLResponse)
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
            stored = request.app.state.store.get_hl7_message(load)
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
    if tool_id == "anonymize":
        if getattr(request.state, "shell", SHELL_DICOMM) != SHELL_ANONYMIZE:
            return RedirectResponse("/anonymize/", status_code=303)
        return _anonymize_page(request)
    return templates.TemplateResponse(
        request,
        tool.template,
        page(request, nav="tools", tool=tool, tool_id=tool.id, result=None),
    )


@router.post("/tools/c-find-advanced/run")
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
        extras = _find_advanced_extras(request, failure, "STUDY")
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/find_advanced_result.html",
                page(request, result=failure, **extras),
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
            request,
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
        extras = _find_advanced_extras(request, failure, level)
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/find_advanced_result.html",
                page(request, result=failure, **extras),
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
    extras = _find_advanced_extras(request, result, level)
    if _hx(request):
        return templates.TemplateResponse(
            request,
            "partials/find_advanced_result.html",
            page(request, result=result, **extras),
        )
    return _c_find_advanced_page(
        request,
        result=result,
        remote_id=remote_id,
        identity_id=identity_id,
        level=level,
    )


def _tag_editor_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    remote_id: str = "",
    identity_id: str = "",
    study_uid: str = "",
    series_uid: str = "",
    accession_number: str = "",
    study_date: str = "",
    final_sign_timestamp: str = "",
    last_composed_by: str = "",
    status_code: int = 200,
):
    tool = get_tool("tag-editor")
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
            study_uid=study_uid,
            series_uid=series_uid,
            accession_number=accession_number,
            study_date=study_date,
            final_sign_timestamp=final_sign_timestamp,
            last_composed_by=last_composed_by,
        ),
        status_code=status_code,
    )


@router.post("/tools/tag-editor/run")
async def tag_editor_run(request: Request):
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    study_uid = str(form.get("study_uid") or "").strip()
    series_uid = str(form.get("series_uid") or "").strip()
    accession_number = str(form.get("accession_number") or "").strip()
    study_date = str(form.get("study_date") or "").strip()
    final_sign_timestamp = str(form.get("final_sign_timestamp") or "").strip()
    last_composed_by = str(form.get("last_composed_by") or "").strip()
    action = str(form.get("action") or "fetch").strip()
    echo = {
        "remote_id": remote_id,
        "identity_id": identity_id,
        "study_uid": study_uid,
        "series_uid": series_uid,
        "accession_number": accession_number,
        "study_date": study_date,
        "final_sign_timestamp": final_sign_timestamp,
        "last_composed_by": last_composed_by,
    }
    try:
        result = execute_tool(
            request,
            "tag-editor",
            remote_id or None,
            {
                "action": action,
                "study_uid": study_uid,
                "series_uid": series_uid,
                "accession_number": accession_number,
                "study_date": study_date,
                "final_sign_timestamp": final_sign_timestamp,
                "last_composed_by": last_composed_by,
            },
            identity_id or None,
        )
    except HTTPException as exc:
        failure = ToolResult(
            tool_id="tag-editor",
            tool_name=get_tool("tag-editor").name,
            ok=False,
            summary=str(exc.detail),
        )
        if _hx(request):
            return templates.TemplateResponse(
                request,
                "partials/result.html",
                {"request": request, "result": failure, "display_tool_name": display_tool_name},
                status_code=exc.status_code,
            )
        return _tag_editor_page(request, result=failure, status_code=exc.status_code, **echo)
    if action == "lookup" and result.ok and len(result.records) == 1:
        echo["study_uid"] = str(result.records[0].get("StudyInstanceUID") or "")
    if _hx(request):
        return templates.TemplateResponse(
            request,
            "partials/result.html",
            {"request": request, "result": result, "display_tool_name": display_tool_name},
        )
    return _tag_editor_page(request, result=result, **echo)


@router.post("/tools/{tool_id}/run")
def run_tool_form(
    request: Request,
    tool_id: str,
    remote_id: str = Form(...),
    identity_id: str = Form(""),
):
    try:
        result = execute_tool(request, tool_id, remote_id, identity_id=identity_id or None)
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
                {"request": request, "result": failure, "display_tool_name": display_tool_name},
                status_code=exc.status_code,
            )
        raise
    if _hx(request):
        return templates.TemplateResponse(
            request,
            "partials/result.html",
            {"request": request, "result": result, "display_tool_name": display_tool_name},
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
