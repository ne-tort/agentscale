"""Transport-neutral contracts for equipment source adapters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SourceKind(StrEnum):
    S4B_WEB = "s4b_web"
    S4B_PRICE_FILE = "s4b_price_file"
    IMAP = "imap"


class SourceConfig(_Contract):
    """Non-secret source identity and adapter options.

    Credentials are intentionally represented only by an external reference. The
    adapter receives resolved credentials through its runtime boundary, never from
    this model or an environment loader.
    """

    id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    kind: SourceKind
    display_name: str = Field(min_length=1)
    credential_ref: str | None = Field(default=None, min_length=1)
    endpoint: str | None = None
    mailbox: str | None = None
    options: Mapping[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_kind_fields(self) -> SourceConfig:
        if self.kind is SourceKind.IMAP and not self.mailbox:
            raise ValueError("IMAP source requires mailbox")
        if self.kind is SourceKind.S4B_WEB and not self.endpoint:
            raise ValueError("S4B web source requires endpoint")
        return self


class IngestionRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionRun(_Contract):
    id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime | None = None
    state: IngestionRunState = IngestionRunState.QUEUED
    artifact_ids: tuple[str, ...] = ()
    snapshot_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> IngestionRun:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        terminal = {IngestionRunState.SUCCEEDED, IngestionRunState.PARTIAL, IngestionRunState.FAILED, IngestionRunState.CANCELLED}
        if self.state in terminal and self.finished_at is None:
            raise ValueError("terminal run requires finished_at")
        if self.state is IngestionRunState.FAILED and not self.error_message:
            raise ValueError("failed run requires error_message")
        return self


class RawArtifact(_Contract):
    """Immutable provenance for bytes or a remote representation received."""

    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    captured_at: datetime
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_uri: str | None = None
    external_id: str | None = None
    filename: str | None = None
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


class NormalizedOfferSnapshot(_Contract):
    """A source-independent offer observation, retaining source provenance."""

    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    external_key: str = Field(min_length=1)
    supplier_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    unit_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    availability: str | None = None
    product_code: str | None = None
    source_updated_at: datetime | None = None
    attributes: Mapping[str, str] = Field(default_factory=dict)
    raw_locator: str | None = None

    @field_validator("source_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("source_updated_at must be timezone-aware")
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @model_validator(mode="after")
    def validate_price_currency(self) -> NormalizedOfferSnapshot:
        if (self.unit_price is None) != (self.currency is None):
            raise ValueError("unit_price and currency must be provided together")
        return self


class IngestionBatch(_Contract):
    run: IngestionRun
    artifacts: tuple[RawArtifact, ...] = ()
    offers: tuple[NormalizedOfferSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> IngestionBatch:
        artifact_ids = {artifact.id for artifact in self.artifacts}
        if len(artifact_ids) != len(self.artifacts):
            raise ValueError("batch artifacts must have unique ids")
        if len(set(self.run.artifact_ids)) != len(self.run.artifact_ids):
            raise ValueError("run artifact_ids must be unique")
        if set(self.run.artifact_ids) != artifact_ids:
            raise ValueError("run artifact_ids must match batch artifacts")
        offer_ids = {offer.id for offer in self.offers}
        if len(offer_ids) != len(self.offers):
            raise ValueError("batch offers must have unique ids")
        external_keys = {offer.external_key for offer in self.offers}
        if len(external_keys) != len(self.offers):
            raise ValueError("batch offers must have unique external_key values")
        if any(artifact.run_id != self.run.id for artifact in self.artifacts):
            raise ValueError("all artifacts must belong to run")
        if any(artifact.source_id != self.run.source_id for artifact in self.artifacts):
            raise ValueError("all artifacts must belong to run source")
        if any(offer.run_id != self.run.id for offer in self.offers):
            raise ValueError("all offers must belong to run")
        if any(offer.source_id != self.run.source_id for offer in self.offers):
            raise ValueError("all offers must belong to run source")
        if self.run.state is IngestionRunState.SUCCEEDED and self.run.snapshot_count != len(self.offers):
            raise ValueError("succeeded run snapshot_count must match offers")
        if any(offer.artifact_id not in artifact_ids for offer in self.offers):
            raise ValueError("offer artifact_id must reference a batch artifact")
        if self.run.snapshot_count != len(self.offers):
            raise ValueError("run snapshot_count must match offers")
        return self


class AdapterContext(Protocol):
    """Runtime boundary supplied by the caller; no persistence is prescribed."""

    async def credentials(self, credential_ref: str | None) -> Mapping[str, str]: ...


class EquipmentIngestionAdapter(Protocol):
    """Adapter protocol implemented by S4B web/file and IMAP connectors."""

    kind: SourceKind

    async def ingest(self, source: SourceConfig, run: IngestionRun, context: AdapterContext) -> IngestionBatch:
        """Fetch source data and return artifacts plus normalized snapshots."""


class S4BWebIngestionAdapter(EquipmentIngestionAdapter, Protocol):
    kind: Literal[SourceKind.S4B_WEB]


class S4BPriceFileIngestionAdapter(EquipmentIngestionAdapter, Protocol):
    kind: Literal[SourceKind.S4B_PRICE_FILE]


class IMAPIngestionAdapter(EquipmentIngestionAdapter, Protocol):
    kind: Literal[SourceKind.IMAP]


__all__ = [
    "AdapterContext",
    "EquipmentIngestionAdapter",
    "IMAPIngestionAdapter",
    "S4BPriceFileIngestionAdapter",
    "S4BWebIngestionAdapter",
    "IngestionBatch",
    "IngestionRun",
    "IngestionRunState",
    "NormalizedOfferSnapshot",
    "RawArtifact",
    "SourceConfig",
    "SourceKind",
]
