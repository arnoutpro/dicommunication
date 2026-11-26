"""Dicom Anonymizer page and run route.

Reached at ``/tools/anonymize``, same as any other tool page.

One combined form drives both actions: "query" runs the Study-level C-FIND
and re-renders the page with a checkable study table; "run" reads the
checked studies (plus level / mode / output folder / archive choices from
the same page) and does the retrieve + anonymize + export.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.fs_dialog import dialogs_available
from app.models import ToolResult
from app.routes._shared import execute_tool, page, study_filter_options, study_query_from_form, templates
from app.routes.cleaner import _resolve_local_remote
from app.tools import get_tool
from app.tools.anon_engine import MODE_LABELS, MODES
from app.tools.anon_tags import tags_by_category
from app.tools.anonymize import LEVELS, list_series
from app.tools.find_keys import normalize_da

router = APIRouter()


def _anonymize_page(
    request: Request,
    *,
    result: ToolResult | None = None,
    level: str = "STUDY",
    action: str = "query",
    nav: str = "tools",
    status_code: int = 200,
    remote_id: str = "",
    identity_id: str = "",
    query: dict[str, Any] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "anonymize.html",
        page(
            request,
            nav=nav,
            tool=get_tool("anonymize"),
            tool_id="anonymize",
            result=result,
            level=level if level in LEVELS else "STUDY",
            action=action,
            remote_id=remote_id,
            identity_id=identity_id,
            query=query or {},
            dialogs_available=dialogs_available(),
            anon_modes=MODES,
            anon_mode_labels=MODE_LABELS,
            anon_tag_categories=tags_by_category(),
        ),
        status_code=status_code,
    )


def _entities_from_form(form) -> list[dict[str, str]]:
    """The checked studies. The tool walks a study down to Series/Image itself when the
    chosen level asks for it (see app.tools.anonymize). A study whose series list was
    opened under its row is sent as exactly the series that are ticked there; a study
    whose list was never opened is sent whole."""
    opened = {uid for uid in form.getlist("series_opened") if uid}
    ticked: dict[str, list[str]] = {}
    for value in form.getlist("series_sel"):
        study_uid, _sep, series_uid = str(value).partition("|")
        if study_uid and series_uid:
            ticked.setdefault(study_uid, []).append(series_uid)
    labels: dict[str, str] = {}
    for value in form.getlist("series_label"):
        series_uid, _sep, text = str(value).partition("|")
        if series_uid and text:
            labels[series_uid] = text
    entities: list[dict[str, str]] = []
    for uid in form.getlist("study_uid"):
        if not uid:
            continue
        if uid in opened:
            entities.extend(
                {"study_uid": uid, "series_uid": series_uid, "label": labels.get(series_uid, "")} for series_uid in ticked.get(uid, [])
            )
        else:
            entities.append({"study_uid": uid})
    return entities


def _custom_actions_from_form(form) -> dict[str, dict[str, str]]:
    actions: dict[str, dict[str, str]] = {}
    for key in form.keys():
        if not key.startswith("custom_action__"):
            continue
        keyword = key[len("custom_action__") :]
        action = str(form.get(key) or "keep").strip()
        if action == "keep":
            continue
        value = str(form.get(f"custom_value__{keyword}") or "")
        actions[keyword] = {"action": action, "value": value}
    return actions


@router.post("/tools/anonymize/run")
async def anonymize_run(request: Request) -> HTMLResponse:
    form = await request.form()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")
    action = str(form.get("action") or "query").strip()
    level = str(form.get("level") or "STUDY").strip().upper()
    if level not in LEVELS:
        level = "STUDY"
    # The query fields sit in the same form as the run options, so they are
    # posted with either action and can be put back into the page afterwards.
    query = study_query_from_form(form)

    options: dict[str, Any] = {"action": action, "level": level}
    if action == "run":
        options["entities_json"] = _entities_from_form(form)
        options["mode"] = str(form.get("mode") or "")
        options["remove_patient_action"] = str(form.get("remove_patient_action") or "erase")
        options["output_dir"] = str(form.get("output_dir") or "")
        options["archive"] = str(form.get("archive") or "none")
        options["layout"] = str(form.get("layout") or "series")
        options["series_names"] = str(form.get("series_names") or "number")
        options["study_names"] = str(form.get("study_names") or "uid")
        options["custom_actions_json"] = _custom_actions_from_form(form)
    else:
        options["patient_id"] = query["patient_id"]
        options["accession_number"] = query["accession_number"]
        options["study_date"] = normalize_da(query["study_date"])
        options["modality"] = "\\".join(query["modalities"])
        options.update(study_filter_options(query))

    try:
        result = execute_tool(request, "anonymize", remote_id or None, options, identity_id or None)
    except HTTPException as exc:
        failure = ToolResult(
            tool_id="anonymize",
            tool_name=get_tool("anonymize").name,
            ok=False,
            summary=str(exc.detail),
        )
        return _anonymize_page(
            request, result=failure, level=level, action=action, status_code=exc.status_code,
            remote_id=remote_id, identity_id=identity_id, query=query,
        )
    return _anonymize_page(
        request, result=result, level=level, action=action,
        remote_id=remote_id, identity_id=identity_id, query=query,
    )


@router.post("/tools/anonymize/series")
async def anonymize_series(request: Request) -> HTMLResponse:
    """The series of one study, as ticked rows, for the picker under a study row."""
    form = await request.form()
    study_uid = str(form.get("series_study") or "").strip()
    remote_id = str(form.get("remote_id") or "")
    identity_id = str(form.get("identity_id") or "")

    def reply(**extra: object) -> HTMLResponse:
        return templates.TemplateResponse(request, "partials/anonymize_series.html", {"request": request, "study_uid": study_uid, **extra})

    if not study_uid:
        return reply(error="No study given.")
    resolved = _resolve_local_remote(request, remote_id, identity_id)
    if isinstance(resolved, str):
        return reply(error=resolved)
    local, remote = resolved
    rows, error, _contexts = list_series(local, remote, study_uid)
    if not rows:
        return reply(error=error or "No series found for this study.")
    rows = sorted(rows, key=lambda row: (int(row["SeriesNumber"]) if str(row.get("SeriesNumber", "")).isdigit() else 10**9, row.get("SeriesInstanceUID", "")))
    return reply(series=rows)
