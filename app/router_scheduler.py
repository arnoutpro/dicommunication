"""Dicom Router background service.

Runs each active RouteRule's C-FIND on its schedule. A rule with no
destinations only reports new matches. A rule with destinations additionally
C-MOVEs each new match to this workstation's local Storage SCP and C-STOREs
it on to every destination node — the retrieve/forward phase.

Mirrors the threading shape of WorklistSCP (app/mwl_scp.py): one background
thread started/stopped from the app lifespan, reading/writing through the
same ConfigStore other requests use. On top of that ticking loop, each rule
can also be started/paused/stopped/run-now on demand (see RouterScheduler's
request_pause/request_stop/start_rule/run_now); `_active` tracks which rule
is currently executing (scheduled or manual) and its cooperative interrupt
signal, checked between targets so Pause/Stop can cut a long batch short
without redoing or losing already-forwarded work.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

from pydicom.dataset import Dataset
from pydicom.datadict import tag_for_keyword
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.applog import log
from app.dicom_client import associate
from app.models import LocalAE, RemoteNode, ROUTE_RUN_MAX_MATCHES, RouteMatch, RouteRule, RouteRun, utc_now
from app.mwl_scp import STORAGE_INBOX, WorklistSCP
from app.store import ConfigStore

TICK_SECONDS = 20
PENDING = {0xFF00, 0xFF01}


def _assoc_up(assoc: Any) -> bool:
    return bool(assoc is not None and getattr(assoc, "is_established", False))


def _release(assoc: Any) -> None:
    if not _assoc_up(assoc):
        return
    try:
        assoc.release()
    except Exception:  # noqa: BLE001
        try:
            assoc.abort()
        except Exception:  # noqa: BLE001
            pass


def _text(ds: Dataset, keyword: str) -> str:
    value = getattr(ds, keyword, "")
    return "" if value is None else str(value).strip()


def _date_query(rule: RouteRule) -> str:
    """DICOM DA/date-range value for the rule's date scope, in local calendar days."""
    today = date.today()
    if rule.date_scope == "today":
        return today.strftime("%Y%m%d")
    if rule.date_scope == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y%m%d")
    if rule.date_scope == "last_n_days":
        start = today - timedelta(days=rule.date_last_n_days - 1)
        return f"{start.strftime('%Y%m%d')}-{today.strftime('%Y%m%d')}"
    return ""


def _apply_extra_query(identifier: Dataset, extra_query: dict[str, str]) -> None:
    for keyword, value in extra_query.items():
        if not tag_for_keyword(keyword):
            continue
        try:
            setattr(identifier, keyword, value)
        except Exception:  # noqa: BLE001
            continue


def _study_identifier(rule: RouteRule) -> Dataset:
    identifier = Dataset()
    identifier.QueryRetrieveLevel = "STUDY"
    identifier.PatientName = ""
    identifier.PatientID = ""
    identifier.AccessionNumber = ""
    identifier.StudyDate = _date_query(rule)
    identifier.StudyDescription = ""
    identifier.ModalitiesInStudy = rule.modality
    identifier.StudyInstanceUID = ""
    _apply_extra_query(identifier, rule.extra_query)
    return identifier


def _series_identifier(rule: RouteRule, study_instance_uid: str) -> Dataset:
    """SERIES-level find under one study. Hierarchical Study Root requires the parent UID."""
    identifier = Dataset()
    identifier.QueryRetrieveLevel = "SERIES"
    identifier.StudyInstanceUID = study_instance_uid
    identifier.SeriesInstanceUID = ""
    identifier.SeriesDescription = ""
    identifier.Modality = rule.modality.split(",")[0] if rule.modality else ""
    if rule.station_ae_title:
        identifier.StationName = rule.station_ae_title
    return identifier


def compute_next_run(rule: RouteRule, after: datetime) -> datetime:
    """Next due time at/after ``after`` (UTC). Daily schedules are evaluated in local time."""
    if rule.schedule_mode == "interval":
        return after + timedelta(minutes=rule.interval_minutes)
    local_after = after.astimezone()
    times = sorted(dt_time.fromisoformat(t) for t in rule.daily_times)
    allowed_days = set(rule.days_of_week) or set(range(7))
    for day_offset in range(8):
        candidate_date = (local_after + timedelta(days=day_offset)).date()
        if candidate_date.weekday() not in allowed_days:
            continue
        for slot in times:
            candidate = datetime.combine(candidate_date, slot, tzinfo=local_after.tzinfo)
            if candidate > local_after:
                return candidate.astimezone(timezone.utc)
    return after + timedelta(days=1)


