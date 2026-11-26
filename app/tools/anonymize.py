"""Query a PACS, pick studies (and a level), anonymize them, and export.

Two actions, staged like Tag Editor's lookup -> fetch -> push:

  "query"  Study-level C-FIND on Patient ID / Accession Number / Study Date /
           Modality (Study Date required, same convention as every other
           Study-level query in this app). Returns a checkable study list.
  "run"    Given the checked studies and a level (Study / Series / Image):
           at Study level, C-MOVEs exactly those studies. At Series or Image
           level, first walks down from each checked study to every series
           (and, for Image, every image in every series) with an internal
           C-FIND, then C-MOVEs all of those — v1 has no per-series/per-image
           picker yet, so "Series" or "Image" means "every series/image in
           the studies I checked", not a further hand-picked subset.
           Anonymizes every retrieved instance with AnonBatch and writes the
           result to a chosen folder, as loose files or zipped.

Every output filename and directory is built from the *anonymized* UIDs
(never the original ones), so a filename can never leak PHI regardless of
which mode was used or whether the batch's UID map even changed anything.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from pydicom.dataset import Dataset
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.dicom_client import associate, capture_pynetdicom_log, context_rows, reject_reason, rejected_sop_message
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.mwl_scp import STORAGE_INBOX
from app.tools.anon_engine import MODE_LABELS, MODES, AnonBatch
from app.tools.base import BaseTool, elapsed_ms
from app.tools.find_advanced import retrieve_storage_gate_message
from app.tools.find_keys import build_identifier, normalize_da, record_from_dataset, study_filter_values
from app.tools.registry import register

PENDING = {0xFF00, 0xFF01}
MAX_QUERY_RESULTS = 500
MAX_BROWSE_RESULTS = 2000
MAX_ENTITIES_PER_RUN = 200

QUERY_COLUMNS = [
    "StudyInstanceUID",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "ModalitiesInStudy",
]
SERIES_COLUMNS = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "Modality",
    "SeriesNumber",
    "SeriesDescription",
    "NumberOfSeriesRelatedInstances",
]
IMAGE_COLUMNS = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "InstanceNumber",
]

LEVELS = ("STUDY", "SERIES", "IMAGE")


def _parse_json_list(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _study_query(local: LocalAE, remote: RemoteNode, values: dict[str, str]):
    identifier = build_identifier("STUDY", values, QUERY_COLUMNS)
    contexts: list[dict[str, Any]] = []
    assoc = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
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
                if len(records) < MAX_QUERY_RESULTS:
                    records.append(record_from_dataset(identifier_ds, QUERY_COLUMNS))
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


def _child_query(
    local: LocalAE,
    remote: RemoteNode,
    assoc,
    level: str,
    values: dict[str, str],
    columns: list[str],
    cap: int,
) -> tuple[list[dict[str, str]], str | None]:
    identifier = build_identifier(level, values, columns)
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
            if len(records) < cap:
                records.append(record_from_dataset(identifier_ds, columns))
        elif code == 0x0000:
            continue
        else:
            error = f"C-FIND status 0x{code:04X}"
            break
    return records, error


def _browse(
    local: LocalAE, remote: RemoteNode, study_uids: list[str], level: str
) -> tuple[list[dict[str, str]], str | None, list[dict[str, Any]]]:
    """Walk down from each selected study to Series (level=SERIES) or all the
    way to every image in every series (level=IMAGE). One association, reused
    for every child query, since PACS commonly cap moves/finds per association
    but rarely mind several sequential FINDs.
    """
    contexts: list[dict[str, Any]] = []
    assoc = None
    rows: list[dict[str, str]] = []
    error: str | None = None
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
        contexts = context_rows(assoc)
        rejected = rejected_sop_message(contexts, "Study Root Query/Retrieve FIND")
        if not getattr(assoc, "is_established", False):
            return [], rejected or reject_reason(assoc), contexts
        if not assoc.accepted_contexts:
            assoc.release()
            return [], rejected or "Study Root Query/Retrieve FIND was not accepted.", contexts
        for study_uid in study_uids:
            series_rows, series_error = _child_query(
                local, remote, assoc, "SERIES", {"StudyInstanceUID": study_uid}, SERIES_COLUMNS, MAX_BROWSE_RESULTS
            )
            if series_error and not series_rows:
                error = series_error
                continue
            if level == "SERIES":
                rows.extend(series_rows)
                continue
            for series_row in series_rows:
                series_uid = series_row.get("SeriesInstanceUID", "")
                image_rows, image_error = _child_query(
                    local,
                    remote,
                    assoc,
                    "IMAGE",
                    {"StudyInstanceUID": study_uid, "SeriesInstanceUID": series_uid},
                    IMAGE_COLUMNS,
                    MAX_BROWSE_RESULTS,
                )
                if image_error and not image_rows:
                    error = image_error
                    continue
                rows.extend(image_rows)
            if len(rows) >= MAX_BROWSE_RESULTS:
                break
        assoc.release()
        return rows[:MAX_BROWSE_RESULTS], (error if not rows else None), contexts
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass


def list_series(local: LocalAE, remote: RemoteNode, study_uid: str):
    """Every series of one study, for the series picker under a study row."""
    return _browse(local, remote, [study_uid], "SERIES")


def _move_identifier(entity: dict[str, Any]) -> Dataset:
    identifier = Dataset()
    study_uid = str(entity.get("study_uid") or entity.get("StudyInstanceUID") or "")
    series_uid = str(entity.get("series_uid") or entity.get("SeriesInstanceUID") or "")
    sop_uid = str(entity.get("sop_instance_uid") or entity.get("SOPInstanceUID") or "")
    identifier.StudyInstanceUID = study_uid
    if sop_uid:
        identifier.QueryRetrieveLevel = "IMAGE"
        identifier.SeriesInstanceUID = series_uid
        identifier.SOPInstanceUID = sop_uid
    elif series_uid:
        identifier.QueryRetrieveLevel = "SERIES"
        identifier.SeriesInstanceUID = series_uid
    else:
        identifier.QueryRetrieveLevel = "STUDY"
    return identifier


def _selection_label(entity: dict[str, Any]) -> str:
    """How a selection is named in messages: the picker's label (``2 · TLC Inspiratory``) or the UID tail."""
    label = str(entity.get("label") or "").strip()
    if label:
        return label
    sop = str(entity.get("sop_instance_uid") or entity.get("SOPInstanceUID") or "")
    series = str(entity.get("series_uid") or entity.get("SeriesInstanceUID") or "")
    if series and not sop:
        # A series row from the series query carries its number and description.
        number = str(entity.get("SeriesNumber") or "").strip()
        description = str(entity.get("SeriesDescription") or "").strip()
        if number or description:
            return f"series {number or '?'}" + (f" · {description}" if description else "")
    study = str(entity.get("study_uid") or entity.get("StudyInstanceUID") or "")
    kind, uid = ("image", sop) if sop else ("series", series) if series else ("study", study)
    return f"{kind} …{uid[-8:]}"


