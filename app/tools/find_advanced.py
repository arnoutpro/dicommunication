"""Study Root C-FIND at STUDY, SERIES, or IMAGE with selectable return keys."""

from __future__ import annotations

import time
from typing import Any

from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.find_keys import (
    LEVEL_LABELS,
    MAX_RECORDS,
    build_identifier,
    keys_for_level,
    options_from_payload,
    record_from_dataset,
    selected_keywords,
    validate_query,
)
from app.tools.registry import register

PENDING = {0xFF00, 0xFF01}


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _noun(level: str, count: int) -> str:
    if level == "STUDY":
        return "study" if count == 1 else "studies"
    if level == "SERIES":
        return "series"
    return "image" if count == 1 else "images"


class CFindAdvancedTool(BaseTool):
    id = "c-find-advanced"
    name = "C-FIND Advanced"
    description = (
        "Query/Retrieve Study Root C-FIND at Study, Series, or Image level with every "
        "searchable key. Series and Image follow hierarchical FIND: parent Unique keys "
        "must be present. Results are a column-aligned table with copy, CSV, and JSON export. "
        "This searches stored studies, not the modality worklist."
    )
    category = "dimse"
    template = "c_find_advanced.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node first.",
            )

        try:
            parsed = options_from_payload(options)
        except ValueError as exc:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=str(exc),
                remote_id=remote.id,
                remote_name=remote.name,
            )

        level = parsed["level"]
        values = parsed["values"]
        return_keys = parsed["return_keys"]
        blocked = validate_query(level, values)
        if blocked:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=blocked,
                remote_id=remote.id,
                remote_name=remote.name,
            )

        columns = selected_keywords(level, values, return_keys)
        if not columns:
            columns = [key.keyword for key in keys_for_level(level)]

        started = time.perf_counter()
        steps: list[ToolStep] = []
        contexts: list[dict[str, Any]] = []
        records: list[dict[str, str]] = []
        truncated = False
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                identifier = build_identifier(level, values, columns)
            except (ValueError, TypeError, KeyError) as exc:
                return ToolResult(
                    tool_id=self.id,
                    tool_name=self.name,
                    ok=False,
                    summary=f"Could not build the C-FIND identifier: {exc}",
                    remote_id=remote.id,
                    remote_name=remote.name,
                )
            try:
                assoc_started = time.perf_counter()
                _ae, assoc = associate(
                    local, remote, [StudyRootQueryRetrieveInformationModelFind]
                )
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Study Root Query/Retrieve FIND",
                    "This is not a Q/R SCP (and it is not MWL).",
                )
                if not assoc.is_established:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected or reject_reason(assoc),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or (
                                "Association opened, but Study Root Query/Retrieve FIND "
                                "was not accepted. This is not a Q/R SCP (and it is not MWL)."
                            ),
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    assoc.release()
                else:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=True,
                            message=f"Study Root FIND SOP Class accepted · {LEVEL_LABELS[level]} level",
                            duration_ms=_elapsed_ms(assoc_started),
                        )
                    )
                    find_started = time.perf_counter()
                    failed_status = None
                    for status, identifier_ds in assoc.send_c_find(
                        identifier, StudyRootQueryRetrieveInformationModelFind
                    ):
                        if not status:
                            failed_status = "No C-FIND response (timeout, abort, or invalid PDU)."
                            break
                        code = int(status.Status)
                        if code in PENDING and identifier_ds is not None:
                            if len(records) >= MAX_RECORDS:
                                truncated = True
                                continue
                            records.append(record_from_dataset(identifier_ds, columns))
                        elif code == 0x0000:
                            continue
                        else:
                            failed_status = f"C-FIND status 0x{code:04X}"
                            break
                    if failed_status:
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=False,
                                message=failed_status,
                                duration_ms=_elapsed_ms(find_started),
                            )
                        )
                    else:
                        extra = f" (first {MAX_RECORDS})" if truncated else ""
                        steps.append(
                            ToolStep(
                                name="C-FIND",
                                ok=True,
                                message=(
                                    f"{len(records)} {_noun(level, len(records))} at "
                                    f"{LEVEL_LABELS[level]} level{extra}"
                                ),
                                duration_ms=_elapsed_ms(find_started),
                                details={
                                    "count": len(records),
                                    "level": level,
                                    "truncated": truncated,
                                },
                            )
                        )
                    assoc.release()
                    steps.append(ToolStep(name="Release", ok=True, message="Association released"))
            except Exception as exc:  # noqa: BLE001
                steps.append(
                    ToolStep(
                        name="C-FIND",
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                        duration_ms=_elapsed_ms(started),
                    )
                )
            finally:
                if assoc is not None and getattr(assoc, "is_established", False):
                    try:
                        assoc.abort()
                    except Exception:  # noqa: BLE001
                        pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        extra = f" (stopped at {MAX_RECORDS})" if truncated else ""
        summary = (
            f"Study Root C-FIND ({LEVEL_LABELS[level]}) returned {len(records)} "
            f"{_noun(level, len(records))} from {remote.ae_title}{extra}"
            if ok
            else (failed.message if failed else "C-FIND Advanced failed")
        )
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log,
            contexts=contexts,
            records=records,
        )


register(CFindAdvancedTool())