def _run_find(local: LocalAE, remote: RemoteNode, identifier: Dataset) -> tuple[bool, list[Dataset], str]:
    assoc = None
    records: list[Dataset] = []
    error = ""
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelFind])
        if not _assoc_up(assoc) or not assoc.accepted_contexts:
            error = "Study Root Query/Retrieve FIND was not accepted."
            return False, [], error
        for status, identifier_ds in assoc.send_c_find(identifier, StudyRootQueryRetrieveInformationModelFind):
            if not status:
                error = "No C-FIND response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code in PENDING and identifier_ds is not None:
                records.append(identifier_ds)
            elif code == 0x0000:
                continue
            else:
                error = f"C-FIND status 0x{code:04X}"
                break
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _release(assoc)
    return not error, records, error


def _move_identifier(study_instance_uid: str, series_instance_uid: str = "") -> Dataset:
    identifier = Dataset()
    identifier.StudyInstanceUID = study_instance_uid
    if series_instance_uid:
        identifier.QueryRetrieveLevel = "SERIES"
        identifier.SeriesInstanceUID = series_instance_uid
    else:
        identifier.QueryRetrieveLevel = "STUDY"
    return identifier


def _retrieve(
    local: LocalAE,
    remote: RemoteNode,
    dest_ae: str,
    study_instance_uid: str,
    series_instance_uid: str = "",
) -> tuple[list[Dataset], str]:
    """C-MOVE one study or series to dest_ae; return what landed on the local Storage SCP.

    An error is only reported when nothing arrived — a C-MOVE that fails after
    partially storing objects still hands back what was received, same as the
    Structured Report retrieve in app/tools/find_advanced.py.
    """
    assoc = None
    error = ""
    STORAGE_INBOX.begin()
    try:
        _ae, assoc = associate(local, remote, [StudyRootQueryRetrieveInformationModelMove])
        if not _assoc_up(assoc) or not assoc.accepted_contexts:
            error = "Study Root Query/Retrieve MOVE was not accepted."
        else:
            identifier = _move_identifier(study_instance_uid, series_instance_uid)
            for status, _remaining in assoc.send_c_move(
                identifier, dest_ae, StudyRootQueryRetrieveInformationModelMove
            ):
                if not status:
                    error = "No C-MOVE response (timeout, abort, or invalid PDU)."
                    break
                code = int(status.Status)
                if code in PENDING:
                    continue
                if code == 0x0000:
                    break
                error = f"C-MOVE status 0x{code:04X}"
                break
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _release(assoc)
        time.sleep(0.05)
        datasets = STORAGE_INBOX.finish()
    return datasets, ("" if datasets else error)


def _forward(local: LocalAE, destination: RemoteNode, datasets: list[Dataset]) -> tuple[int, str]:
    """C-STORE every dataset to one destination. Returns (stored_count, error)."""
    if not datasets:
        return 0, ""
    sop_classes: list[str] = []
    for ds in datasets:
        sop = str(getattr(ds, "SOPClassUID", "") or "")
        if sop and sop not in sop_classes:
            sop_classes.append(sop)
    if not sop_classes:
        return 0, "Retrieved objects had no SOP Class UID."
    assoc = None
    stored = 0
    error = ""
    try:
        _ae, assoc = associate(local, destination, sop_classes)
        if not _assoc_up(assoc) or not assoc.accepted_contexts:
            return 0, "Storage association was not accepted."
        for ds in datasets:
            status = assoc.send_c_store(ds)
            if not status:
                error = "No C-STORE response (timeout, abort, or invalid PDU)."
                break
            code = int(status.Status)
            if code == 0x0000:
                stored += 1
            else:
                error = f"C-STORE status 0x{code:04X}"
                break
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _release(assoc)
    return stored, error


