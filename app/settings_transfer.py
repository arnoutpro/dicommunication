"""Export and import of the app's settings, to move them between computers.

One JSON file holds the parts you choose: remote nodes, virtual AEs, this
workstation's local AE, saved Analytics queries, and Dicom Router rules.
Never included: results, the worklist, router run history, HL7 drafts and
logs, because they can contain patient data. The file lists PACS hosts and AE
titles but no passwords (there are none), so it is internal, not secret.

Import is two steps. ``plan_import`` only reads: it says what would be added,
updated or left alone. ``apply_import`` then writes, and puts everything back
if anything fails halfway. It never deletes. A record is matched by id, then by
name, so importing the same file twice makes no duplicates, and links between
records (a rule pointing at its PACS) are rewritten to the ids used here.

No web code, so it is tested with plain stores and files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from app import __version__
from app.models import LocalAE, RemoteNode, RouteRule, SavedQuery, VirtualAE
from app.store import ConfigStore

FORMAT = "dicommunication-settings"
VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 2000

SECTION_REMOTES = "remotes"
SECTION_IDENTITIES = "identities"
SECTION_LOCAL = "local"
SECTION_QUERIES = "queries"
SECTION_RULES = "rules"
SECTIONS = (SECTION_REMOTES, SECTION_IDENTITIES, SECTION_LOCAL, SECTION_QUERIES, SECTION_RULES)
SECTION_LABELS = {
    SECTION_REMOTES: "Remote nodes",
    SECTION_IDENTITIES: "Virtual AEs",
    SECTION_LOCAL: "Local AE settings (this workstation)",
    SECTION_QUERIES: "Saved Analytics queries",
    SECTION_RULES: "Dicom Router rules",
}
# What the export and the import start with ticked. The local AE is specific to
# one machine; saved queries may hold patient identifiers typed as filters.
DEFAULT_EXPORT = (SECTION_REMOTES, SECTION_IDENTITIES, SECTION_RULES)
DEFAULT_IMPORT = (SECTION_REMOTES, SECTION_IDENTITIES, SECTION_QUERIES, SECTION_RULES)

# A rule's run bookkeeping is not part of its settings.
_RULE_STATE_FIELDS = ("status", "seen_study_uids", "pdf_sent_keys", "last_run_at", "next_run_at", "last_run_ok", "last_error", "created_at", "updated_at")

ADD, UPDATE, SAME = "add", "update", "same"


class SettingsError(Exception):
    """A problem the user can read and fix (not a settings file, too new, too big)."""


# --- export --------------------------------------------------------------------


def export_settings(store: ConfigStore, sections: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """The settings file's content for the chosen sections."""
    chosen = [section for section in SECTIONS if section in sections]
    config = store.load()
    body: dict[str, Any] = {}
    if SECTION_REMOTES in chosen:
        body[SECTION_REMOTES] = [item.model_dump(mode="json", exclude={"created_at"}) for item in config.remotes]
    if SECTION_IDENTITIES in chosen:
        body[SECTION_IDENTITIES] = [item.model_dump(mode="json") for item in config.identities]
    if SECTION_LOCAL in chosen:
        body[SECTION_LOCAL] = config.local.model_dump(mode="json", exclude={"listener_paused"})  # runtime state, not a setting
    if SECTION_QUERIES in chosen:
        body[SECTION_QUERIES] = [item.model_dump(mode="json", exclude={"created_at", "updated_at"}) for item in store.list_saved_queries()]
    if SECTION_RULES in chosen:
        body[SECTION_RULES] = [item.model_dump(mode="json", exclude=set(_RULE_STATE_FIELDS)) for item in store.list_route_rules()]
    return {
        "format": FORMAT,
        "version": VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections": body,
    }


