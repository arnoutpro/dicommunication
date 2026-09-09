"""Configuration and result models for the PACS admin toolkit."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AE_TITLE_MAX = 16
AE_TITLE_PATTERN = re.compile(r"^[\x20-\x7e]+$")


def new_record_id() -> str:
    """Identifier for a stored record. Server-assigned, never taken from a request."""
    return uuid.uuid4().hex[:12]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_ae_title(value: str) -> str:
    """Normalize a DICOM Application Entity Title (16-char printable ASCII)."""
    value = value.strip()
    if not value:
        raise ValueError("AE Title is required")
    if len(value) > AE_TITLE_MAX:
        raise ValueError(f"AE Title must be {AE_TITLE_MAX} characters or fewer")
    if "\\" in value:
        raise ValueError("AE Title cannot contain a backslash")
    if not AE_TITLE_PATTERN.fullmatch(value):
        raise ValueError("AE Title must be printable ASCII")
    return value


class LocalAE(BaseModel):
    """Identity of this workstation when it talks to other DICOM nodes."""

    ae_title: str = "DICOMM"
    host: str = "0.0.0.0"
    hostname: str = ""
    port: int = Field(default=11112, ge=1, le=65535)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_pdu: int = Field(default=16382, ge=4096, le=131072)
    implementation_version: str = "DICOMM_1"
    station_ae_title: str = ""
    mwl_scp_enabled: bool = False
    storage_scp_enabled: bool = False

    @field_validator("ae_title")
    @classmethod
    def _ae_title(cls, value: str) -> str:
        return normalize_ae_title(value)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Host is required")
        return value

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("implementation_version")
    @classmethod
    def _implementation_version(cls, value: str) -> str:
        value = (value or "").strip() or "DICOMM_1"
        if len(value) > 16:
            raise ValueError("Implementation version must be 16 characters or fewer")
        return value

    @field_validator("station_ae_title")
    @classmethod
    def _station_ae_title(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        return normalize_ae_title(value)


class VirtualAE(BaseModel):
    """A saved calling-AE identity for impersonating a modality without extra listen ports."""

    id: str = Field(default_factory=new_record_id)
    name: str
    ae_title: str
    station_ae_title: str = ""
    modality: str = ""
    notes: str = ""

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("ae_title")
    @classmethod
    def _ae_title(cls, value: str) -> str:
        return normalize_ae_title(value)

    @field_validator("station_ae_title")
    @classmethod
    def _station_ae_title(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        return normalize_ae_title(value)

    @field_validator("modality", "notes")
    @classmethod
    def _optional_text(cls, value: str) -> str:
        return (value or "").strip()

    @property
    def scheduled_station_ae_title(self) -> str:
        return self.station_ae_title or self.ae_title


class RemoteNode(BaseModel):
    """A peer DICOM Application Entity (PACS, modality, VNA, test SCP)."""

    id: str = Field(default_factory=new_record_id)
    name: str
    ae_title: str
    host: str = ""
    hostname: str = ""
    port: int = Field(default=11112, ge=1, le=65535)
    notes: str = ""
    kind: Literal["pacs", "mwl", "modality", "vna", "other"] = "other"
    provides_mwl: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("host", "hostname", "notes")
    @classmethod
    def _optional_text(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("ae_title")
    @classmethod
    def _ae_title(cls, value: str) -> str:
        return normalize_ae_title(value)

    @model_validator(mode="after")
    def _normalize_connection_and_kind(self) -> RemoteNode:
        if not self.host and not self.hostname:
            raise ValueError("IP address or hostname is required")
        if not self.host:
            self.host = self.hostname
        if self.kind == "mwl":
            self.provides_mwl = True
        return self

    @property
    def connect_host(self) -> str:
        return self.host or self.hostname

    @property
    def endpoint(self) -> str:
        return f"{self.connect_host}:{self.port}"

    @property
    def kind_label(self) -> str:
        labels = {
            "pacs": "PACS",
            "mwl": "DMWL",
            "modality": "Modality",
            "vna": "VNA",
            "other": "Other",
        }
        return labels.get(self.kind, self.kind)


HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_SEEN_STUDY_UIDS = 5000
ROUTE_RUN_MAX_MATCHES = 500


class RouteRule(BaseModel):
    """A scheduled C-FIND against a PACS, with optional retrieve/forward.

    Runs the C-FIND and records new matches (``seen_study_uids`` tracks what
    has already been fully handled so a rule doesn't redo it on every tick —
    see RouterScheduler for how that also makes a paused/stopped run resume
    without repeating work). ``destination_remote_ids``, when set, additionally
    retrieves and forwards each new match.

    ``status`` is the rule's own start/pause/stop state, independent of its
    schedule: "active" is eligible to fire on schedule; "paused" and
    "stopped" are both skipped by the scheduler and can both still be run
    manually (Run now). Starting either one always computes a fresh
    next_run_at from that moment (RouterScheduler.start_rule) — they differ
    only in ``next_run_at`` while inactive: Stop clears it (nothing
    scheduled), Pause leaves the old value in place purely as a "would have
    run at" record.
    """

    id: str = Field(default_factory=new_record_id)
    name: str
    status: Literal["active", "paused", "stopped"] = "active"

    source_remote_id: str
    level: Literal["STUDY", "SERIES"] = "STUDY"
    modality: str = ""
    date_scope: Literal["today", "yesterday", "last_n_days", "all"] = "today"
    date_last_n_days: int = Field(default=1, ge=1, le=365)
    station_ae_title: str = ""
    extra_query: dict[str, str] = Field(default_factory=dict)

    schedule_mode: Literal["interval", "daily"] = "interval"
    interval_minutes: int = Field(default=15, ge=1, le=10080)
    daily_times: list[str] = Field(default_factory=list)
    days_of_week: list[int] = Field(default_factory=list)

    destination_remote_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_run_ok: bool | None = None
    last_error: str = ""
    seen_study_uids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("source_remote_id")
    @classmethod
    def _required_source(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Source PACS is required")
        return value

    @field_validator("station_ae_title")
    @classmethod
    def _optional_station(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        return normalize_ae_title(value)

    @field_validator("modality")
    @classmethod
    def _modality(cls, value: str) -> str:
        parts = [part.strip().upper() for part in (value or "").split(",") if part.strip()]
        return ",".join(parts)

    @field_validator("daily_times")
    @classmethod
    def _daily_times(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = (item or "").strip()
            if not text:
                continue
            if not HHMM_PATTERN.fullmatch(text):
                raise ValueError(f"Invalid time {text!r}, expected HH:MM (24h)")
            if text not in cleaned:
                cleaned.append(text)
        return sorted(cleaned)

    @field_validator("days_of_week")
    @classmethod
    def _days_of_week(cls, value: list[int]) -> list[int]:
        for day in value:
            if not 0 <= day <= 6:
                raise ValueError("Days of week must be 0 (Monday) through 6 (Sunday)")
        return sorted(set(value))

    @field_validator("destination_remote_ids")
    @classmethod
    def _dedupe_destinations(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            item = (item or "").strip()
            if item and item not in seen:
                seen.append(item)
        return seen

    @model_validator(mode="after")
    def _require_daily_times(self) -> RouteRule:
        if self.schedule_mode == "daily" and not self.daily_times:
            raise ValueError("Add at least one time of day for a daily schedule")
        return self

    def has_seen(self, study_instance_uid: str) -> bool:
        return study_instance_uid in self.seen_study_uids

    def mark_seen(self, study_instance_uids: Iterable[str]) -> None:
        """Remember matched studies so re-runs only report new ones.

        Kept as a bounded, most-recent-first list rather than growing forever —
        a rule that runs every few minutes for years must not turn its own
        dedupe memory into the thing that makes it slow to load.
        """
        merged = list(self.seen_study_uids)
        for uid in study_instance_uids:
            uid = (uid or "").strip()
            if uid and uid not in merged:
                merged.insert(0, uid)
        self.seen_study_uids = merged[:MAX_SEEN_STUDY_UIDS]

    @property
    def schedule_label(self) -> str:
        if self.schedule_mode == "daily":
            days = self.days_of_week
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            day_text = "every day" if not days else ", ".join(day_names[d] for d in days)
            return f"{', '.join(self.daily_times)} ({day_text})"
        if self.interval_minutes % 60 == 0:
            hours = self.interval_minutes // 60
            return f"every {hours} hour{'s' if hours != 1 else ''}"
        return f"every {self.interval_minutes} min"


class RouteMatch(BaseModel):
    """One study a route run found (and, in a later phase, retrieved/forwarded)."""

    study_instance_uid: str
    series_instance_uid: str = ""
    patient_name: str = ""
    patient_id: str = ""
    study_date: str = ""
    accession_number: str = ""
    modality: str = ""
    study_description: str = ""
    status: Literal["found", "retrieved", "forwarded", "failed"] = "found"
    error: str = ""


class RouteRun(BaseModel):
    """History entry for one execution of a RouteRule.

    ``status`` is "completed" unless a Pause or Stop request interrupted the
    run partway through — see RouterScheduler. An interrupted run's
    completed matches are still fully handled (marked seen); the studies it
    didn't get to just show up as new again on the next run.
    """

    id: str = Field(default_factory=new_record_id)
    rule_id: str
    rule_name: str = ""
    started_at: datetime = Field(default_factory=utc_now)
    duration_ms: float = 0
    ok: bool = True
    error: str = ""
    status: Literal["completed", "interrupted"] = "completed"
    matched_count: int = 0
    new_count: int = 0
    matches: list[RouteMatch] = Field(default_factory=list)


class LoggingSettings(BaseModel):
    """Rotating application log written next to config.json."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(default=2 * 1024 * 1024, ge=256 * 1024, le=50 * 1024 * 1024)
    backup_count: int = Field(default=3, ge=1, le=20)

    @property
    def max_megabytes(self) -> int:
        return max(1, round(self.max_bytes / (1024 * 1024)))


