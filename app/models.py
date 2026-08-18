"""Configuration and result models for the PACS admin toolkit."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AE_TITLE_MAX = 16
AE_TITLE_PATTERN = re.compile(r"^[\x20-\x7e]+$")


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


class RemoteNode(BaseModel):
    """A peer DICOM Application Entity (PACS, modality, VNA, test SCP)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    ae_title: str
    host: str = ""
    hostname: str = ""
    port: int = Field(default=11112, ge=1, le=65535)
    notes: str = ""
    kind: Literal["pacs", "mwl", "modality", "vna", "other"] = "other"
    provides_mwl: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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


class AppConfig(BaseModel):
    local: LocalAE = Field(default_factory=LocalAE)
    remotes: list[RemoteNode] = Field(default_factory=list)

    def get_remote(self, remote_id: str) -> RemoteNode | None:
        for remote in self.remotes:
            if remote.id == remote_id:
                return remote
        return None

    def mwl_remotes(self) -> list[RemoteNode]:
        return [remote for remote in self.remotes if remote.provides_mwl]


class ToolStep(BaseModel):
    name: str
    ok: bool
    message: str
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tool_id: str
    tool_name: str
    ok: bool
    summary: str
    remote_id: str | None = None
    remote_name: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0
    steps: list[ToolStep] = Field(default_factory=list)
    log: str = ""
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)


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
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
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
