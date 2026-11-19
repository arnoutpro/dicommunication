"""Dicom Import page: scan a folder, review, change details, send to a PACS.

Reached at ``/tools/dicom-import``. The scan and the send run in background
threads (app/import_session.py); the page polls a small partial while one is
running. Nothing about the patients is stored: the session lives in memory and
is dropped when idle or when the user starts over.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydicom.uid import UID

from app.dicom_import import (
    PATIENT_FIELDS,
    STUDY_FIELDS,
    ImportProblem,
    ScanResult,
    SendOptions,
    edits_from_form,
)
from app.fs_dialog import dialogs_available
from app.import_session import DONE, FAILED, READY, SCANNING, SENDING, ImportSession
from app.routes._shared import _hx, page, templates
from app.tools import get_tool

router = APIRouter()
BASE = "/tools/dicom-import"

SEX_CHOICES = (("", "Not set"), ("F", "F"), ("M", "M"), ("O", "O"))
PLACEHOLDERS = {
    "birth_date": "YYYY-MM-DD",
    "date": "YYYY-MM-DD",
    "time": "HH:MM",
    "name": "FAMILY^GIVEN",
    "referring_physician": "FAMILY^GIVEN",
}


def human_size(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def pretty_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 and value.isdigit() else value


def pretty_time(value: str) -> str:
    return f"{value[:2]}:{value[2:4]}" if len(value) >= 4 and value[:4].isdigit() else value


templates.env.filters.setdefault("human_size", human_size)
templates.env.filters.setdefault("pretty_date", pretty_date)
templates.env.filters.setdefault("uid_name", lambda uid: UID(str(uid)).name)


def _form_fields(table: dict[str, tuple[str, str, int]], prefix: str, current: dict[str, str], values: dict[str, str], errors: dict[str, str]):
    fields = []
    for name, (_keyword, label, _limit) in table.items():
        field_name = f"{prefix}_{name}"
        shown = current.get(name, "")
        if name in ("birth_date", "date"):
            shown = pretty_date(shown)
        elif name == "time":
            shown = pretty_time(shown)
        fields.append(
            {
                "field": field_name,
                "name": name,
                "label": label,
                "value": values.get(field_name, shown),
                "error": errors.get(field_name, ""),
                "placeholder": PLACEHOLDERS.get(name, ""),
            }
        )
    return fields


def build_view(session: ImportSession, scan: ScanResult, values: dict[str, str], errors: dict[str, str], selected: set[str] | None) -> dict[str, Any]:
    """The review as the template draws it: patients with their studies and form fields.

    Indexes run in the same order ``edits_from_form`` reads them back in.
    """
    patients: list[dict[str, Any]] = []
    study_index = 0
    for patient_index, patient in enumerate(scan.patients.values()):
        studies = []
        for study in patient.studies.values():
            studies.append(
                {
                    "study": study,
                    "uid": study.uid,
                    "selected": True if selected is None else study.uid in selected,
                    "fields": _form_fields(
                        STUDY_FIELDS,
                        f"s{study_index}",
                        {
                            "description": study.description,
                            "accession": study.accession,
                            "date": study.date,
                            "time": study.time,
                            "referring_physician": study.referring_physician,
                            "study_id": study.study_id,
                        },
                        values,
                        errors,
                    ),
                }
            )
            study_index += 1
        patients.append(
            {
                "patient": patient,
                "fields": _form_fields(
                    PATIENT_FIELDS,
                    f"p{patient_index}",
                    {"name": patient.name, "patient_id": patient.patient_id, "birth_date": patient.birth_date, "sex": patient.sex},
                    values,
                    errors,
                ),
                "studies": studies,
            }
        )
    return {"patients": patients, "sex_choices": SEX_CHOICES}


def _sessions(request: Request):
    return request.app.state.import_sessions


def _render(
    request: Request,
    session: ImportSession | None,
    *,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    flash: str = "",
    flash_ok: bool = False,
    selected: set[str] | None = None,
    remote_id: str = "",
    identity_id: str = "",
    new_uids: bool = False,
    decompress: bool = True,
    folder: str = "",
    include_headerless: bool = False,
    expired: bool = False,
    partial: bool | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    values, errors = values or {}, errors or {}
    # The scan or send thread can finish at any moment. Read the state once, so the
    # view built here and the page drawn from it agree.
    state = session.state if session is not None else ""
    scan = session.scan if session is not None else None
    view = build_view(session, scan, values, errors, selected) if scan is not None and state in (READY, DONE) else None
    context = page(
        request,
        nav="tools",
        tool=get_tool("dicom-import"),
        tool_id="dicom-import",
        session=session,
        state=state,
        busy=state in (SCANNING, SENDING),
        scan=scan,
        view=view,
        flash=flash,
        flash_ok=flash_ok,
        errors=errors,
        remote_id=remote_id,
        identity_id=identity_id,
        new_uids=new_uids,
        decompress=decompress,
        folder=folder or (session.folder if session else ""),
        include_headerless=include_headerless or (session.include_headerless if session else False),
        expired=expired,
        dialogs_available=dialogs_available(),
        base=BASE,
        relative=lambda path: os.path.relpath(path, scan.root) if scan is not None else path,
        READY=READY,
        DONE=DONE,
        FAILED=FAILED,
    )
    use_partial = _hx(request) if partial is None else partial
    name = "partials/import_body.html" if use_partial else "dicom_import.html"
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _expired(request: Request) -> HTMLResponse:
    return _render(request, None, expired=True, flash="This import has expired. Choose the folder again.", status_code=404)


@router.get(BASE, response_class=HTMLResponse)
def import_start(request: Request) -> HTMLResponse:
    return _render(request, None, partial=False)


@router.post(f"{BASE}/scan")
async def import_scan(request: Request):
    form = await request.form()
    folder = str(form.get("folder") or "").strip()
    headerless = bool(form.get("include_headerless"))
    if not folder:
        return _render(request, None, folder=folder, include_headerless=headerless, flash="Enter the folder to scan.", partial=False, status_code=400)
    try:
        session = _sessions(request).start_scan(folder, headerless)
    except ImportProblem as exc:
        return _render(request, None, folder=folder, include_headerless=headerless, flash=str(exc), partial=False, status_code=400)
    return RedirectResponse(f"{BASE}/{session.id}", status_code=303)


@router.get(BASE + "/{sid}", response_class=HTMLResponse)
def import_session_page(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None:
        return _expired(request)
    return _render(request, session, partial=False)


@router.get(BASE + "/{sid}/body", response_class=HTMLResponse)
def import_session_body(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None:
        return _expired(request)
    return _render(request, session, partial=True)


def _read_form(form) -> tuple[dict[str, str], set[str], str, str, bool, bool]:
    values = {key: str(value) for key, value in form.items() if key not in ("send_study",)}
    selected = {str(item) for item in form.getlist("send_study")}
    return (
        values,
        selected,
        str(form.get("remote_id") or ""),
        str(form.get("identity_id") or ""),
        bool(form.get("new_uids")),
        bool(form.get("decompress")),
    )


@router.post(BASE + "/{sid}/check")
async def import_check(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None or session.scan is None:
        return _expired(request)
    values, selected, remote_id, identity_id, new_uids, decompress = _read_form(await request.form())
    edits, errors = edits_from_form(session.scan, values, new_uids)
    if errors:
        flash, ok = "Some values need a correction. Nothing has been changed.", False
    else:
        count = edits.changed_count()
        flash = f"{count} field{'s' if count != 1 else ''} will change" if count else "No details will change"
        flash += ", and every study, series and image gets a new UID." if new_uids else "."
        flash += " Nothing has been sent."
        ok = True
    return _render(
        request, session, values=values, errors=errors, flash=flash, flash_ok=ok, selected=selected,
        remote_id=remote_id, identity_id=identity_id, new_uids=new_uids, decompress=decompress,
        status_code=200,
    )


@router.post(BASE + "/{sid}/send")
async def import_send(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None or session.scan is None:
        return _expired(request)
    values, selected, remote_id, identity_id, new_uids, decompress = _read_form(await request.form())
    common = dict(values=values, selected=selected, remote_id=remote_id, identity_id=identity_id, new_uids=new_uids, decompress=decompress)
    edits, errors = edits_from_form(session.scan, values, new_uids)
    if errors:
        return _render(request, session, errors=errors, flash="Some values need a correction. Nothing was sent.", **common)
    config = request.app.state.store.load()
    remote = config.get_remote(remote_id) if remote_id else None
    if remote is None:
        return _render(request, session, flash="Choose the PACS to send to.", **common)
    if not selected:
        return _render(request, session, flash="Tick at least one study to send.", **common)
    try:
        local = config.calling_ae(identity_id or None)
    except KeyError:
        return _render(request, session, flash="That virtual AE no longer exists.", **common)
    try:
        _sessions(request).start_send(session, local, remote, edits, SendOptions(study_uids=selected, decompress=decompress))
    except ImportProblem as exc:
        return _render(request, session, flash=str(exc), **common)
    return _render(request, session, remote_id=remote_id, identity_id=identity_id)


@router.post(BASE + "/{sid}/cancel")
def import_cancel(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None:
        return _expired(request)
    _sessions(request).cancel(session)
    return _render(request, session, flash="Stopping after the current file…", flash_ok=True)


@router.post(BASE + "/{sid}/again")
def import_again(request: Request, sid: str) -> HTMLResponse:
    session = _sessions(request).get(sid)
    if session is None:
        return _expired(request)
    _sessions(request).reset_send(session)
    return _render(request, session)


@router.post(BASE + "/{sid}/discard")
def import_discard(request: Request, sid: str):
    _sessions(request).discard(sid)
    return RedirectResponse(BASE, status_code=303)