class AppConfig(BaseModel):
    local: LocalAE = Field(default_factory=LocalAE)
    identities: list[VirtualAE] = Field(default_factory=list)
    remotes: list[RemoteNode] = Field(default_factory=list)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    def get_remote(self, remote_id: str) -> RemoteNode | None:
        for remote in self.remotes:
            if remote.id == remote_id:
                return remote
        return None

    def get_identity(self, identity_id: str | None) -> VirtualAE | None:
        if not identity_id:
            return None
        for identity in self.identities:
            if identity.id == identity_id:
                return identity
        return None

    def calling_ae(self, identity_id: str | None = None) -> LocalAE:
        """Workstation listen settings with an optional virtual calling AE Title."""
        if not identity_id:
            return self.local
        identity = self.get_identity(identity_id)
        if identity is None:
            raise KeyError(identity_id)
        return self.local.model_copy(
            update={
                "ae_title": identity.ae_title,
                "station_ae_title": identity.scheduled_station_ae_title,
            }
        )

    def mwl_remotes(self) -> list[RemoteNode]:
        return [remote for remote in self.remotes if remote.provides_mwl]


class ToolStep(BaseModel):
    name: str
    ok: bool
    message: str
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    id: str = Field(default_factory=new_record_id)
    tool_id: str
    tool_name: str
    ok: bool
    summary: str
    remote_id: str | None = None
    remote_name: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    duration_ms: float = 0
    steps: list[ToolStep] = Field(default_factory=list)
    log: str = ""
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    calling_ae: str | None = None


