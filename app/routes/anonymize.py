"""Dicom Anonymizer page and run route.

Reached at ``/anonymize/`` (see app.shell); the middleware strips that
prefix, so this module — like tag_editor's routes — just handles the plain
``/tools/anonymize`` and ``/tools/anonymize/run`` paths underneath it.

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
from app.routes._shared import execute_tool, page, templates
from app.tools import get_tool
from app.tools.anon_engine import MODE_LABELS, MODES
from app.tools.anon_tags import tags_by_category
from app.tools.anonymize import LEVELS
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
            dialogs_available=dialogs_available(),
            anon_modes=MODES,
            anon_mode_labels=MODE_LABELS,
            anon_tag_categories=tags_by_category(),
        ),
        status_code=status_code,
    )


def _entities_from_form(form) -> list[dict[str, str]]:
    """Always the checked studies — the tool itself walks down to Series/Image
    internally when the chosen level asks for it. See app.tools.anonymize.
    """
    return [{"study_uid": uid} for uid in form.getlist("study_uid") if uid]


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

    options: dict[str, Any] = {"action": action, "level": level}
    if action == "run":
        options["entities_json"] = _entities_from_form(form)
        options["mode"] = str(form.get("mode") or "")
        options["remove_patient_action"] = str(form.get("remove_patient_action") or "erase")
        options["output_dir"] = str(form.get("output_dir") or "")
        options["archive"] = str(form.get("archive") or "none")
        options["custom_actions_json"] = _custom_actions_from_form(form)
    else:
        options["patient_id"] = str(form.get("patient_id") or "")
        options["accession_number"] = str(form.get("accession_number") or "")
        options["study_date"] = normalize_da(str(form.get("study_date") or ""))
        options["modality"] = "\\".join(v for v in form.getlist("modality") if v)

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
            remote_id=remote_id, identity_id=identity_id,
        )
    return _anonymize_page(
        request, result=result, level=level, action=action,
        remote_id=remote_id, identity_id=identity_id,
    )
