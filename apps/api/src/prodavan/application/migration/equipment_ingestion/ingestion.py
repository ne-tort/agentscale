"""Transport-neutral, fail-closed equipment source ingestion contract.

Persistence adapters must provide immutable append/CAS semantics; this module does
not infer canonical owner identity or make an OpenSearch index authoritative.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_id: str | None
    source_kind: str | None
    schema_version: str | None
    canonical_owner_id: str | None
    tenant_id: str | None
    project_id: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.source_id and self.source_kind and self.schema_version
                    and self.canonical_owner_id and self.tenant_id)


@dataclass(frozen=True, slots=True)
class SupplierIdentity:
    supplier_id: str | None
    source_supplier_key: str | None
    display_name: str | None = None
    verified: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.supplier_id and self.source_supplier_key and self.verified)


@dataclass(frozen=True, slots=True)
class SourcePage:
    cursor: str | None
    next_cursor: str | None
    records: tuple[Mapping[str, Any], ...]
    observed_at: datetime
    source_revision: str | None = None

    def __post_init__(self) -> None:
        _aware(self.observed_at)


class SourceAdapter(Protocol):
    """Adapter performs bounded acquisition; credentials remain outside run evidence."""

    supplier_identity: SupplierIdentity

    async def identity(self) -> SourceIdentity: ...
    async def fetch_page(self, *, cursor: str | None, limit: int) -> SourcePage: ...
    async def fetch_raw_artifact(self, *, cursor: str | None, limit: int) -> bytes: ...


class IngestionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RawArtifact:
    artifact_id: str
    source_id: str
    run_id: str
    content_digest: str
    byte_length: int
    media_type: str
    immutable: bool
    retained_reference: str


@dataclass(frozen=True, slots=True)
class CatalogOffer:
    offer_id: str
    snapshot_id: str
    supplier_id: str
    source_id: str
    source_record_key: str
    observed_at: datetime
    currency: str
    price: str | None
    availability: str | None
    attributes: Mapping[str, Any]
    provenance_digest: str

    def __post_init__(self) -> None:
        _aware(self.observed_at)
        if not all((self.offer_id, self.snapshot_id, self.supplier_id, self.source_id,
                    self.source_record_key, self.currency, self.provenance_digest)):
            raise ValueError("offer identity and provenance are required")


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    snapshot_id: str
    tenant_id: str
    owner_id: str
    source_id: str
    supplier_id: str
    run_id: str
    artifact_id: str
    schema_version: str
    source_revision: str | None
    observed_at: datetime
    freshness: FreshnessState
    offers: tuple[CatalogOffer, ...]
    content_digest: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class IngestionRun:
    run_id: str
    source: SourceIdentity
    supplier: SupplierIdentity
    status: IngestionRunStatus
    checkpoint_cursor: str | None
    page_count: int
    record_count: int
    artifact_ids: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    started_at: datetime
    updated_at: datetime
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionContract:
    projection_name: str
    projection_version: str
    source_snapshot_id: str
    source_digest: str
    rebuilt_at: datetime
    document_count: int
    deterministic: bool


class IngestionEvidencePort(Protocol):
    async def load_run(self, run_id: str) -> IngestionRun | None: ...
    async def append_run(self, run: IngestionRun, *, expected_checkpoint: str | None) -> bool: ...
    async def append_raw_artifact(self, artifact: RawArtifact, payload: bytes) -> bool: ...
    async def append_snapshot(self, snapshot: CatalogSnapshot) -> bool: ...
    async def mark_source_failure(self, source_id: str, *, at: datetime, reason: str) -> None: ...
    async def read_last_good(self, source_id: str) -> CatalogSnapshot | None: ...


class CatalogNormalizer(Protocol):
    def normalize(self, record: Mapping[str, Any], *, source: SourceIdentity,
                  supplier: SupplierIdentity, snapshot_id: str, observed_at: datetime,
                  artifact_id: str) -> CatalogOffer: ...


class EquipmentIngestionService:
    """Resumable page ingestion; missing canonical identity never creates positive data."""

    def __init__(self, *, evidence: IngestionEvidencePort, normalizer: CatalogNormalizer,
                 page_size: int = 250, max_pages_per_call: int = 20) -> None:
        if not 1 <= page_size <= 1000 or not 1 <= max_pages_per_call <= 100:
            raise ValueError("ingestion bounds are invalid")
        self._evidence, self._normalizer = evidence, normalizer
        self._page_size, self._max_pages = page_size, max_pages_per_call

    async def ingest(self, *, adapter: SourceAdapter, run_id: str,
                     now: datetime | None = None) -> IngestionRun:
        started = now or datetime.now(UTC)
        _aware(started)
        source, supplier = await adapter.identity(), getattr(adapter, "supplier_identity", None)
        if not isinstance(supplier, SupplierIdentity):
            return self._blocked(run_id, source, SupplierIdentity(None, None), started, "supplier_identity_missing")
        if not source.complete:
            return self._blocked(run_id, source, supplier, started, "canonical_source_owner_identity_incomplete")
        if not supplier.complete:
            return self._blocked(run_id, source, supplier, started, "canonical_supplier_identity_incomplete")

        prior = await self._evidence.load_run(run_id)
        if prior is not None and (prior.source != source or prior.supplier != supplier):
            return self._blocked(run_id, source, supplier, started, "run_identity_conflict")
        cursor = prior.checkpoint_cursor if prior else None
        page_count = prior.page_count if prior else 0
        record_count = prior.record_count if prior else 0
        artifact_ids = list(prior.artifact_ids if prior else ())
        snapshot_ids = list(prior.snapshot_ids if prior else ())
        offers: list[CatalogOffer] = []
        expected_checkpoint = cursor

        for _ in range(self._max_pages):
            page = await adapter.fetch_page(cursor=cursor, limit=self._page_size)
            if page.cursor != cursor:
                return self._blocked(run_id, source, supplier, started, "source_cursor_mismatch")
            raw = await adapter.fetch_raw_artifact(cursor=cursor, limit=self._page_size)
            artifact_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            artifact_id = f"{run_id}:{artifact_digest[7:23]}"
            artifact = RawArtifact(artifact_id, source.source_id or "", run_id, artifact_digest,
                                   len(raw), "application/octet-stream", True,
                                   f"immutable://equipment-artifacts/{artifact_id}")
            if not await self._evidence.append_raw_artifact(artifact, raw):
                return self._blocked(run_id, source, supplier, started, "immutable_artifact_conflict")
            artifact_ids.append(artifact_id)
            snapshot_id = f"{run_id}:snapshot:{page_count + 1}"
            for record in page.records:
                offer = self._normalizer.normalize(record, source=source, supplier=supplier,
                    snapshot_id=snapshot_id, observed_at=page.observed_at, artifact_id=artifact_id)
                if offer.source_id != source.source_id or offer.supplier_id != supplier.supplier_id:
                    return self._blocked(run_id, source, supplier, started, "normalized_identity_mismatch")
                offers.append(offer)
            page_count += 1
            record_count += len(page.records)
            snapshot = CatalogSnapshot(snapshot_id, source.tenant_id or "", source.canonical_owner_id or "",
                source.source_id or "", supplier.supplier_id or "", run_id, artifact_id,
                source.schema_version or "", page.source_revision, page.observed_at,
                FreshnessState.FRESH, tuple(offers), _digest([o.provenance_digest for o in offers]), True)
            if not await self._evidence.append_snapshot(snapshot):
                return self._blocked(run_id, source, supplier, started, "snapshot_conflict")
            snapshot_ids.append(snapshot_id)
            run = IngestionRun(run_id, source, supplier, IngestionRunStatus.RUNNING,
                page.next_cursor, page_count, record_count, tuple(artifact_ids), tuple(snapshot_ids),
                prior.started_at if prior else started, page.observed_at)
            if not await self._evidence.append_run(run, expected_checkpoint=expected_checkpoint):
                return self._blocked(run_id, source, supplier, started, "checkpoint_cas_conflict")
            expected_checkpoint, cursor = page.next_cursor, page.next_cursor
            if cursor is None:
                return IngestionRun(run_id, source, supplier, IngestionRunStatus.SUCCEEDED,
                    None, page_count, record_count, tuple(artifact_ids), tuple(snapshot_ids),
                    prior.started_at if prior else started, page.observed_at)
        return IngestionRun(run_id, source, supplier, IngestionRunStatus.PARTIAL,
            cursor, page_count, record_count, tuple(artifact_ids), tuple(snapshot_ids),
            prior.started_at if prior else started, datetime.now(UTC), "page_budget_exhausted")

    async def record_failure(self, *, source_id: str, reason: str,
                             at: datetime | None = None) -> tuple[CatalogSnapshot | None, FreshnessState]:
        observed = at or datetime.now(UTC)
        _aware(observed)
        await self._evidence.mark_source_failure(source_id, at=observed, reason=reason)
        last_good = await self._evidence.read_last_good(source_id)
        return last_good, FreshnessState.STALE if last_good else FreshnessState.UNKNOWN

    @staticmethod
    def _blocked(run_id: str, source: SourceIdentity, supplier: SupplierIdentity,
                 now: datetime, code: str) -> IngestionRun:
        return IngestionRun(run_id, source, supplier, IngestionRunStatus.BLOCKED,
                            None, 0, 0, (), (), now, now, code)


__all__ = ["CatalogOffer", "CatalogSnapshot", "EquipmentIngestionService", "FreshnessState",
           "IngestionRun", "IngestionRunStatus", "ProjectionContract", "RawArtifact",
           "SourceAdapter", "SourceIdentity", "SourcePage", "SupplierIdentity"]
