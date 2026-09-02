"""Correct the Tamar report-composition tags on a study and C-STORE it back.

Two private ELSCINT1 tags only, both report-authoring metadata CS PACS REPORT
writes onto every instance in a study: Tamar Report Final Sign Timestamp
(07a3,10fb, DT) and Tamar Report Study Last Composed By (07a5,1040, LO). This
tool only ever overwrites a value already present -- it never invents either
tag on an instance that never had it, since that would mean asserting a
compose/sign event that this tool did not witness.

Vue mirrors both tags onto every instance in a study, so Push edits every
instance retrieved (the whole study, or one series), not just one.

C-STORE only asks a peer to store an object; it does not define an "update".
Whether Vue treats a C-STORE of an already-known SOP Instance UID as replacing
the original or as a duplicate is PACS-specific and unverified by this tool.
"""

from __future__ import annotations

import time
from typing import Any

from pydicom.datadict import add_private_dict_entries
from pydicom.dataset import Dataset
from pynetdicom.sop_class import (
    SecondaryCaptureImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.dicom_client import (
    associate,
    capture_pynetdicom_log,
    context_rows,
    reject_reason,
    rejected_sop_message,
    uid_name,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.mwl_scp import STORAGE_INBOX
from app.tools.base import BaseTool, elapsed_ms
from app.tools.find_advanced import retrieve_storage_gate_message
from app.tools.find_keys import normalize_da, record_from_dataset
from app.tools.registry import register
from app.tools.store import build_test_instance

PENDING = {0xFF00, 0xFF01}
MAX_INSTANCES = 200
MAX_LOOKUP_RESULTS = 50

STUDY_LOOKUP_COLUMNS = [
    "StudyInstanceUID",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
]

FINAL_SIGN_TIMESTAMP_TAG = (0x07A3, 0x10FB)
LAST_COMPOSED_BY_TAG = (0x07A5, 0x1040)

EDITABLE_TAGS: dict[str, dict[str, Any]] = {
    "final_sign_timestamp": {
        "tag": FINAL_SIGN_TIMESTAMP_TAG,
        "creator": "ELSCINT1",
        "vr": "DT",
        "label": "Tamar Report Final Sign Timestamp",
        "form_field": "final_sign_timestamp",
    },
    "last_composed_by": {
        "tag": LAST_COMPOSED_BY_TAG,
        "creator": "ELSCINT1",
        "vr": "LO",
        "label": "Tamar Report Study Last Composed By",
        "form_field": "last_composed_by",
    },
}

# An unregistered private tag decoded under Implicit VR Little Endian (the
# transfer syntax every DICOM SCP must support, and often the one actually
# negotiated) comes back with VR "UN" and a raw bytes value instead of its
# real VR, and cannot be C-STORE'd back out with a plain string value.
# Registering these two tags with pydicom's private dictionary fixes decoding
# and encoding for both tags under any transfer syntax.
add_private_dict_entries(
    "ELSCINT1",
    {
        (spec["tag"][0] << 16) | spec["tag"][1]: (spec["vr"], "1", spec["label"])
        for spec in EDITABLE_TAGS.values()
    },
)


def to_dicom_dt(value: str) -> str:
    """'2026-09-02T08:41:52' (datetime-local input) -> DICOM DT '20260902084152.000000'."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Final Sign Timestamp is required")
    text = value.replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    digits, _, fraction = text.partition(".")
    digits = "".join(ch for ch in digits if ch.isdigit())
    if len(digits) < 8:
        raise ValueError("Final Sign Timestamp must include at least a date (YYYY-MM-DD)")
    digits = digits.ljust(14, "0")[:14]
    fraction = "".join(ch for ch in fraction if ch.isdigit())
    fraction = (fraction + "000000")[:6]
    return f"{digits}.{fraction}"


def _read_tag(dataset: Dataset, tag: tuple[int, int]) -> str | None:
    if tag not in dataset:
        return None
    value = dataset[tag].value
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    return str(value).strip()


def _set_tag(dataset: Dataset, tag: tuple[int, int], vr: str, value: str) -> bool:
    """Overwrite an existing tag's value. False (no-op) if the tag isn't there."""
    if tag not in dataset:
        return False
    element = dataset[tag]
    element.VR = vr  # guard against a stale "UN" from decoding without this dictionary
    element.value = value
    return True


def _move_identifier(study_uid: str, series_uid: str) -> Dataset:
    identifier = Dataset()
    identifier.StudyInstanceUID = study_uid
    if series_uid:
        identifier.SeriesInstanceUID = series_uid
        identifier.QueryRetrieveLevel = "SERIES"
    else:
        identifier.QueryRetrieveLevel = "STUDY"
    return identifier


def _move_status_message(code: int) -> str:
    hints = {
        0xA801: "Move Destination unknown — Vue does not have this AE as a C-MOVE destination.",
        0xA702: "Unable to perform sub-operations (C-STORE to this AE failed).",
        0xC002: "Unable to process the C-MOVE identifier.",
    }
    hint = hints.get(code)
    return f"C-MOVE status 0x{code:04X}" + (f" — {hint}" if hint else "")


def _lookup_study(
    local: LocalAE, remote: RemoteNode, accession_number: str, study_date: str
) -> tuple[list[dict[str, str]], str | None, list[dict[str, Any]]]:
    """Study-level C-FIND by Accession Number + Study Date. No local Storage SCP needed."""
    identifier = Dataset()
    identifier.QueryRetrieveLevel = "STUDY"
    identifier.AccessionNumber = accession_number
    identifier.StudyDate = study_date
    for keyword in STUDY_LOOKUP_COLUMNS:
        if not hasattr(identifier, keyword):
            setattr(identifier, keyword, "")

    contexts: list[dict[str, Any]] = []
    assoc = None
    try:
        ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(contexts, "Study Root Query/Retrieve FIND")
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve FIND was not accepted.", contexts
        records: list[dict[str, str]] = []
        error = None
        for status, identifier_ds in assoc.send_c_find(
            identifier, StudyRootQueryRetrieveInformationModelFind
        ):
            if not status:
                error = "No C-FIND response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code in PENDING and identifier_ds is not None:
                if len(records) < MAX_LOOKUP_RESULTS:
                    records.append(record_from_dataset(identifier_ds, STUDY_LOOKUP_COLUMNS))
            elif code == 0x0000:
                continue
            else:
                error = f"C-FIND status 0x{code:04X}"
                break
        assoc.release()
        return records, error, contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass


def _retrieve(
    local: LocalAE, remote: RemoteNode, study_uid: str, series_uid: str, dest_ae: str
) -> tuple[list[Dataset], str | None, list[dict[str, Any]]]:
    """One C-MOVE at STUDY or SERIES level. Returns (datasets, error, contexts)."""
    STORAGE_INBOX.begin()
    contexts: list[dict[str, Any]] = []
    assoc = None
    try:
        ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelMove])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(
            contexts,
            "Study Root Query/Retrieve MOVE",
            "Vue must accept C-MOVE (not C-GET) and know this AE as a destination.",
        )
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve MOVE was not accepted.", contexts
        identifier = _move_identifier(study_uid, series_uid)
        error = None
        for status, _remaining in assoc.send_c_move(
            identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove
        ):
            if not status:
                error = "No C-MOVE response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code in PENDING:
                continue
            if code != 0x0000:
                error = _move_status_message(code)
            break
        assoc.release()
        time.sleep(0.05)
        return STORAGE_INBOX.finish(), error, contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass
        STORAGE_INBOX.finish()


def _display_tag_value(dataset: Dataset, tag: tuple[int, int]) -> str:
    """Distinguish 'tag exists but is blank' from 'tag isn't there at all' — both
    would otherwise show as the same empty cell, which is exactly the distinction
    that matters for deciding whether Push can do anything.
    """
    value = _read_tag(dataset, tag)
    if value is None:
        return "(not present)"
    if value == "":
        return "(present, empty)"
    return value


def _instance_row(dataset: Dataset) -> dict[str, Any]:
    row = {
        "sop_instance_uid": str(getattr(dataset, "SOPInstanceUID", "")),
        "sop_class": uid_name(getattr(dataset, "SOPClassUID", "")),
        "modality": str(getattr(dataset, "Modality", "")),
    }
    for key, spec in EDITABLE_TAGS.items():
        row[key] = _display_tag_value(dataset, spec["tag"])
    return row


SEED_VARIANTS = (
    ("blank", "present, blank value"),
    ("absent", "not present at all"),
)


def _build_seed_instance(variant: str) -> Dataset:
    """A synthetic test instance (same ARNPRO^TESTBENCH patient as the C-STORE test
    tool, fresh UIDs) for checking, without touching real patient data, whether a
    C-STORE actually applies when these tags start out missing rather than already
    filled in. 'blank' adds both tags with an empty value; 'absent' adds neither.
    """
    ds = build_test_instance()
    if variant == "blank":
        for spec in EDITABLE_TAGS.values():
            block = ds.private_block(spec["tag"][0], spec["creator"], create=True)
            block.add_new(spec["tag"][1] & 0xFF, spec["vr"], "")
    return ds


class TagEditorTool(BaseTool):
    id = "tag-editor"
    name = "Tag Editor"
    description = (
        "Correct Tamar Report Final Sign Timestamp and Tamar Report Study Last Composed "
        "By (private ELSCINT1 tags CS PACS REPORT writes onto every instance in a study) "
        "when a workflow gets stuck, and C-STORE the correction back to Vue. Only "
        "overwrites a value already present. Whether Vue treats the C-STORE as an update "
        "or a duplicate is PACS-specific and not verified by this tool."
    )
    category = "dimse"
    template = "tag_editor.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Select a remote DICOM node first.",
            )
        options = options or {}
        action = str(options.get("action") or "fetch").strip()

        if action == "lookup":
            return self._lookup(local, remote, options)
        if action == "seed_test":
            return self._seed_test(local, remote)

        study_uid = str(options.get("study_uid") or "").strip()
        series_uid = str(options.get("series_uid") or "").strip()
        if not study_uid:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Study Instance UID is required. Find Study can look it up "
                "from Accession Number and Study Date.",
                remote_id=remote.id, remote_name=remote.name,
            )

        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        storage_enabled = bool(
            options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False))
        )
        storage_running = bool(options.get("_storage_running", False))
        storage_error = str(options.get("_storage_error") or "").strip()
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae,
            port=int(local.port),
            calling_ae=local.ae_title,
            enabled=storage_enabled,
            running=storage_running,
            storage_error=storage_error,
        )
        if blocked:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary=blocked,
                remote_id=remote.id, remote_name=remote.name,
            )

        if action == "push":
            return self._push(local, remote, study_uid, series_uid, dest_ae, options)
        return self._fetch(local, remote, study_uid, series_uid, dest_ae)

    def _seed_test(self, local: LocalAE, remote: RemoteNode) -> ToolResult:
        """C-STORE two synthetic test studies (patient ARNPRO^TESTBENCH), one with
        both tags present-but-blank and one with them entirely absent, so Push can
        be tested against the actual missing-data case instead of an already-filled
        study. Plain C-STORE, like the C-STORE test tool — no local Storage SCP
        needed, unlike Fetch/Push.
        """
        started = time.perf_counter()
        steps: list[ToolStep] = []
        records: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        instances = {variant: _build_seed_instance(variant) for variant, _label in SEED_VARIANTS}
        assoc = None
        with capture_pynetdicom_log() as log_stream:
            try:
                _ae, assoc = associate(local, remote, [SecondaryCaptureImageStorage])
                contexts = context_rows(assoc)
                rejected = rejected_sop_message(
                    contexts,
                    "Secondary Capture Image Storage",
                    "This node is not a Storage SCP for that SOP Class.",
                )
                if not getattr(assoc, "is_established", False):
                    steps.append(ToolStep(name="Association", ok=False, message=rejected or reject_reason(assoc)))
                elif not assoc.accepted_contexts:
                    steps.append(
                        ToolStep(
                            name="Association",
                            ok=False,
                            message=rejected
                            or "Secondary Capture Image Storage was not accepted.",
                        )
                    )
                else:
                    for variant, label in SEED_VARIANTS:
                        dataset = instances[variant]
                        store_started = time.perf_counter()
                        status = assoc.send_c_store(dataset)
                        ok = bool(status) and int(status.Status) == 0x0000
                        code = int(status.Status) if status else None
                        records.append(
                            {
                                "variant": label,
                                "study_instance_uid": str(dataset.StudyInstanceUID),
                                "sop_instance_uid": str(dataset.SOPInstanceUID),
                                "store": "stored" if ok else "failed",
                            }
                        )
                        steps.append(
                            ToolStep(
                                name=f"C-STORE ({label})",
                                ok=ok,
                                message=(
                                    f"Study Instance UID {dataset.StudyInstanceUID}"
                                    if ok
                                    else (
                                        f"DIMSE status 0x{code:04X}"
                                        if code is not None
                                        else "No C-STORE response."
                                    )
                                ),
                                duration_ms=elapsed_ms(store_started),
                            )
                        )
            finally:
                if assoc is not None and getattr(assoc, "is_established", False):
                    try:
                        assoc.release()
                    except Exception:  # noqa: BLE001
                        try:
                            assoc.abort()
                        except Exception:  # noqa: BLE001
                            pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        summary = (
            "Seeded 2 synthetic test studies (patient ARNPRO^TESTBENCH) — copy a "
            "Study Instance UID below into Fetch to test Push against it."
            if ok
            else (failed.message if failed else "Seeding the test studies failed.")
        )
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )

    def _lookup(
        self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]
    ) -> ToolResult:
        accession_number = str(options.get("accession_number") or "").strip()
        study_date = normalize_da(str(options.get("study_date") or "").strip())
        if not accession_number or not study_date:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Accession Number and Study Date are both required to find a study.",
                remote_id=remote.id, remote_name=remote.name,
            )

        started = time.perf_counter()
        with capture_pynetdicom_log() as log_stream:
            find_started = time.perf_counter()
            records, error, contexts = _lookup_study(local, remote, accession_number, study_date)
            log = log_stream.getvalue().strip()

        ok = bool(records) and not error
        if ok:
            message = f"Found {len(records)} study" + ("" if len(records) == 1 else "ies")
            if len(records) == 1:
                message += f" — Study Instance UID {records[0]['StudyInstanceUID']}"
        else:
            message = error or "No study matched that Accession Number and Study Date."
        steps = [
            ToolStep(
                name="C-FIND",
                ok=ok,
                message=message,
                duration_ms=elapsed_ms(find_started),
            )
        ]
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=message,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )

    def _fetch(
        self, local: LocalAE, remote: RemoteNode, study_uid: str, series_uid: str, dest_ae: str
    ) -> ToolResult:
        started = time.perf_counter()
        steps: list[ToolStep] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, error, contexts = _retrieve(local, remote, study_uid, series_uid, dest_ae)
            datasets = datasets[:MAX_INSTANCES]
            if error and not datasets:
                steps.append(
                    ToolStep(name="C-MOVE", ok=False, message=error, duration_ms=elapsed_ms(move_started))
                )
            else:
                message = f"Retrieved {len(datasets)} instance(s) from {study_uid} to {dest_ae}"
                if error:
                    message += f". Last error: {error}"
                steps.append(
                    ToolStep(
                        name="C-MOVE",
                        ok=bool(datasets),
                        message=message if datasets else (error or "No instances were stored on this AE."),
                        duration_ms=elapsed_ms(move_started),
                    )
                )
            log = log_stream.getvalue().strip()

        records = [_instance_row(ds) for ds in datasets]
        ok = bool(steps) and all(step.ok for step in steps)
        failed = next((step for step in steps if not step.ok), None)
        summary = (
            f"Fetched {len(records)} instance(s); showing current tag values below."
            if ok
            else (failed.message if failed else "Fetch failed")
        )
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )

    def _push(
        self,
        local: LocalAE,
        remote: RemoteNode,
        study_uid: str,
        series_uid: str,
        dest_ae: str,
        options: dict[str, Any],
    ) -> ToolResult:
        new_values: dict[str, str] = {}
        raw_timestamp = str(options.get("final_sign_timestamp") or "").strip()
        raw_composed_by = str(options.get("last_composed_by") or "").strip()
        if raw_timestamp:
            try:
                new_values["final_sign_timestamp"] = to_dicom_dt(raw_timestamp)
            except ValueError as exc:
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False, summary=str(exc),
                    remote_id=remote.id, remote_name=remote.name,
                )
        if raw_composed_by:
            if len(raw_composed_by) > 64:
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary="Study Last Composed By is at most 64 characters (VR LO).",
                    remote_id=remote.id, remote_name=remote.name,
                )
            new_values["last_composed_by"] = raw_composed_by
        if not new_values:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Enter at least one new value before pushing.",
                remote_id=remote.id, remote_name=remote.name,
            )

        started = time.perf_counter()
        steps: list[ToolStep] = []
        records: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, move_error, move_contexts = _retrieve(
                local, remote, study_uid, series_uid, dest_ae
            )
            datasets = datasets[:MAX_INSTANCES]
            contexts = move_contexts
            if not datasets:
                steps.append(
                    ToolStep(
                        name="C-MOVE",
                        ok=False,
                        message=move_error or "No instances were stored on this AE.",
                        duration_ms=elapsed_ms(move_started),
                    )
                )
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=move_error or "Nothing retrieved; nothing pushed.",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            steps.append(
                ToolStep(
                    name="C-MOVE",
                    ok=True,
                    message=f"Retrieved {len(datasets)} instance(s) from {study_uid} to {dest_ae}",
                    duration_ms=elapsed_ms(move_started),
                )
            )

            edited: list[Dataset] = []
            for dataset in datasets:
                before = _instance_row(dataset)
                applied = {
                    key: _set_tag(dataset, EDITABLE_TAGS[key]["tag"], EDITABLE_TAGS[key]["vr"], value)
                    for key, value in new_values.items()
                }
                if any(applied.values()):
                    edited.append(dataset)
                records.append(
                    {
                        **before,
                        "edited": ", ".join(
                            EDITABLE_TAGS[key]["label"] for key, ok in applied.items() if ok
                        ),
                        "skipped": ", ".join(
                            f"{EDITABLE_TAGS[key]['label']} (tag not present)"
                            for key, ok in applied.items()
                            if not ok
                        ),
                        "store": "pending",
                    }
                )

            skipped_all = len(edited) == 0
            if skipped_all:
                steps.append(
                    ToolStep(
                        name="Edit",
                        ok=False,
                        message=(
                            "None of the retrieved instances have the tag(s) being set. "
                            "Nothing to push — this tool only corrects an existing value."
                        ),
                    )
                )
            else:
                sop_classes = sorted({str(ds.SOPClassUID) for ds in edited})
                store_started = time.perf_counter()
                stored = 0
                failed = 0
                assoc = None
                try:
                    _ae, assoc = associate(local, remote, sop_classes)
                    store_contexts = context_rows(assoc)
                    if store_contexts:
                        contexts = store_contexts
                    if not getattr(assoc, "is_established", False):
                        message = reject_reason(assoc)
                        for record in records:
                            if record["store"] == "pending":
                                record["store"] = "not stored (association failed)"
                        steps.append(ToolStep(name="C-STORE", ok=False, message=message))
                    else:
                        by_uid = {str(ds.SOPInstanceUID): ds for ds in edited}
                        for record in records:
                            dataset = by_uid.get(record["sop_instance_uid"])
                            if dataset is None:
                                continue
                            status = assoc.send_c_store(dataset)
                            if status and int(status.Status) == 0x0000:
                                stored += 1
                                record["store"] = "stored"
                            else:
                                failed += 1
                                code = int(status.Status) if status else None
                                record["store"] = (
                                    f"failed (0x{code:04X})" if code is not None else "failed (no response)"
                                )
                        steps.append(
                            ToolStep(
                                name="C-STORE",
                                ok=failed == 0,
                                message=f"Stored {stored} of {len(edited)} instance(s)"
                                + (f", {failed} failed" if failed else ""),
                                duration_ms=elapsed_ms(store_started),
                                details={"stored": stored, "failed": failed},
                            )
                        )
                finally:
                    if assoc is not None and getattr(assoc, "is_established", False):
                        try:
                            assoc.release()
                        except Exception:  # noqa: BLE001
                            try:
                                assoc.abort()
                            except Exception:  # noqa: BLE001
                                pass
            log = log_stream.getvalue().strip()

        ok = bool(steps) and all(step.ok for step in steps)
        failed_step = next((step for step in steps if not step.ok), None)
        stored_count = sum(1 for r in records if r.get("store") == "stored")
        summary = (
            f"Pushed {', '.join(EDITABLE_TAGS[k]['label'] for k in new_values)} to "
            f"{stored_count} of {len(records)} instance(s) on {remote.name}"
            if ok
            else (failed_step.message if failed_step else "Push failed")
        )
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )


register(TagEditorTool())