def _retrieve_selections(
    local: LocalAE, remote: RemoteNode, entities: list[dict[str, Any]], dest_ae: str
) -> tuple[list[Dataset], str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """One C-MOVE per selection, all captured together. Returns the datasets, the last error, the
    association contexts and one outcome per selection (``label``, ``count``, ``error``).

    A selection that gets nothing because the PACS closed the association (some PACS allow only a
    few moves per association) is retried once on a new association, and later selections carry on
    on the new one; before, everything after the first dropped association was silently lost."""
    STORAGE_INBOX.begin()
    contexts: list[dict[str, Any]] = []
    assoc = None
    error: str | None = None
    outcomes: list[dict[str, Any]] = []

    def open_association():
        nonlocal contexts
        _ae, new_assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelMove])
        rows = context_rows(new_assoc)
        if not contexts:
            contexts = rows
        rejected = rejected_sop_message(
            rows,
            "Study Root Query/Retrieve MOVE",
            "The remote must accept C-MOVE (not C-GET) and know this AE as a destination.",
        )
        if not getattr(new_assoc, "is_established", False):
            return None, rejected or reject_reason(new_assoc)
        if not new_assoc.accepted_contexts:
            new_assoc.release()
            return None, rejected or "Study Root Query/Retrieve MOVE was not accepted."
        return new_assoc, None

    def close(current) -> None:
        if current is not None and getattr(current, "is_established", False):
            try:
                current.release()
            except Exception:  # noqa: BLE001
                try:
                    current.abort()
                except Exception:  # noqa: BLE001
                    pass

    try:
        for entity in entities:
            before = STORAGE_INBOX.count()
            note = ""
            for attempt in (1, 2):
                if assoc is None or not getattr(assoc, "is_established", False):
                    assoc, open_error = open_association()
                    if assoc is None:
                        if not outcomes and attempt == 1 and not STORAGE_INBOX.count():
                            # Could not connect at all for the first selection: report why and stop.
                            return [], open_error, contexts, []
                        note = open_error or "Could not connect to the PACS."
                        break
                identifier = _move_identifier(entity)
                note = ""
                lost = False
                try:
                    for status, _remaining in assoc.send_c_move(
                        identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove
                    ):
                        if not status:
                            note = "No C-MOVE response (timeout, abort, or invalid PDU)."
                            lost = True
                            break
                        code = int(status.Status)
                        if code in PENDING:
                            continue
                        if code != 0x0000:
                            note = f"C-MOVE status 0x{code:04X}"
                        break
                except Exception as exc:  # noqa: BLE001 — a dropped association must not crash the request
                    note = f"{type(exc).__name__}: {exc}"
                    lost = True
                got = STORAGE_INBOX.count() - before
                # No answer at all means the association is gone, even when it has not noticed yet.
                dropped = lost or not getattr(assoc, "is_established", False)
                if dropped:
                    if lost:
                        try:
                            assoc.abort()
                        except Exception:  # noqa: BLE001
                            pass
                    assoc = None
                if got == 0 and dropped and attempt == 1:
                    continue  # the PACS closed the association: try this selection once on a new one
                break
            got = STORAGE_INBOX.count() - before
            if note:
                error = f"{_selection_label(entity)}: {note}"
            outcomes.append({"label": _selection_label(entity), "count": got, "error": note})
        close(assoc)
        time.sleep(0.05)
        return STORAGE_INBOX.finish(), error, contexts, outcomes
    finally:
        if assoc is not None and getattr(assoc, "is_established", False):
            try:
                assoc.abort()
            except Exception:  # noqa: BLE001
                pass
        STORAGE_INBOX.finish()