class RouterScheduler:
    def __init__(self, store: ConfigStore, storage_scp: WorklistSCP) -> None:
        self.store = store
        self.storage_scp = storage_scp
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._active_lock = threading.Lock()
        # rule_id -> interrupt signal for whichever run (scheduled or manual)
        # is currently executing that rule. Presence of a key means running.
        self._active: dict[str, threading.Event] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="router-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("Router scheduler tick failed: %s", exc)
            self._stop_event.wait(TICK_SECONDS)

    def _tick(self) -> None:
        now = utc_now()
        for rule in self.store.list_route_rules():
            if rule.status != "active":
                continue
            if rule.next_run_at is not None and rule.next_run_at > now:
                continue
            event = self._register(rule.id)
            if event is None:
                continue  # already running (a manual trigger got there first)
            try:
                self.run_rule(rule, now=now, interrupt=event)
            except Exception as exc:  # noqa: BLE001
                log.warning("Route rule %r failed: %s", rule.name, exc)
            finally:
                self._unregister(rule.id)

    def is_running(self, rule_id: str) -> bool:
        with self._active_lock:
            return rule_id in self._active

    def _register(self, rule_id: str) -> threading.Event | None:
        """Claim a rule for execution. None means it's already running."""
        with self._active_lock:
            if rule_id in self._active:
                return None
            event = threading.Event()
            self._active[rule_id] = event
            return event

    def _unregister(self, rule_id: str) -> None:
        with self._active_lock:
            self._active.pop(rule_id, None)

    def request_pause(self, rule_id: str) -> RouteRule:
        """Stop scheduling this rule and interrupt a run in progress, if any.

        next_run_at is left untouched (purely informational while paused —
        starting always computes a fresh one, see start_rule).
        """
        updated = self.store.set_route_rule_status(rule_id, "paused")
        self._interrupt_if_running(rule_id)
        return updated

    def request_stop(self, rule_id: str) -> RouteRule:
        """Stop scheduling this rule, clear its next run, interrupt if running."""
        updated = self.store.set_route_rule_status(rule_id, "stopped", next_run_at=None)
        self._interrupt_if_running(rule_id)
        return updated

    def start_rule(self, rule_id: str) -> str:
        """Mark active and run right now.

        The triggered run's own completion computes a fresh next_run_at (see
        run_rule) since the rule is active by the time it finishes — so this
        doesn't need to compute one itself. Returns "started" or
        "already_running" (from the run-now that follows).
        """
        updated = self.store.set_route_rule_status(rule_id, "active")
        return self.run_now(rule_id, rule=updated)

    def run_now(self, rule_id: str, *, rule: RouteRule | None = None) -> str:
        """Run a rule immediately in the background, regardless of its status."""
        rule = rule or self._require_rule(rule_id)
        event = self._register(rule.id)
        if event is None:
            return "already_running"
        thread = threading.Thread(
            target=self._run_registered,
            args=(rule, event),
            name=f"route-rule-{rule.id}",
            daemon=True,
        )
        thread.start()
        return "started"

    def _run_registered(self, rule: RouteRule, event: threading.Event) -> None:
        try:
            self.run_rule(rule, interrupt=event)
        except Exception:  # noqa: BLE001
            log.exception("Route rule %r failed", rule.name)
        finally:
            self._unregister(rule.id)

    def _interrupt_if_running(self, rule_id: str) -> None:
        with self._active_lock:
            event = self._active.get(rule_id)
        if event is not None:
            event.set()

    def _require_rule(self, rule_id: str) -> RouteRule:
        rule = self.store.get_route_rule(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        return rule

    def run_rule(
        self,
        rule: RouteRule,
        *,
        now: datetime | None = None,
        interrupt: threading.Event | None = None,
    ) -> RouteRun:
        """Run one rule's C-FIND (and, with destinations configured, retrieve+forward).

        Checked between targets (never mid-association): if `interrupt` is
        set, finishes the target in progress and stops before starting the
        next one. Each target is marked seen — persisted immediately, not
        batched at the end — as soon as it's fully handled, so an interrupted
        run never redoes work: whatever it didn't reach is still "new" and
        picked up by the next run, scheduled or manual.
        """
        now = now or utc_now()
        started = time.perf_counter()
        config = self.store.load()
        local = config.local
        remote = config.get_remote(rule.source_remote_id)
        run = RouteRun(rule_id=rule.id, rule_name=rule.name, started_at=now)
        interrupted = bool(interrupt is not None and interrupt.is_set())

        if remote is None:
            run.ok = False
            run.error = "Source PACS is no longer configured."
        elif interrupted:
            run.ok = True
            run.status = "interrupted"
        else:
            ok, study_datasets, error = _run_find(local, remote, _study_identifier(rule))
            run.ok = ok
            run.error = error
            if ok:
                studies = [
                    {
                        "study_instance_uid": _text(ds, "StudyInstanceUID"),
                        "patient_name": _text(ds, "PatientName"),
                        "patient_id": _text(ds, "PatientID"),
                        "study_date": _text(ds, "StudyDate"),
                        "accession_number": _text(ds, "AccessionNumber"),
                        "modality": _text(ds, "ModalitiesInStudy"),
                        "study_description": _text(ds, "StudyDescription"),
                    }
                    for ds in study_datasets
                    if _text(ds, "StudyInstanceUID")
                ]
                run.matched_count = len(studies)
                new_studies = [s for s in studies if not rule.has_seen(s["study_instance_uid"])]

                destinations = [
                    node
                    for node in (config.get_remote(rid) for rid in rule.destination_remote_ids)
                    if node is not None
                ]
                targets: list[dict[str, str]] = []
                for study in new_studies:
                    targets.extend(self._expand_targets(local, remote, rule, study))
                run.new_count = len(targets)

                for target in targets[:ROUTE_RUN_MAX_MATCHES]:
                    if interrupt is not None and interrupt.is_set():
                        interrupted = True
                        break
                    match = RouteMatch(
                        study_instance_uid=target["study_instance_uid"],
                        series_instance_uid=target.get("series_instance_uid", ""),
                        patient_name=target["patient_name"],
                        patient_id=target["patient_id"],
                        study_date=target["study_date"],
                        accession_number=target["accession_number"],
                        modality=target["modality"],
                        study_description=target["study_description"],
                    )
                    dedupe_uid = match.series_instance_uid or match.study_instance_uid
                    newly_handled = False
                    if target.get("_error"):
                        match.status = "failed"
                        match.error = target["_error"]
                    elif not rule.destination_remote_ids:
                        newly_handled = True
                    else:
                        newly_handled = self._retrieve_and_forward(local, remote, destinations, match)
                    run.matches.append(match)
                    if newly_handled:
                        rule.mark_seen([dedupe_uid])
                        try:
                            self.store.save_route_rule_run_state(rule)
                        except KeyError:
                            break  # rule was deleted while this run was in flight
                run.status = "interrupted" if interrupted else "completed"

        run.duration_ms = (time.perf_counter() - started) * 1000
        rule.last_run_at = now
        rule.last_run_ok = run.ok
        rule.last_error = run.error
        # Scheduling is only this run's business while the rule is (still)
        # active — a status flip requested mid-run (Pause/Stop) already wrote
        # its own next_run_at and must not be raced by a stale recompute here.
        current = self.store.get_route_rule(rule.id)
        if current is not None:
            if current.status == "active":
                rule.next_run_at = compute_next_run(rule, now)
            else:
                rule.next_run_at = current.next_run_at
            try:
                self.store.save_route_rule_run_state(rule)
            except KeyError:
                pass  # rule was deleted while this run was in flight
        self.store.add_route_run(run)
        return run

    def _expand_targets(
        self, local: LocalAE, remote: RemoteNode, rule: RouteRule, study: dict[str, str]
    ) -> list[dict[str, str]]:
        """One target per study (STUDY level) or per matching series (SERIES level)."""
        if rule.level != "SERIES":
            return [{**study, "series_instance_uid": ""}]
        ok, series_datasets, error = _run_find(
            local, remote, _series_identifier(rule, study["study_instance_uid"])
        )
        if not ok:
            return [{**study, "series_instance_uid": "", "_error": error}]
        targets = []
        for ds in series_datasets:
            series_uid = _text(ds, "SeriesInstanceUID")
            if not series_uid:
                continue
            targets.append(
                {
                    **study,
                    "series_instance_uid": series_uid,
                    "modality": _text(ds, "Modality") or study["modality"],
                    "study_description": _text(ds, "SeriesDescription") or study["study_description"],
                }
            )
        return targets

    def _retrieve_and_forward(
        self,
        local: LocalAE,
        remote: RemoteNode,
        destinations: list[RemoteNode],
        match: RouteMatch,
    ) -> bool:
        if not local.storage_scp_enabled or not self.storage_scp.running:
            match.status = "failed"
            match.error = "Local Storage SCP is not enabled/listening; cannot C-MOVE here."
            return False
        if not destinations:
            match.status = "failed"
            match.error = "No configured destination is reachable in the current config."
            return False

        dest_ae = local.ae_title
        datasets, error = _retrieve(
            local, remote, dest_ae, match.study_instance_uid, match.series_instance_uid
        )
        if not datasets:
            match.status = "failed"
            match.error = error or "C-MOVE returned no objects."
            return False
        match.status = "retrieved"

        failures: list[str] = []
        for destination in destinations:
            stored, forward_error = _forward(local, destination, datasets)
            if stored < len(datasets):
                failures.append(f"{destination.name}: {forward_error or f'{stored}/{len(datasets)} stored'}")
        if failures:
            match.status = "failed"
            match.error = "; ".join(failures)
            return False
        match.status = "forwarded"
        return True
