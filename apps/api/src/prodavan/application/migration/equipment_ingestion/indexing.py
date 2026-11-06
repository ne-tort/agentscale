"""Post-commit search indexing for durable equipment ingestion.

`search_projection` is a pure builder: it validates provenance and returns an
idempotent, deterministic document set. This module owns the *ordering* and
*evidence* around the write:

* indexing happens only after the page transaction that committed the snapshot
  and its offer batches returned ``COMMITTED`` (or ``REPLAYED``);
* a repeat with the same immutable page evidence is a replay, never a second
  write with different identity;
* an indexing failure never deletes or rewrites committed catalog evidence and
  never lets the run reach ``succeeded`` — the failure is reported as a redacted
  reason code and the page stays available for a forward repair.

The actual index writer (OpenSearch) is outside this migration file zone, so it
is a narrow port. Anything that is not an explicit success is a failure: an
unknown writer result never counts as indexed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Callable, Protocol

from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogSnapshot,
    RawArtifact,
    evidence_digest,
)
from prodavan.application.migration.equipment_ingestion.search_projection import (
    EquipmentSearchProjection,
    ProjectionScope,
    SearchDocumentFieldAdapter,
    SnapshotEvidence,
    build_search_projection,
)

DEFAULT_PROJECTION_NAME = "equipment-catalog"
DEFAULT_PROJECTION_VERSION = "1"


class IndexingState(StrEnum):
    """Durable disposition of one committed page in the search projection."""

    INDEXED = "indexed"
    REPLAYED = "replayed"
    SKIPPED = "skipped_no_documents"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class IndexMarker:
    """Append-only evidence that one immutable page reached the index (or not).

    ``marker_id`` binds snapshot identity, snapshot content digest and the
    projection contract version, so a changed snapshot digest can never be
    mistaken for an already indexed page.
    """

    marker_id: str
    snapshot_id: str
    content_digest: str
    source_id: str
    projection_name: str
    projection_version: str
    state: IndexingState
    document_count: int
    recorded_at: datetime
    source_digest: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, IndexingState):
            raise ValueError("index marker requires a typed state")
        if not all((self.marker_id, self.snapshot_id, self.content_digest, self.source_id,
                    self.projection_name, self.projection_version)):
            raise ValueError("index marker identity is incomplete")
        if self.document_count < 0:
            raise ValueError("index marker document count cannot be negative")
        if self.state in (IndexingState.FAILED, IndexingState.BLOCKED) and not self.reason_code:
            raise ValueError("a failed or blocked marker requires a reason code")


def index_marker_id(*, snapshot_id: str, content_digest: str, projection_name: str,
                    projection_version: str) -> str:
    """Deterministic, length-bounded ledger id for one page/contract pair."""
    if not all(isinstance(value, str) and value.strip() for value in (
            snapshot_id, content_digest, projection_name, projection_version)):
        raise ValueError("index marker identity is incomplete")
    token = evidence_digest({
        "snapshot_id": snapshot_id,
        "content_digest": content_digest,
        "projection_name": projection_name,
        "projection_version": projection_version,
    })[7:31]
    return f"eir_idx_{token}"


@dataclass(frozen=True, slots=True)
class IndexingOutcome:
    """Result of indexing one committed page; always safe to log."""

    state: IndexingState
    snapshot_id: str
    content_digest: str
    document_count: int
    reason_code: str
    marker: IndexMarker | None = None
    contract_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, IndexingState):
            raise ValueError("indexing outcome requires a typed state")
        if not self.reason_code:
            raise ValueError("indexing outcome requires a redacted reason code")
        if self.document_count < 0:
            raise ValueError("indexing outcome document count cannot be negative")

    @property
    def searchable(self) -> bool:
        """True only when this page is durably represented in the index."""
        return self.state in (IndexingState.INDEXED, IndexingState.REPLAYED,
                              IndexingState.SKIPPED)

    @property
    def failed(self) -> bool:
        return self.state in (IndexingState.FAILED, IndexingState.BLOCKED)


class SearchDocumentWriter(Protocol):
    """OpenSearch/engine boundary; document ids come from the projection.

    ``write_documents`` must upsert by ``document_id`` and be safe to repeat
    with the same projection. Returning ``False`` or raising is a failure; a
    writer must never report success for a partial document set.
    """

    async def write_documents(self, projection: EquipmentSearchProjection) -> bool: ...


class IndexMarkerStore(Protocol):
    """Durable marker ledger (PostgreSQL) for post-commit indexing evidence."""

    async def read_index_marker(self, *, snapshot_id: str, content_digest: str,
                                projection_name: str, projection_version: str
                                ) -> IndexMarker | None: ...

    async def record_index_marker(self, marker: IndexMarker) -> None: ...


class PostCommitIndexer:
    """Index one committed immutable page, idempotently and fail-closed."""

    def __init__(self, *, writer: SearchDocumentWriter, markers: IndexMarkerStore,
                 field_adapter: SearchDocumentFieldAdapter,
                 projection_name: str = DEFAULT_PROJECTION_NAME,
                 projection_version: str = DEFAULT_PROJECTION_VERSION,
                 clock: Callable[[], datetime] | None = None) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (projection_name, projection_version)):
            raise ValueError("projection name and version are required")
        if not callable(getattr(writer, "write_documents", None)):
            raise ValueError("a search document writer is required")
        if not callable(getattr(markers, "read_index_marker", None)) or not callable(
                getattr(markers, "record_index_marker", None)):
            raise ValueError("a durable index marker store is required")
        if not callable(getattr(field_adapter, "fields_for", None)):
            raise ValueError("a search field adapter is required")
        self._writer, self._markers, self._fields = writer, markers, field_adapter
        self._projection_name = projection_name
        self._projection_version = projection_version
        self._clock = clock or (lambda: datetime.now(UTC))

    async def index_snapshot(self, *, snapshot: CatalogSnapshot, artifact: RawArtifact,
                             now: datetime | None = None) -> IndexingOutcome:
        """Index exactly one committed page snapshot; never mutate its evidence."""
        recorded_at = now or self._clock()  # type: ignore[operator]
        if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if not isinstance(snapshot, CatalogSnapshot) or not isinstance(artifact, RawArtifact):
            raise ValueError("indexing requires a committed snapshot and artifact")
        if snapshot.artifact_id != artifact.artifact_id or snapshot.run_id != artifact.run_id:
            return IndexingOutcome(IndexingState.BLOCKED, snapshot.snapshot_id,
                                   snapshot.content_digest, 0,
                                   "snapshot_artifact_mismatch")

        existing = await self._markers.read_index_marker(
            snapshot_id=snapshot.snapshot_id, content_digest=snapshot.content_digest,
            projection_name=self._projection_name,
            projection_version=self._projection_version,
        )
        if existing is not None and existing.state is IndexingState.INDEXED:
            return IndexingOutcome(
                IndexingState.REPLAYED, snapshot.snapshot_id, snapshot.content_digest,
                existing.document_count, "index_evidence_present", marker=existing,
                contract_digest=existing.source_digest,
            )

        if not snapshot.accepted or not snapshot.offers:
            # A page whose every row was quarantined has nothing to index. It is
            # still recorded, so run finalization can prove it was considered.
            return await self._record(
                snapshot=snapshot, artifact=artifact, recorded_at=recorded_at,
                state=IndexingState.SKIPPED, document_count=0,
                reason_code="no_searchable_documents",
            )

        scope = ProjectionScope(snapshot.tenant_id, snapshot.owner_id, snapshot.project_id)
        try:
            projection = build_search_projection(
                evidence=(SnapshotEvidence(snapshot=snapshot, artifact=artifact),),
                scope=scope,
                field_adapter=self._fields,
                projection_name=self._projection_name,
                projection_version=self._projection_version,
                expected_generation=0,
                rebuilt_at=recorded_at,
            )
        except (TypeError, ValueError):
            return IndexingOutcome(IndexingState.BLOCKED, snapshot.snapshot_id,
                                   snapshot.content_digest, 0,
                                   "search_projection_invalid")

        if projection.document_count == 0:
            return await self._record(
                snapshot=snapshot, artifact=artifact, recorded_at=recorded_at,
                state=IndexingState.SKIPPED, document_count=0,
                reason_code="no_searchable_documents", projection=projection,
            )

        try:
            written = await self._writer.write_documents(projection)
        except Exception:
            written = False
        if written is not True:
            return IndexingOutcome(IndexingState.FAILED, snapshot.snapshot_id,
                                   snapshot.content_digest, projection.document_count,
                                   "search_index_write_failed",
                                   contract_digest=projection.source_digest)

        return await self._record(
            snapshot=snapshot, artifact=artifact, recorded_at=recorded_at,
            state=IndexingState.INDEXED, document_count=projection.document_count,
            reason_code="index_written", projection=projection,
        )

    async def _record(self, *, snapshot: CatalogSnapshot, artifact: RawArtifact,
                      recorded_at: datetime, state: IndexingState, document_count: int,
                      reason_code: str,
                      projection: EquipmentSearchProjection | None = None) -> IndexingOutcome:
        marker = IndexMarker(
            marker_id=index_marker_id(
                snapshot_id=snapshot.snapshot_id, content_digest=snapshot.content_digest,
                projection_name=self._projection_name,
                projection_version=self._projection_version,
            ),
            snapshot_id=snapshot.snapshot_id,
            content_digest=snapshot.content_digest,
            source_id=artifact.source_id,
            projection_name=self._projection_name,
            projection_version=self._projection_version,
            state=state,
            document_count=document_count,
            recorded_at=recorded_at,
            source_digest=projection.source_digest if projection is not None else None,
            reason_code=reason_code,
        )
        await self._markers.record_index_marker(marker)
        return IndexingOutcome(state, snapshot.snapshot_id, snapshot.content_digest,
                               document_count, reason_code, marker=marker,
                               contract_digest=marker.source_digest)


__all__ = [
    "DEFAULT_PROJECTION_NAME",
    "DEFAULT_PROJECTION_VERSION",
    "IndexMarker",
    "IndexMarkerStore",
    "IndexingOutcome",
    "IndexingState",
    "PostCommitIndexer",
    "SearchDocumentWriter",
    "index_marker_id",
]