def export_bytes(store: ConfigStore, sections: list[str] | tuple[str, ...]) -> bytes:
    return (json.dumps(export_settings(store, sections), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def export_filename(now: datetime | None = None) -> str:
    return f"dicommunication-settings-{(now or datetime.now()).strftime('%Y-%m-%d')}.json"


def section_counts(store: ConfigStore) -> dict[str, int]:
    """How many records each section would hold, for the export page."""
    config = store.load()
    return {
        SECTION_REMOTES: len(config.remotes),
        SECTION_IDENTITIES: len(config.identities),
        SECTION_LOCAL: 1,
        SECTION_QUERIES: len(store.list_saved_queries()),
        SECTION_RULES: len(store.list_route_rules()),
    }


# --- reading a file ------------------------------------------------------------


@dataclass
class ParsedSettings:
    app_version: str = ""
    exported_at: str = ""
    remotes: list[RemoteNode] = field(default_factory=list)
    identities: list[VirtualAE] = field(default_factory=list)
    local: LocalAE | None = None
    queries: list[SavedQuery] = field(default_factory=list)
    rules: list[RouteRule] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # records that could not be read; they are left out

    def present(self) -> list[str]:
        have = {
            SECTION_REMOTES: bool(self.remotes),
            SECTION_IDENTITIES: bool(self.identities),
            SECTION_LOCAL: self.local is not None,
            SECTION_QUERIES: bool(self.queries),
            SECTION_RULES: bool(self.rules),
        }
        return [section for section in SECTIONS if have[section]]


def parse_settings(raw: bytes | str) -> ParsedSettings:
    """Read and validate a settings file. SettingsError says what is wrong with it."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_BYTES:
        raise SettingsError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB, so it is not a settings file from this app.")
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise SettingsError("That is not a settings file: it is not readable JSON.") from None
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise SettingsError("That is not a Dicommunication settings file.")
    version = document.get("version")
    if not isinstance(version, int) or version < 1:
        raise SettingsError("The settings file has no valid version.")
    if version > VERSION:
        raise SettingsError(
            f"This settings file is version {version}, made by a newer Dicommunication"
            f"{' (' + str(document['app_version']) + ')' if document.get('app_version') else ''}. Update this app to read it."
        )
    sections = document.get("sections")
    if not isinstance(sections, dict):
        raise SettingsError("The settings file has no sections.")
    parsed = ParsedSettings(app_version=str(document.get("app_version") or ""), exported_at=str(document.get("exported_at") or ""))
    _read_list(sections, SECTION_REMOTES, RemoteNode, parsed.remotes, parsed.problems, "remote node")
    _read_list(sections, SECTION_IDENTITIES, VirtualAE, parsed.identities, parsed.problems, "virtual AE")
    _read_list(sections, SECTION_QUERIES, SavedQuery, parsed.queries, parsed.problems, "saved query")
    _read_list(sections, SECTION_RULES, RouteRule, parsed.rules, parsed.problems, "router rule")
    local = sections.get(SECTION_LOCAL)
    if local is not None:
        try:
            parsed.local = LocalAE.model_validate(local)
        except (ValidationError, ValueError) as exc:
            parsed.problems.append(f"The local AE settings could not be read: {_why(exc)}")
    return parsed


def _why(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error.get("loc", ()))
        return f"{where}: {str(error.get('msg', 'invalid')).removeprefix('Value error, ')}" if where else str(error.get("msg", "invalid"))
    return str(exc)


def _read_list(sections: dict, key: str, model: type, out: list, problems: list[str], noun: str) -> None:
    items = sections.get(key)
    if items is None:
        return
    if not isinstance(items, list):
        problems.append(f"The {noun}s section is not a list. It is left out.")
        return
    if len(items) > MAX_RECORDS:
        problems.append(f"The file has more than {MAX_RECORDS} {noun}s; only the first {MAX_RECORDS} are read.")
        items = items[:MAX_RECORDS]
    for index, item in enumerate(items, start=1):
        try:
            out.append(model.model_validate(item))
        except (ValidationError, ValueError, TypeError) as exc:
            label = item.get("name") if isinstance(item, dict) and item.get("name") else f"#{index}"
            problems.append(f"The {noun} {label} could not be read ({_why(exc)}) and is left out.")


# --- planning ------------------------------------------------------------------


@dataclass
class PlanItem:
    section: str
    name: str
    action: str  # ADD, UPDATE or SAME
    detail: str = ""


@dataclass
class ImportPlan:
    items: list[PlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # What apply_import writes, already resolved against the current store.
    _identities: list[tuple[str, VirtualAE]] = field(default_factory=list)
    _remotes: list[tuple[str, RemoteNode]] = field(default_factory=list)
    _local: LocalAE | None = None
    _queries: list[SavedQuery] = field(default_factory=list)
    _rules: list[tuple[str, RouteRule]] = field(default_factory=list)

    def count(self, section: str, action: str) -> int:
        return sum(1 for item in self.items if item.section == section and item.action == action)

    def changes(self) -> int:
        return sum(1 for item in self.items if item.action != SAME)

    def sections(self) -> list[str]:
        return [section for section in SECTIONS if any(item.section == section for item in self.items)]


def _same_payload(a: Any, b: Any, ignore: set[str]) -> bool:
    return a.model_dump(mode="json", exclude=ignore) == b.model_dump(mode="json", exclude=ignore)


def _match(existing: list, wanted: Any) -> Any | None:
    """The record already here that this one is: same id, else same name (any case)."""
    for item in existing:
        if item.id == wanted.id:
            return item
    name = wanted.name.casefold()
    for item in existing:
        if item.name.casefold() == name:
            return item
    return None


def _dedupe(records: list, noun: str, problems: list[str]) -> list:
    seen: set[str] = set()
    kept = []
    for record in records:
        key = record.name.casefold()
        if key in seen:
            problems.append(f"The file lists the {noun} {record.name} twice. Only the first is used.")
            continue
        seen.add(key)
        kept.append(record)
    return kept


def plan_import(store: ConfigStore, parsed: ParsedSettings, sections: list[str] | tuple[str, ...]) -> ImportPlan:
    """What importing the chosen sections would do. Reads the store, writes nothing."""
    plan = ImportPlan(problems=list(parsed.problems))
    config = store.load()
    chosen = set(sections)
    remote_ids: dict[str, str] = {}  # id in the file -> id here
    identity_ids: dict[str, str] = {}

    if SECTION_IDENTITIES in chosen:
        for identity in _dedupe(parsed.identities, "virtual AE", plan.problems):
            current = _match(config.identities, identity)
            target = current.id if current else identity.id
            identity_ids[identity.id] = target
            incoming = identity.model_copy(update={"id": target})
            action = ADD if current is None else SAME if _same_payload(incoming, current, set()) else UPDATE
            plan.items.append(PlanItem(SECTION_IDENTITIES, identity.name, action, f"{identity.ae_title}"))
            if action != SAME:
                plan._identities.append((action, incoming))
    if SECTION_REMOTES in chosen:
        for remote in _dedupe(parsed.remotes, "remote node", plan.problems):
            current = _match(config.remotes, remote)
            target = current.id if current else remote.id
            remote_ids[remote.id] = target
            incoming = remote.model_copy(update={"id": target})
            action = ADD if current is None else SAME if _same_payload(incoming, current, {"created_at"}) else UPDATE
            plan.items.append(PlanItem(SECTION_REMOTES, remote.name, action, f"{remote.ae_title} @ {remote.connect_host}:{remote.port}"))
            if action != SAME:
                plan._remotes.append((action, incoming))
    if SECTION_LOCAL in chosen and parsed.local is not None:
        same = parsed.local.model_dump(mode="json", exclude={"listener_paused"}) == config.local.model_dump(mode="json", exclude={"listener_paused"})
        plan.items.append(PlanItem(SECTION_LOCAL, parsed.local.ae_title, SAME if same else UPDATE, "replaces this workstation's AE settings" if not same else ""))
        if not same:
            plan._local = parsed.local

    known_remotes = {item.id for item in config.remotes} | set(remote_ids.values())
    known_identities = {item.id for item in config.identities} | set(identity_ids.values())

    def remote_ref(value: str, who: str) -> str:
        mapped = remote_ids.get(value, value)
        if value and mapped not in known_remotes:
            plan.warnings.append(f"{who} points at a remote node that does not exist on this computer. Choose another one in its settings.")
        return mapped

    if SECTION_QUERIES in chosen:
        existing_queries = store.list_saved_queries()
        for query in _dedupe(parsed.queries, "saved query", plan.problems):
            current = next((item for item in existing_queries if item.name.casefold() == query.name.casefold()), None)
            who = f"The saved query {query.name}"
            incoming = query.model_copy(
                update={
                    "remote_id": remote_ref(query.remote_id, who),
                    "identity_id": identity_ids.get(query.identity_id, query.identity_id),
                }
            )
            if incoming.identity_id and not incoming.identity_id.startswith("ae:") and incoming.identity_id not in known_identities:
                plan.warnings.append(f"{who} uses a virtual AE that does not exist on this computer; it will fall back to this workstation's AE.")
                incoming = incoming.model_copy(update={"identity_id": ""})
            action = ADD if current is None else SAME if _same_payload(incoming, current, {"id", "created_at", "updated_at"}) else UPDATE
            plan.items.append(PlanItem(SECTION_QUERIES, query.name, action))
            if action != SAME:
                plan._queries.append(incoming)
    if SECTION_RULES in chosen:
        existing_rules = store.list_route_rules()
        for rule in _dedupe(parsed.rules, "router rule", plan.problems):
            current = _match(existing_rules, rule)
            who = f"The rule {rule.name}"
            incoming = rule.model_copy(
                update={
                    "id": current.id if current else rule.id,
                    "source_remote_id": remote_ref(rule.source_remote_id, who) if rule.source_remote_id else "",
                    "destination_remote_ids": [remote_ref(item, who) for item in rule.destination_remote_ids],
                    "pdf_destination_id": remote_ref(rule.pdf_destination_id, who) if rule.pdf_destination_id else "",
                    "pdf_identity_id": identity_ids.get(rule.pdf_identity_id, rule.pdf_identity_id),
                }
            )
            if incoming.pdf_identity_id and not incoming.pdf_identity_id.startswith("ae:") and incoming.pdf_identity_id not in known_identities:
                plan.warnings.append(f"{who} presents as a virtual AE that does not exist on this computer; it will fall back to this workstation's AE.")
                incoming = incoming.model_copy(update={"pdf_identity_id": ""})
            ignore = {"created_at", "updated_at", "status", "seen_study_uids", "pdf_sent_keys", "last_run_at", "next_run_at", "last_run_ok", "last_error"}
            action = ADD if current is None else SAME if _same_payload(incoming, current, ignore) else UPDATE
            detail = "new rules start stopped" if action == ADD else "keeps its running state" if action == UPDATE else ""
            plan.items.append(PlanItem(SECTION_RULES, rule.name, action, detail))
            if rule.is_folder_watch and action != SAME:
                plan.warnings.append(f"{who} watches {rule.pdf_folder}. Check that folder exists on this computer, and that the app's account may read and write it.")
            if rule.export_dir and action != SAME:
                plan.warnings.append(f"{who} writes files to {rule.export_dir}. Check that folder exists on this computer.")
            if action != SAME:
                plan._rules.append((action, incoming))
    return plan


# --- applying ------------------------------------------------------------------


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: (path.read_bytes() if path.exists() else None) for path in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)


def apply_import(store: ConfigStore, plan: ImportPlan, on_error: Callable[[Exception], None] | None = None) -> ImportResult:
    """Write the plan. If any write fails the settings go back as they were."""
    snapshot = _snapshot([store.config_path, store.saved_queries_path, store.route_rules_path])
    try:
        for action, identity in plan._identities:
            if action == ADD:
                store.add_identity(identity)
            else:
                store.update_identity(identity.id, identity)
        for action, remote in plan._remotes:
            if action == ADD:
                store.add_remote(remote)
            else:
                store.update_remote(remote.id, remote)
        if plan._local is not None:
            store.save_local(plan._local.model_copy(update={"listener_paused": store.load().local.listener_paused}))
        for query in plan._queries:
            store.save_query(query)
        for action, rule in plan._rules:
            if action == ADD:
                store.add_route_rule(
                    rule.model_copy(update={"status": "stopped", "next_run_at": None, "seen_study_uids": [], "last_run_at": None, "last_run_ok": None, "last_error": ""})
                )
            else:
                store.update_route_rule(rule.id, rule)
    except Exception as exc:  # noqa: BLE001
        _restore(snapshot)
        if on_error is not None:
            on_error(exc)
        raise SettingsError(f"The import failed and nothing was changed: {type(exc).__name__}: {exc}") from exc
    result = ImportResult()
    for item in plan.items:
        if item.action == ADD:
            result.added += 1
        elif item.action == UPDATE:
            result.updated += 1
        else:
            result.unchanged += 1
    return result