class EchoBoardRow(BaseModel):
    remote: RemoteNode
    result: ToolResult | None = None
    status: str = "unknown"


class EchoBoard(BaseModel):
    rows: list[EchoBoardRow] = Field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0
    duration_ms: float | None = None
    ran_at: datetime | None = None


class WorklistQuery(BaseModel):
    patient_name: str = ""
    patient_id: str = ""
    accession_number: str = ""
    modality: str = ""
    station_ae_title: str = ""
    scheduled_date: str = ""

    @field_validator(
        "patient_name",
        "patient_id",
        "accession_number",
        "modality",
        "station_ae_title",
        "scheduled_date",
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()


class WorklistEntry(BaseModel):
    id: str = Field(default_factory=new_record_id)
    patient_name: str
    patient_id: str
    patient_birth_date: str = ""
    patient_sex: str = ""
    accession_number: str = ""
    requested_procedure_id: str = ""
    requested_procedure_description: str = ""
    modality: str = "CT"
    station_ae_title: str = ""
    station_name: str = ""
    scheduled_date: str = ""
    scheduled_time: str = ""
    scheduled_physician: str = ""
    study_instance_uid: str = ""
    scheduled_procedure_step_id: str = ""

    @field_validator(
        "patient_name",
        "patient_id",
        "patient_birth_date",
        "patient_sex",
        "accession_number",
        "requested_procedure_id",
        "requested_procedure_description",
        "modality",
        "station_ae_title",
        "station_name",
        "scheduled_date",
        "scheduled_time",
        "scheduled_physician",
        "study_instance_uid",
        "scheduled_procedure_step_id",
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("station_ae_title")
    @classmethod
    def _station(cls, value: str) -> str:
        if not value:
            return ""
        return normalize_ae_title(value)


class WorklistQueryResult(BaseModel):
    ok: bool
    summary: str
    source: str = ""
    duration_ms: float = 0
    entries: list[WorklistEntry] = Field(default_factory=list)
    log: str = ""
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    calling_ae: str = ""


class Hl7Message(BaseModel):
    """A saved HL7 v2 draft. Stored as text; not parsed."""

    id: str = Field(default_factory=new_record_id)
    name: str
    body: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("body")
    @classmethod
    def _required_body(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Paste an HL7 v2 message first")
        return value

    @property
    def preview(self) -> str:
        text = self.body.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        return text[:48]