def _retrieve_many(
    local: LocalAE, remote: RemoteNode, entities: list[dict[str, Any]], dest_ae: str
) -> tuple[list[Dataset], str | None, list[dict[str, Any]]]:
    datasets, error, contexts, _outcomes = _retrieve_selections(local, remote, entities, dest_ae)
    return datasets, error, contexts


def _safe_uid_component(value: str) -> str:
    return "".join(ch if (ch.isalnum() or ch == ".") else "_" for ch in str(value)) or "unknown"


LAYOUTS = ("series", "flat")


_FOLDER_BAD = set('<>:"/\\|?*')


def _folder_text(value: str, limit: int = 80) -> str:
    """Text that is safe as a folder name on Windows, macOS and Linux (and keeps spaces and commas)."""
    cleaned = "".join("_" if (ch in _FOLDER_BAD or ord(ch) < 32) else ch for ch in str(value or ""))
    return " ".join(cleaned.split()).strip(" .")[:limit].strip(" .")


def _series_folder_names(
    datasets: list[Dataset], descriptions: dict[tuple[str, str], str] | None = None
) -> dict[tuple[str, str], str]:
    """A readable folder name per (study, series).

    By default ``series-3-CT``: the series number and modality, neither of which identifies a
    patient. With ``descriptions`` (original Series Descriptions, chosen by the operator) the
    description is the name. Either way a missing name, or two series that would share one,
    get the number or the tail of their (anonymized) Series UID added."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for dataset in datasets:
        key = (str(getattr(dataset, "StudyInstanceUID", "study")), str(getattr(dataset, "SeriesInstanceUID", "series")))
        if key not in found:
            number = getattr(dataset, "SeriesNumber", None)
            found[key] = ("" if number in (None, "") else str(number), str(getattr(dataset, "Modality", "") or ""))
    bases: dict[tuple[str, str], str] = {}
    for key, (number, modality) in found.items():
        text = _folder_text((descriptions or {}).get(key, ""))
        bases[key] = text or ("series" + (f"-{number}" if number else "") + (f"-{_safe_uid_component(modality)}" if modality else ""))
    counts: dict[tuple[str, str], int] = {}
    for key, base in bases.items():
        counts[(key[0], base.lower())] = counts.get((key[0], base.lower()), 0) + 1
    names: dict[tuple[str, str], str] = {}
    for key, base in bases.items():
        number = found[key][0]
        described = bool(_folder_text((descriptions or {}).get(key, "")))
        if counts[(key[0], base.lower())] > 1 or (not described and not number):
            base = f"{base} - {number}" if (described and number) else f"{base}-{_safe_uid_component(key[1])[-6:]}"
        names[key] = base
    # Two that still collide (the same description and number twice): tell them apart by the UID tail.
    seen: dict[tuple[str, str], int] = {}
    for key, name in names.items():
        seen[(key[0], name.lower())] = seen.get((key[0], name.lower()), 0) + 1
    for key, name in list(names.items()):
        if seen[(key[0], name.lower())] > 1:
            names[key] = f"{name}-{_safe_uid_component(key[1])[-6:]}"
    return names


def _study_folder_names(datasets: list[Dataset], descriptions: dict[str, str] | None = None) -> dict[str, str]:
    """A folder name per study: its (anonymized) Study UID, or with ``descriptions`` (original Study
    Descriptions, chosen by the operator) the description. A study with no usable description keeps
    its UID, and two studies that would share a name get the tail of their UID added."""
    uids: list[str] = []
    for dataset in datasets:
        uid = str(getattr(dataset, "StudyInstanceUID", "study"))
        if uid not in uids:
            uids.append(uid)
    bases = {uid: _folder_text((descriptions or {}).get(uid, "")) or _safe_uid_component(uid) for uid in uids}
    counts: dict[str, int] = {}
    for base in bases.values():
        counts[base.lower()] = counts.get(base.lower(), 0) + 1
    return {
        uid: (f"{base}-{_safe_uid_component(uid)[-6:]}" if counts[base.lower()] > 1 and base != _safe_uid_component(uid) else base)
        for uid, base in bases.items()
    }


def _write_anonymized(
    datasets: list[Dataset],
    staging: Path,
    layout: str = "series",
    descriptions: dict[tuple[str, str], str] | None = None,
    study_descriptions: dict[str, str] | None = None,
) -> list[Path]:
    """Write the files. ``series``: one folder per study, a folder per series inside it.
    ``flat``: every file directly in the export folder. Names come from anonymized UIDs only."""
    written: list[Path] = []
    names = _series_folder_names(datasets, descriptions) if layout != "flat" else {}
    study_names = _study_folder_names(datasets, study_descriptions) if layout != "flat" else {}
    used: set[Path] = set()
    for dataset in datasets:
        study_uid = str(getattr(dataset, "StudyInstanceUID", "study"))
        series_uid = str(getattr(dataset, "SeriesInstanceUID", "series"))
        if layout == "flat":
            folder = staging
        else:
            folder = staging / study_names[study_uid] / names[(study_uid, series_uid)]
        folder.mkdir(parents=True, exist_ok=True)
        sop_uid = _safe_uid_component(getattr(dataset, "SOPInstanceUID", f"instance-{len(written)}"))
        path = folder / f"{sop_uid}.dcm"
        counter = 2
        while path in used:
            path = folder / f"{sop_uid}-{counter}.dcm"
            counter += 1
        used.add(path)
        dataset.save_as(path, enforce_file_format=True)
        written.append(path)
    return written


def _archive(staging: Path, output_dir: Path, archive: str) -> Path:
    if archive == "zip":
        stamp = time.strftime("%Y%m%d-%H%M%S")
        zip_path = output_dir / f"anonymized-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging))
        return zip_path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final_dir = output_dir / f"anonymized-{stamp}"
    shutil.move(str(staging), str(final_dir))
    return final_dir


class AnonymizeTool(BaseTool):
    id = "anonymize"
    name = "Dicom Anonymizer"
    description = (
        "Query a PACS by Patient ID, Accession Number, Study Date, and Modality, pick "
        "studies, series, or individual images, anonymize them (nuke, fuzz, remove "
        "patient info, or a custom per-tag list), and export the result as loose "
        "DICOM files or a ZIP archive."
    )
    category = "dimse"
    template = "anonymize.html"

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
        action = str(options.get("action") or "query").strip()
        if action == "run":
            return self._run(local, remote, options)
        return self._query(local, remote, options)

    def _query(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        study_date = normalize_da(str(options.get("study_date") or "").strip())
        if not study_date:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Study Date is required.",
                remote_id=remote.id, remote_name=remote.name,
            )
        values: dict[str, str] = {"StudyDate": study_date}
        patient_id = str(options.get("patient_id") or "").strip()
        accession_number = str(options.get("accession_number") or "").strip()
        modality = str(options.get("modality") or "").strip()
        if patient_id:
            values["PatientID"] = patient_id
        if accession_number:
            values["AccessionNumber"] = accession_number
        if modality:
            values["ModalitiesInStudy"] = modality
        try:
            values.update(study_filter_values(options))
        except ValueError as exc:
            return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary=str(exc), remote_id=remote.id, remote_name=remote.name)

        started = time.perf_counter()
        with capture_pynetdicom_log() as log_stream:
            find_started = time.perf_counter()
            records, error, contexts = _study_query(local, remote, values)
            log = log_stream.getvalue().strip()

        ok = not error
        if ok:
            message = f"Found {len(records)} stud" + ("y" if len(records) == 1 else "ies")
        else:
            message = error or "Query failed."
        steps = [ToolStep(name="C-FIND", ok=ok, message=message, duration_ms=elapsed_ms(find_started))]
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=message,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )

    def _run(self, local: LocalAE, remote: RemoteNode, options: dict[str, Any]) -> ToolResult:
        level = str(options.get("level") or "STUDY").strip().upper()
        if level not in LEVELS:
            level = "STUDY"
        entities = _parse_json_list(options.get("entities_json") or options.get("entities"))
        if not entities:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Select at least one study first.",
                remote_id=remote.id, remote_name=remote.name,
            )

        browse_step: ToolStep | None = None
        # Series the operator ticked under a study are retrieved as they are; the other
        # studies are walked down to their series or images when the level asks for it.
        picked_series = [item for item in entities if item.get("series_uid")]
        entities = [item for item in entities if not item.get("series_uid")]
        if level != "STUDY" and entities:
            study_uids = [str(item.get("study_uid") or "") for item in entities]
            study_uids = [uid for uid in study_uids if uid]
            browse_started = time.perf_counter()
            with capture_pynetdicom_log():
                rows, browse_error, _contexts = _browse(local, remote, study_uids, level)
            noun = "series" if level == "SERIES" else "image"
            if not rows:
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=browse_error or f"No {noun}s found for the selected studies.",
                    remote_id=remote.id, remote_name=remote.name,
                    steps=[ToolStep(name=f"Find {noun}s", ok=False, message=browse_error or f"No {noun}s found.")],
                )
            entities = rows
            browse_step = ToolStep(
                name=f"Find {noun}s",
                ok=True,
                message=f"Found {len(rows)} {noun}" + ("" if len(rows) == 1 else "s")
                + (f" (last error: {browse_error})" if browse_error else "")
                + " across the selected studies.",
                duration_ms=elapsed_ms(browse_started),
            )
        entities = (picked_series + entities)[:MAX_ENTITIES_PER_RUN]

        mode = str(options.get("mode") or "").strip()
        if mode not in MODES:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Choose an anonymization mode first.",
                remote_id=remote.id, remote_name=remote.name,
            )
        remove_patient_erase = str(options.get("remove_patient_action") or "erase") != "fuzz"
        custom_actions_raw = options.get("custom_actions_json") or options.get("custom_actions")
        custom_actions: dict[str, dict[str, str]] = {}
        if mode == "custom":
            if isinstance(custom_actions_raw, str):
                try:
                    custom_actions = json.loads(custom_actions_raw) or {}
                except json.JSONDecodeError:
                    custom_actions = {}
            elif isinstance(custom_actions_raw, dict):
                custom_actions = custom_actions_raw

        output_dir_raw = str(options.get("output_dir") or "").strip()
        if not output_dir_raw:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary="Choose an output folder first.",
                remote_id=remote.id, remote_name=remote.name,
            )
        output_dir = Path(output_dir_raw).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False,
                summary=f"Could not use output folder {output_dir}: {exc}",
                remote_id=remote.id, remote_name=remote.name,
            )
        archive = str(options.get("archive") or "none").strip()
        if archive not in ("none", "zip"):
            archive = "none"
        layout = str(options.get("layout") or "series").strip()
        if layout not in LAYOUTS:
            layout = "series"
        by_description = str(options.get("series_names") or "number").strip() == "description"
        study_by_description = str(options.get("study_names") or "uid").strip() == "description"

        dest_ae = str(options.get("_listen_ae") or local.ae_title).strip() or local.ae_title
        storage_enabled = bool(
            options.get("_storage_enabled", getattr(local, "storage_scp_enabled", False))
        )
        storage_running = bool(options.get("_storage_running", False))
        storage_error = str(options.get("_storage_error") or "").strip()
        blocked = retrieve_storage_gate_message(
            dest_ae=dest_ae, port=int(local.port), calling_ae=local.ae_title,
            enabled=storage_enabled, running=storage_running, storage_error=storage_error,
        )
        if blocked:
            return ToolResult(
                tool_id=self.id, tool_name=self.name, ok=False, summary=blocked,
                remote_id=remote.id, remote_name=remote.name,
            )

        started = time.perf_counter()
        steps: list[ToolStep] = [browse_step] if browse_step else []
        records: list[dict[str, Any]] = []
        with capture_pynetdicom_log() as log_stream:
            move_started = time.perf_counter()
            datasets, move_error, contexts, outcomes = _retrieve_selections(local, remote, entities, dest_ae)
            if not datasets:
                steps.append(
                    ToolStep(
                        name="C-MOVE", ok=False,
                        message=move_error or "No instances were stored on this AE.",
                        duration_ms=elapsed_ms(move_started),
                    )
                )
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=move_error or "Nothing retrieved; nothing anonymized.",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            empty = [item for item in outcomes if not item["count"]]
            table = "\n".join(
                f"{item['label']}: {item['count']} instance(s)" + (f" ({item['error']})" if item["error"] else "")
                for item in outcomes
            )
            steps.append(
                ToolStep(
                    name="C-MOVE", ok=not empty,
                    message=f"Retrieved {len(datasets)} instance(s) from {len(entities) - len(empty)} of {len(entities)} selection(s)"
                    + (f". Nothing came back for: {'; '.join(item['label'] + (' (' + item['error'] + ')' if item['error'] else '') for item in empty)}" if empty else "")
                    + (f". Last error: {move_error}" if move_error and not empty else ""),
                    duration_ms=elapsed_ms(move_started),
                    details={"output": table},
                )
            )

            anon_started = time.perf_counter()
            batch = AnonBatch()
            anonymized: list[Dataset] = []
            failed = 0
            last_error = ""
            descriptions: dict[tuple[str, str], str] = {}
            study_descriptions: dict[str, str] = {}
            for dataset in datasets:
                try:
                    result_ds = batch.anonymize(
                        dataset, mode,
                        remove_patient_erase=remove_patient_erase,
                        custom_actions=custom_actions,
                    )
                    anonymized.append(result_ds)
                    if study_by_description:
                        study_descriptions.setdefault(
                            str(getattr(result_ds, "StudyInstanceUID", "")), str(getattr(dataset, "StudyDescription", "") or "")
                        )
                    if by_description:
                        # The description comes from the original series (the anonymized copy may have lost it).
                        descriptions.setdefault(
                            (str(getattr(result_ds, "StudyInstanceUID", "")), str(getattr(result_ds, "SeriesInstanceUID", ""))),
                            str(getattr(dataset, "SeriesDescription", "") or ""),
                        )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    last_error = str(exc)
            steps.append(
                ToolStep(
                    name="Anonymize",
                    ok=bool(anonymized) and not failed,
                    message=f"Anonymized {len(anonymized)} of {len(datasets)} instance(s) ({MODE_LABELS.get(mode, mode)})"
                    + (f" — {failed} failed: {last_error}" if failed else ""),
                    duration_ms=elapsed_ms(anon_started),
                )
            )

            write_started = time.perf_counter()
            staging = Path(tempfile.mkdtemp(prefix="dicomm-anon-"))
            try:
                written = _write_anonymized(
                    anonymized, staging, layout,
                    descriptions if by_description else None,
                    study_descriptions if study_by_description else None,
                )
                final_path = _archive(staging, output_dir, archive)
            except Exception as exc:  # noqa: BLE001
                shutil.rmtree(staging, ignore_errors=True)
                steps.append(ToolStep(name="Export", ok=False, message=f"Could not write output: {exc}"))
                log = log_stream.getvalue().strip()
                return ToolResult(
                    tool_id=self.id, tool_name=self.name, ok=False,
                    summary=f"Could not write output: {exc}",
                    remote_id=remote.id, remote_name=remote.name,
                    duration_ms=elapsed_ms(started), steps=steps, log=log, contexts=contexts,
                )
            steps.append(
                ToolStep(
                    name="Export", ok=True,
                    message=f"Wrote {len(written)} file(s) to {final_path}",
                    duration_ms=elapsed_ms(write_started),
                )
            )
            study_summary: dict[str, dict[str, Any]] = {}
            for dataset in anonymized:
                study_uid = str(getattr(dataset, "StudyInstanceUID", ""))
                series_uid = str(getattr(dataset, "SeriesInstanceUID", ""))
                entry = study_summary.setdefault(
                    study_uid, {"study_instance_uid": study_uid, "_series": set(), "instances": 0}
                )
                entry["_series"].add(series_uid)
                entry["instances"] += 1
            records = [
                {
                    "study_instance_uid": entry["study_instance_uid"],
                    "series": len(entry["_series"]),
                    "instances": entry["instances"],
                    "status": "written",
                }
                for entry in study_summary.values()
            ]
            log = log_stream.getvalue().strip()

        ok = bool(anonymized) and bool(steps) and all(step.ok for step in steps)
        if ok:
            summary = f"Anonymized and exported {len(anonymized)} instance(s) to {final_path}"
        elif anonymized:
            problems = [step.message for step in steps if not step.ok and step.name != "Export"]
            summary = f"Exported {len(anonymized)} instance(s) to {final_path}, but not everything: " + " | ".join(problems)
        else:
            summary = next((s.message for s in steps if not s.ok), "Anonymize run failed")
        return ToolResult(
            tool_id=self.id, tool_name=self.name, ok=ok, summary=summary,
            remote_id=remote.id, remote_name=remote.name,
            duration_ms=elapsed_ms(started), steps=steps, log=log,
            contexts=contexts, records=records,
        )


register(AnonymizeTool())
