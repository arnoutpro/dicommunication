"""Dicom Send: find studies on one archive and send them to another registered archive.

The one-off counterpart of a Dicom Router rule that queries a PACS and forwards what it finds.
The same plumbing as Dicom Cleaner, without the pixel edit: a Study-level C-FIND on the source,
then each checked study (or its series or images) is C-MOVEd to this workstation's Storage SCP
and C-STOREd to the destination with its own UIDs, so the destination gets an identical copy.
The images only pass through memory; nothing is written to disk, and nothing about the patients
is kept by this app.
"""

from __future__ import annotations

import time
from typing import Any

from app.dicom_client import capture_pynetdicom_log
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.anonymize import _retrieve_selections
from app.tools.base import elapsed_ms
from app.tools.cleaner import MAX_QUERY_RESULTS, CleanerTool, _send_back, _study_query
from app.tools.find_advanced import retrieve_storage_gate_message
from app.tools.find_keys import validate_study_values
from app.tools.registry import register


class DicomSendTool(CleanerTool):
    # Query step, level handling and the result table are Dicom Cleaner's, unchanged. Only the run differs.
    id = "dicom-send"
    name = "Dicom Send"
    description = (
        "Find studies on one archive (C-FIND) and send them to another registered archive: "
        "each is retrieved with C-MOVE to this workstation and forwarded with C-STORE, unchanged."
    )
    category = "dimse"
    template = "dicom_send.html"

    def _query(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        """The Dicomtag Analytics query, at Study level: the match values and the columns come from the form."""
        try:
            values = validate_study_values(options.get("values") or {})
        except ValueError as exc:
            return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary=str(exc), remote_id=remote.id, remote_name=remote.name)
        columns = ["StudyInstanceUID"]
        for keyword in options.get("return_keys") or []:
            if keyword not in columns:
                columns.append(keyword)
        started = time.perf_counter()
        with capture_pynetdicom_log() as log_stream:
            find_started = time.perf_counter()
            records, error, contexts = _study_query(local, remote, values, columns)
            log = log_stream.getvalue().strip()
        ok = not error
        message = f"Found {len(records)} stud" + ("y" if len(records) == 1 else "ies") if ok else (error or "Query failed.")
        if ok and len(records) >= MAX_QUERY_RESULTS:
            message += f" (the list stops at {MAX_QUERY_RESULTS}; narrow the filters to see the rest)"
        steps = [ToolStep(name="C-FIND", ok=ok, message=message, duration_ms=elapsed_ms(find_started))]
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=message,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts, records=records,
        )

    def _run(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        def fail(summary: str, **extra: Any) -> ToolResult:
            return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary=summary, remote_id=remote.id, remote_name=remote.name, **extra)

        config_remotes = options.get("_remotes") or {}
        destination_id = str(options.get("destination_remote_id") or "").strip()
        destination = config_remotes.get(destination_id) if destination_id else None
        if destination is None:
            return fail("Choose the archive to send to.")
        if destination.id == remote.id:
            return fail("The source and the destination are the same archive. Choose another one to send to.")

        level = str(options.get("level") or "STUDY").strip().upper()
        if level not in ("STUDY", "SERIES", "IMAGE"):
            level = "STUDY"
        resolved = self._resolve_entities(local, remote, options, level)
        if isinstance(resolved, ToolResult):
            return resolved
        entities, browse_step = resolved

        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae, port=int(local.port), calling_ae=local.ae_title,
            enabled=bool(options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False))),
            running=bool(options.get("_storage_running", False)),
            storage_error=str(options.get("_storage_error") or "").strip(),
        )
        if blocked:
            return fail(blocked)

        started = time.perf_counter()
        steps: list[ToolStep] = [browse_step] if browse_step else []
        records: list[dict[str, Any]] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, move_error, contexts, outcomes = _retrieve_selections(local, remote, entities, dest_ae)
            if not datasets:
                steps.append(ToolStep(
                    name="C-MOVE", ok=False, message=move_error or "No instances were stored on this AE.",
                    duration_ms=elapsed_ms(move_started),
                ))
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=move_error or "Nothing was retrieved, so nothing was sent.",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log_stream.getvalue().strip(), contexts=contexts,
                )
            empty = [item for item in outcomes if not item["count"]]
            table = "\n".join(
                f"{item['label']}: {item['count']} instance(s)" + (f" ({item['error']})" if item["error"] else "")
                for item in outcomes
            )
            steps.append(ToolStep(
                name="C-MOVE", ok=not empty,
                message=f"Retrieved {len(datasets)} instance(s) from {remote.name}, for {len(entities) - len(empty)} of {len(entities)} selection(s)"
                + (f". Nothing came back for: {'; '.join(item['label'] + (' (' + item['error'] + ')' if item['error'] else '') for item in empty)}" if empty else "")
                + (f". Last error: {move_error}" if move_error and not empty else ""),
                duration_ms=elapsed_ms(move_started),
                details={"output": table},
            ))

            send_started = time.perf_counter()
            stored, send_error = _send_back(local, destination, datasets)
            for index, ds in enumerate(datasets):
                if index < stored:
                    status, err = "sent", ""
                elif index == stored:
                    status, err = "failed", send_error or "C-STORE failed"
                else:
                    status, err = "failed", "Not attempted: the association stopped after an earlier C-STORE failure."
                records.append({
                    "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "")),
                    "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "")),
                    "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "")),
                    "status": status,
                    "error": err,
                })
            if send_error and not stored and not send_error.startswith("C-STORE status"):
                # Nothing was stored and the destination never answered a C-STORE: it refused the association itself.
                send_error += (
                    f". {destination.name} must know the calling AE Title {local.ae_title} as an allowed AE (registered with this "
                    f"computer's address and port), and accept C-STORE from it, or the send fails"
                )
            steps.append(ToolStep(
                name="C-STORE", ok=stored == len(datasets) and not send_error,
                message=f"Sent {stored} of {len(datasets)} instance(s) to {destination.name} with their original UIDs"
                + (f". Error: {send_error}" if send_error else ""),
                duration_ms=elapsed_ms(send_started),
            ))
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        if ok:
            summary = f"Sent {stored} instance(s) from {remote.name} to {destination.name}"
        elif stored:
            summary = f"Sent {stored} instance(s) from {remote.name} to {destination.name}, but not everything: " + " | ".join(
                step.message for step in steps if not step.ok
            )
        else:
            summary = next((step.message for step in steps if not step.ok), "Dicom Send failed")
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts, records=records,
        )


register(DicomSendTool())
