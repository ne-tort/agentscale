"""Transport-neutral, fail-closed equipment source ingestion contract.

The service owns orchestration only. Persistence adapters must implement an
atomic, immutable page commit (artifact + page snapshot + run/checkpoint) and a
compare-and-swap projection publish. No database or filesystem is accessed here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ALLOWED_AVAILABILITY = frozenset({"in_stock", "out_of_stock", "backorder", "unknown"})


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, default=_json_default, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def evidence_digest(value: Any) -> str:
    """Public stable digest helper for evidence and quarantine references."""
    return _digest(value)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def offer_identity(source_id: str, supplier_id: str, source_record_key: str) -> str:
    """Create an ID without conflating source and supplier identity."""
    if (not isinstance(source_id, str) or not isinstance(supplier_id, str)
            or not isinstance(source_record_key, str)
            or not all((source_id.strip(), supplier_id.strip(), source_record_key.strip()))):
        raise ValueError("source, supplier and source record references are required")
    return f"offer:{_digest({'source_id': source_id, 'supplier_id': supplier_id, 'source_record_key': source_record_key})[7:]}"


def offer_provenance_digest(*, source_id: str, supplier_id: str,
                            source_record_key: str, artifact_id: str,
                            observed_at: datetime) -> str:
    """Bind normalized row provenance to immutable artifact and identities."""
    _aware(observed_at)
    if (not isinstance(source_id, str) or not isinstance(supplier_id, str)
            or not isinstance(source_record_key, str) or not isinstance(artifact_id, str)
            or not all((source_id.strip(), supplier_id.strip(),
                        source_record_key.strip(), artifact_id.strip()))):
        raise ValueError("source, supplier, record and artifact references are required")
    return _digest({
        "source_id": source_id,
        "supplier_id": supplier_id,
        "source_record_key": source_record_key,
        "artifact_id": artifact_id,
        "observed_at": observed_at.isoformat(),
    })


def _page_key(run_id: str, cursor: str | None) -> str:
    return _digest({"run_id": run_id, "cursor": cursor})[7:]


def _page_commit_outcome(value: Any) -> "PageCommitOutcome":
    """Normalize a bare result or a typed outcome; unknown shapes are failures."""

    if isinstance(value, PageCommitOutcome):
        return value
    if isinstance(value, PageCommitResult):
        return PageCommitOutcome(result=value, reason_code=value.value)
    return PageCommitOutcome(result=PageCommitResult.CONFLICT,
                             reason_code="page_commit_result_invalid")


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
        required = (self.source_id, self.source_kind, self.schema_version,
                    self.canonical_owner_id, self.tenant_id)
        optional_project_valid = (self.project_id is None or
                                  isinstance(self.project_id, str) and bool(self.project_id.strip()))
        return optional_project_valid and all(
            isinstance(value, str) and bool(value.strip()) for value in required
        )


@dataclass(frozen=True, slots=True)
class SupplierIdentity:
    supplier_id: str | None
    source_supplier_key: str | None
    display_name: str | None = None
    verified: bool = False

    @property
    def complete(self) -> bool:
        return all(
            isinstance(value, str) and bool(value.strip())
            for value in (self.supplier_id, self.source_supplier_key)
        ) and self.verified


@dataclass(frozen=True, slots=True)
class SourcePage:
    cursor: str | None
    next_cursor: str | None
    records: tuple[Any, ...]
    observed_at: datetime
    source_revision: str | None = None
    raw_digest: str = field(kw_only=True)

    def __post_init__(self) -> None:
        _aware(self.observed_at)
        if not isinstance(self.records, tuple):
            raise ValueError("page records must be a bounded tuple")
        if self.cursor is not None and not isinstance(self.cursor, str):
            raise ValueError("page cursor must be an opaque string")
        if self.next_cursor is not None and not isinstance(self.next_cursor, str):
            raise ValueError("page next_cursor must be an opaque string")
        if not _valid_digest(self.raw_digest):
            raise ValueError("page raw_digest must be a sha256 digest")


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


class PageCommitResult(StrEnum):
    """Outcome of the single artifact + snapshot + checkpoint transaction."""

    COMMITTED = "committed"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    CHECKPOINT_CONFLICT = "checkpoint_conflict"
    PARTIALLY_COMMITTED = "partially_committed"
    #: The page transaction committed (artifact evidence, snapshot, offers,
    #: checkpoint and run status), but a required post-commit step failed, e.g.
    #: search indexing after the commit. The committed evidence is kept and the
    #: run must not be reported as ``succeeded``; repair is a forward re-run of
    #: the post-commit step from the committed snapshot.
    COMMITTED_INCOMPLETE = "committed_incomplete"


@dataclass(frozen=True, slots=True)
class PageCommitOutcome:
    """Typed commit outcome carrying a redacted reason code and offer counters.

    A durable adapter returns this instead of a bare ``PageCommitResult`` so the
    orchestration can distinguish "the whole page committed" from "the page was
    only partially written". ``PARTIALLY_COMMITTED`` means offer batches reached
    the database but the page did not reach its terminal state: the checkpoint
    stayed put, so the page is retried and every retried offer must match by
    ``(snapshot_id, external_key)``. That outcome can never yield a succeeded run.

    ``COMMITTED_INCOMPLETE`` is the committed counterpart of a failure: the page
    is durable but a post-commit requirement (search indexing) failed, so the run
    is failed with the adapter's reason code while the page stays committed and
    resumable from its advanced checkpoint.

    ``reason_code`` is a redacted machine code (``[a-z][a-z0-9_.-]*``) and never
    contains payload, credential or supplier text. A reason code that differs
    from the bare ``PageCommitResult`` value is treated as adapter-supplied and
    is surfaced verbatim by the orchestration; the bare enum keeps the generic
    legacy codes.
    """

    result: PageCommitResult
    reason_code: str
    offers_written: int = 0
    offers_replayed: int = 0
    offers_conflicted: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.result, PageCommitResult):
            raise ValueError("page commit outcome requires a typed result")
        if not isinstance(self.reason_code, str) or not _REASON_CODE_RE.fullmatch(self.reason_code):
            raise ValueError("page commit outcome reason_code must be a redacted code")
        for name in ("offers_written", "offers_replayed", "offers_conflicted"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("page commit offer counters must be nonnegative integers")
        if self.result is PageCommitResult.COMMITTED and self.offers_conflicted:
            raise ValueError("a committed page cannot report conflicting offers")
        if self.result not in (PageCommitResult.COMMITTED,
                               PageCommitResult.PARTIALLY_COMMITTED,
                               PageCommitResult.COMMITTED_INCOMPLETE) and self.offers_written:
            raise ValueError("only a committed or partially committed page may report written offers")


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

    def __post_init__(self) -> None:
        if not all((self.artifact_id, self.source_id, self.run_id, self.media_type,
                    self.retained_reference)):
            raise ValueError("artifact identity and retained reference are required")
        if not self.source_id or not self.run_id:
            raise ValueError("artifact source/run identity is required")
        if not _valid_digest(self.content_digest) or self.byte_length < 0 or not self.immutable:
            raise ValueError("artifact digest, byte length or immutability is invalid")


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
    availability: str
    attributes: Mapping[str, Any]
    provenance_digest: str
    artifact_id: str

    def __post_init__(self) -> None:
        _aware(self.observed_at)
        if not isinstance(self.source_record_key, str) or not self.source_record_key.strip():
            raise ValueError("offer source record reference is required")
        if not all((self.offer_id, self.snapshot_id, self.supplier_id, self.source_id,
                    self.artifact_id, self.provenance_digest)):
            raise ValueError("offer identity, source/artifact reference and provenance are required")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("currency must be an uppercase three-letter code")
        if not isinstance(self.availability, str) or self.availability not in _ALLOWED_AVAILABILITY:
            raise ValueError("unsupported availability value")
        if self.price is not None:
            if not isinstance(self.price, str) or not self.price or self.price != self.price.strip():
                raise ValueError("price must be a nonnegative decimal string")
            try:
                amount = Decimal(self.price)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("price must be a nonnegative decimal string") from exc
            if not amount.is_finite() or amount < 0:
                raise ValueError("price must be a nonnegative decimal string")
        if not isinstance(self.attributes, Mapping):
            raise ValueError("offer attributes must be a mapping")
        if not _valid_digest(self.provenance_digest):
            raise ValueError("offer provenance_digest must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class QuarantinedRow:
    """Redacted row disposition; original bytes remain in the raw artifact."""

    row_number: int
    artifact_id: str
    record_digest: str
    error_code: str

    def __post_init__(self) -> None:
        if self.row_number < 1 or not self.artifact_id or not self.error_code:
            raise ValueError("quarantine row reference is invalid")
        if not _valid_digest(self.record_digest):
            raise ValueError("quarantine record_digest must be a sha256 digest")


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
    quarantined_rows: tuple[QuarantinedRow, ...] = ()
    project_id: str | None = None

    def __post_init__(self) -> None:
        _aware(self.observed_at)
        if not all((self.snapshot_id, self.tenant_id, self.owner_id, self.source_id,
                    self.supplier_id, self.run_id, self.artifact_id, self.schema_version)):
            raise ValueError("snapshot scope and provenance are required")
        if not _valid_digest(self.content_digest):
            raise ValueError("snapshot content_digest must be a sha256 digest")
        for offer in self.offers:
            if (offer.snapshot_id, offer.source_id, offer.supplier_id) != (
                    self.snapshot_id, self.source_id, self.supplier_id):
                raise ValueError("snapshot contains an offer with mismatched references")
            if offer.artifact_id != self.artifact_id:
                raise ValueError("snapshot contains an offer with a mismatched artifact")
        if self.accepted != bool(self.offers):
            raise ValueError("only pages with accepted offers may become accepted snapshots")
        if any(row.artifact_id != self.artifact_id for row in self.quarantined_rows):
            raise ValueError("snapshot contains a quarantine reference to another artifact")


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
    observed_count: int = 0
    quarantined_count: int = 0

    def __post_init__(self) -> None:
        _aware(self.started_at)
        _aware(self.updated_at)
        if self.observed_count == 0 and self.record_count + self.quarantined_count > 0:
            object.__setattr__(self, "observed_count", self.record_count + self.quarantined_count)
        if min(self.page_count, self.record_count, self.observed_count, self.quarantined_count) < 0:
            raise ValueError("run counters cannot be negative")
        if self.record_count + self.quarantined_count != self.observed_count:
            raise ValueError("every observed row must be accepted or quarantined")
        if self.page_count != len(self.artifact_ids) or self.page_count != len(self.snapshot_ids):
            raise ValueError("run page, artifact and snapshot counts must agree")
        if self.status is IngestionRunStatus.PARTIAL and self.checkpoint_cursor is None:
            raise ValueError("partial run requires a resumable checkpoint")


@dataclass(frozen=True, slots=True)
class ProjectionContract:
    projection_name: str
    projection_version: str
    source_snapshot_id: str
    source_digest: str
    rebuilt_at: datetime
    document_count: int
    deterministic: bool
    generation: int = 0
    source_snapshot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.rebuilt_at)
        if not all((self.projection_name, self.projection_version, self.source_snapshot_id)):
            raise ValueError("projection identity and source snapshot are required")
        if not _valid_digest(self.source_digest) or self.document_count < 0 or self.generation < 0:
            raise ValueError("projection digest, document count or generation is invalid")
        if not self.deterministic:
            raise ValueError("equipment projection must be deterministic")


@dataclass(frozen=True, slots=True)
class CatalogProjection:
    contract: ProjectionContract
    offers: tuple[CatalogOffer, ...]


class IngestionEvidencePort(Protocol):
    """Durable evidence boundary; ``commit_page`` must be one transaction.

    A new page commits the exact raw bytes/artifact, exact page snapshot, every
    offer batch of that page and the run checkpoint together. Any failure leaves
    all of them unchanged. Exact replay returns ``REPLAYED`` if the durable run
    already contains the identical page, even when it has advanced beyond that
    page. Changed raw bytes or snapshot content returns ``CONFLICT`` and cannot
    overwrite accepted evidence.

    An adapter may return a :class:`PageCommitOutcome` instead of a bare result
    to report a redacted reason code and offer counters, including the
    ``PARTIALLY_COMMITTED`` case where offer batches landed but the page did not
    reach its terminal state. A bare :class:`PageCommitResult` stays supported.
    """

    async def load_run(self, run_id: str) -> IngestionRun | None: ...
    async def commit_page(self, *, artifact: RawArtifact, payload: bytes,
                          snapshot: CatalogSnapshot, run: IngestionRun,
                          expected_checkpoint: str | None
                          ) -> PageCommitResult | PageCommitOutcome: ...
    async def mark_source_failure(self, source_id: str, *, at: datetime, reason: str) -> None: ...
    async def record_run_failure(self, *, run: IngestionRun,
                                 expected_checkpoint: str | None) -> bool: ...
    async def read_last_good(self, source_id: str) -> CatalogSnapshot | None: ...


class ProjectionRebuildPort(Protocol):
    """Accepted snapshot input and generation-fenced derived publication."""

    async def accepted_snapshots(self, *, source_ids: tuple[str, ...] | None = None
                                 ) -> Sequence[CatalogSnapshot]: ...

    async def publish_projection(self, projection: CatalogProjection, *,
                                 expected_generation: int,
                                 expected_source_digest: str,
                                 source_ids: tuple[str, ...] | None = None) -> bool:
        """CAS generation and exact accepted-snapshot query scope/digest."""
        ...


class ProjectionRebuilder:
    """Rebuild only from accepted snapshots; publication is fenced by generation."""

    def __init__(self, *, source: ProjectionRebuildPort,
                 projection_name: str, projection_version: str) -> None:
        self._source = source
        self._projection_name = projection_name
        self._projection_version = projection_version

    async def rebuild(self, *, expected_generation: int,
                      source_ids: Sequence[str] | None = None,
                      rebuilt_at: datetime | None = None) -> CatalogProjection:
        if expected_generation < 0:
            raise ValueError("expected projection generation cannot be negative")
        observed = rebuilt_at or datetime.now(UTC)
        _aware(observed)
        source_scope = tuple(source_ids) if source_ids is not None else None
        snapshots = await self._source.accepted_snapshots(source_ids=source_scope)
        projection = build_catalog_projection(
            snapshots=snapshots,
            projection_name=self._projection_name,
            projection_version=self._projection_version,
            generation=expected_generation + 1,
            rebuilt_at=observed,
        )
        if not await self._source.publish_projection(
                projection, expected_generation=expected_generation,
                expected_source_digest=projection.contract.source_digest,
                source_ids=source_scope):
            raise RuntimeError("projection_generation_conflict")
        return projection


class CatalogNormalizer(Protocol):
    def normalize(self, record: Mapping[str, Any], *, source: SourceIdentity,
                  supplier: SupplierIdentity, snapshot_id: str, observed_at: datetime,
                  artifact_id: str) -> CatalogOffer: ...


def _projection_payload(offer: CatalogOffer) -> Mapping[str, Any]:
    return {
        "source_id": offer.source_id,
        "supplier_id": offer.supplier_id,
        "source_record_key": offer.source_record_key,
        "observed_at": offer.observed_at.isoformat(),
        "currency": offer.currency,
        "price": offer.price,
        "availability": offer.availability,
        "attributes": offer.attributes,
    }


def build_catalog_projection(*, snapshots: Sequence[CatalogSnapshot],
                              projection_name: str, projection_version: str,
                              generation: int, rebuilt_at: datetime) -> CatalogProjection:
    """Pure deterministic rebuild from accepted immutable page snapshots only."""
    _aware(rebuilt_at)
    if generation < 1 or not projection_name or not projection_version:
        raise ValueError("projection identity and positive generation are required")
    if any(not snapshot.accepted for snapshot in snapshots):
        raise ValueError("projection input contains an unaccepted snapshot")
    ordered_snapshots = tuple(sorted(snapshots, key=lambda item: item.snapshot_id))
    snapshot_ids = tuple(snapshot.snapshot_id for snapshot in ordered_snapshots)
    if len(set(snapshot_ids)) != len(snapshot_ids):
        raise ValueError("projection input contains duplicate snapshot identities")
    if not ordered_snapshots:
        raise ValueError("projection requires at least one accepted snapshot")
    scopes = {(snapshot.tenant_id, snapshot.owner_id, snapshot.project_id)
              for snapshot in ordered_snapshots}
    if len(scopes) > 1:
        raise ValueError("projection cannot combine tenant, owner or project scopes")

    latest: dict[tuple[str, str, str], CatalogOffer] = {}
    for snapshot in ordered_snapshots:
        for offer in snapshot.offers:
            if (offer.snapshot_id, offer.source_id, offer.supplier_id) != (
                    snapshot.snapshot_id, snapshot.source_id, snapshot.supplier_id):
                raise ValueError("projection input offer provenance mismatch")
            key = (offer.source_id, offer.supplier_id, offer.source_record_key)
            current = latest.get(key)
            if current is None or offer.observed_at > current.observed_at:
                latest[key] = offer
            elif offer.observed_at == current.observed_at:
                if _projection_payload(offer) != _projection_payload(current):
                    raise ValueError("conflicting offer evidence has the same observed time")
                if offer.snapshot_id < current.snapshot_id:
                    latest[key] = offer

    offers = tuple(sorted(latest.values(), key=lambda item: (
        item.source_id, item.supplier_id, item.source_record_key)))
    source_snapshot_id = (
        max(ordered_snapshots, key=lambda item: (item.observed_at, item.snapshot_id)).snapshot_id
        if ordered_snapshots else "empty-accepted-snapshot-set"
    )
    source_snapshot_ids = tuple(snapshot.snapshot_id for snapshot in ordered_snapshots)
    source_digest = _digest([
        {"snapshot_id": snapshot.snapshot_id, "content_digest": snapshot.content_digest}
        for snapshot in ordered_snapshots
    ])
    contract = ProjectionContract(
        projection_name=projection_name,
        projection_version=projection_version,
        source_snapshot_id=source_snapshot_id,
        source_digest=source_digest,
        rebuilt_at=rebuilt_at,
        document_count=len(offers),
        deterministic=True,
        generation=generation,
        source_snapshot_ids=source_snapshot_ids,
    )
    return CatalogProjection(contract=contract, offers=offers)


class EquipmentIngestionService:
    """Resumable page ingestion; incomplete identity never creates positive data."""

    def __init__(self, *, evidence: IngestionEvidencePort, normalizer: CatalogNormalizer,
                 page_size: int = 250, max_pages_per_call: int = 20,
                 max_raw_bytes: int = 16 * 1024 * 1024) -> None:
        if not 1 <= page_size <= 1000 or not 1 <= max_pages_per_call <= 100:
            raise ValueError("ingestion bounds are invalid")
        if max_raw_bytes < 1:
            raise ValueError("max_raw_bytes must be positive")
        self._evidence, self._normalizer = evidence, normalizer
        self._page_size, self._max_pages = page_size, max_pages_per_call
        self._max_raw_bytes = max_raw_bytes

    async def ingest(self, *, adapter: SourceAdapter, run_id: str,
                     now: datetime | None = None) -> IngestionRun:
        started = now or datetime.now(UTC)
        _aware(started)
        if not run_id:
            raise ValueError("run_id is required")
        try:
            source = await adapter.identity()
        except Exception:
            unknown_source = SourceIdentity(None, None, None, None, None)
            return self._blocked(run_id, unknown_source, SupplierIdentity(None, None),
                                 started, "source_identity_unavailable")
        if not isinstance(source, SourceIdentity):
            return self._blocked(run_id, SourceIdentity(None, None, None, None, None),
                                 SupplierIdentity(None, None), started,
                                 "source_identity_invalid")
        supplier = getattr(adapter, "supplier_identity", SupplierIdentity(None, None))
        if not isinstance(supplier, SupplierIdentity):
            supplier = SupplierIdentity(None, None)
        if not source.complete:
            return self._blocked(run_id, source, supplier, started,
                                 "canonical_source_owner_identity_incomplete")
        if not supplier.complete:
            return self._blocked(run_id, source, supplier, started,
                                 "canonical_supplier_identity_incomplete")

        prior = await self._evidence.load_run(run_id)
        if prior is not None and (prior.source != source or prior.supplier != supplier):
            return self._blocked(run_id, source, supplier, started, "run_identity_conflict")
        replay_completed_run = prior is not None and prior.status is IngestionRunStatus.SUCCEEDED
        cursor = None if replay_completed_run else prior.checkpoint_cursor if prior else None
        page_count = 0 if replay_completed_run else prior.page_count if prior else 0
        accepted_count = 0 if replay_completed_run else prior.record_count if prior else 0
        observed_count = (0 if replay_completed_run else prior.observed_count if prior else 0)
        quarantined_count = 0 if replay_completed_run else prior.quarantined_count if prior else 0
        artifact_ids = [] if replay_completed_run else list(prior.artifact_ids if prior else ())
        snapshot_ids = [] if replay_completed_run else list(prior.snapshot_ids if prior else ())
        expected_checkpoint = None if replay_completed_run else cursor
        run_started = prior.started_at if prior else started
        latest_update = prior.updated_at if prior else started

        for page_index in range(self._max_pages):
            try:
                page = await adapter.fetch_page(cursor=cursor, limit=self._page_size)
                if len(page.records) > self._page_size:
                    return await self._source_failed(
                        run_id, source, supplier, cursor, page_count, accepted_count,
                        observed_count, quarantined_count, artifact_ids, snapshot_ids,
                        run_started, latest_update, "source_page_limit_exceeded", page.observed_at,
                    )
                if page.cursor != cursor:
                    return await self._source_failed(
                        run_id, source, supplier, cursor, page_count, accepted_count,
                        observed_count, quarantined_count, artifact_ids, snapshot_ids,
                        run_started, latest_update, "source_cursor_mismatch", page.observed_at,
                    )
                if page.next_cursor is not None and page.next_cursor == cursor:
                    return await self._source_failed(
                        run_id, source, supplier, cursor, page_count, accepted_count,
                        observed_count, quarantined_count, artifact_ids, snapshot_ids,
                        run_started, latest_update, "source_cursor_did_not_advance", page.observed_at,
                    )
                raw = await adapter.fetch_raw_artifact(cursor=cursor, limit=self._page_size)
                if not isinstance(raw, bytes) or len(raw) > self._max_raw_bytes:
                    return await self._source_failed(
                        run_id, source, supplier, cursor, page_count, accepted_count,
                        observed_count, quarantined_count, artifact_ids, snapshot_ids,
                        run_started, latest_update, "raw_artifact_limit_exceeded", page.observed_at,
                    )
                raw_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
                if raw_digest != page.raw_digest:
                    return await self._source_failed(
                        run_id, source, supplier, cursor, page_count, accepted_count,
                        observed_count, quarantined_count, artifact_ids, snapshot_ids,
                        run_started, latest_update, "page_raw_digest_mismatch", page.observed_at,
                    )
            except Exception:
                return await self._source_failed(
                    run_id, source, supplier, cursor, page_count, accepted_count,
                    observed_count, quarantined_count, artifact_ids, snapshot_ids,
                    run_started, latest_update, "source_page_fetch_failed", datetime.now(UTC),
                )

            page_token = _page_key(run_id, cursor)
            artifact_id = f"{run_id}:artifact:{page_token}"
            snapshot_id = f"{run_id}:snapshot:{page_token}"
            if artifact_id in artifact_ids:
                return await self._source_failed(
                    run_id, source, supplier, cursor, page_count, accepted_count,
                    observed_count, quarantined_count, artifact_ids, snapshot_ids,
                    run_started, latest_update, "source_cursor_cycle", page.observed_at,
                )
            artifact = RawArtifact(
                artifact_id=artifact_id,
                source_id=source.source_id or "",
                run_id=run_id,
                content_digest=raw_digest,
                byte_length=len(raw),
                media_type="application/octet-stream",
                immutable=True,
                retained_reference=f"immutable://equipment-artifacts/{artifact_id}",
            )
            page_offers: list[CatalogOffer] = []
            quarantined: list[QuarantinedRow] = []
            seen_source_keys: set[str] = set()
            for row_number, record in enumerate(page.records, start=1):
                try:
                    row_digest = _digest({
                        "artifact_digest": artifact.content_digest,
                        "row_number": row_number,
                        "record": record,
                    })
                except Exception:
                    row_digest = _digest({
                        "artifact_digest": artifact.content_digest,
                        "row_number": row_number,
                    })
                if not isinstance(record, Mapping):
                    quarantined.append(QuarantinedRow(row_number, artifact_id,
                                                      row_digest, "record_not_an_object"))
                    continue
                try:
                    offer = self._normalizer.normalize(
                        record, source=source, supplier=supplier, snapshot_id=snapshot_id,
                        observed_at=page.observed_at, artifact_id=artifact_id,
                    )
                    invalid_code = self._offer_error(
                        offer, source=source, supplier=supplier, snapshot_id=snapshot_id,
                        artifact_id=artifact_id, observed_at=page.observed_at,
                    )
                    if invalid_code is not None:
                        quarantined.append(QuarantinedRow(row_number, artifact_id,
                                                          row_digest, invalid_code))
                        continue
                    assert isinstance(offer, CatalogOffer)
                    if offer.source_record_key in seen_source_keys:
                        quarantined.append(QuarantinedRow(row_number, artifact_id,
                                                          row_digest, "duplicate_source_record_key"))
                        continue
                    seen_source_keys.add(offer.source_record_key)
                    page_offers.append(offer)
                except Exception:
                    quarantined.append(QuarantinedRow(row_number, artifact_id,
                                                      row_digest, "normalization_invalid"))

            page_accepted = bool(page_offers)
            content_digest = _digest({
                "artifact_digest": artifact.content_digest,
                "cursor": cursor,
                "next_cursor": page.next_cursor,
                "source_revision": page.source_revision,
                "observed_at": page.observed_at.isoformat(),
                "offers": [self._offer_payload(offer) for offer in page_offers],
                "quarantined_rows": [asdict(row) for row in quarantined],
                "accepted": page_accepted,
            })
            snapshot = CatalogSnapshot(
                snapshot_id=snapshot_id,
                tenant_id=source.tenant_id or "",
                owner_id=source.canonical_owner_id or "",
                source_id=source.source_id or "",
                supplier_id=supplier.supplier_id or "",
                run_id=run_id,
                artifact_id=artifact_id,
                schema_version=source.schema_version or "",
                source_revision=page.source_revision,
                observed_at=page.observed_at,
                freshness=FreshnessState.FRESH,
                offers=tuple(page_offers),
                content_digest=content_digest,
                accepted=page_accepted,
                quarantined_rows=tuple(quarantined),
                project_id=source.project_id,
            )
            next_page_count = page_count + 1
            next_accepted_count = accepted_count + len(page_offers)
            next_observed_count = observed_count + len(page.records)
            next_quarantined_count = quarantined_count + len(quarantined)
            next_artifacts = tuple((*artifact_ids, artifact_id))
            next_snapshots = tuple((*snapshot_ids, snapshot_id))
            next_status = (
                IngestionRunStatus.SUCCEEDED if page.next_cursor is None
                else IngestionRunStatus.PARTIAL if page_index + 1 == self._max_pages
                else IngestionRunStatus.RUNNING
            )
            next_error_code = "page_budget_exhausted" if next_status is IngestionRunStatus.PARTIAL else None
            run_record = IngestionRun(
                run_id=run_id, source=source, supplier=supplier, status=next_status,
                checkpoint_cursor=page.next_cursor, page_count=next_page_count,
                record_count=next_accepted_count, artifact_ids=next_artifacts,
                snapshot_ids=next_snapshots, started_at=run_started,
                updated_at=page.observed_at, error_code=next_error_code,
                observed_count=next_observed_count,
                quarantined_count=next_quarantined_count,
            )
            try:
                outcome = _page_commit_outcome(await self._evidence.commit_page(
                    artifact=artifact, payload=raw, snapshot=snapshot,
                    run=run_record, expected_checkpoint=expected_checkpoint,
                ))
            except Exception as exc:
                # A durable adapter may fail after some offer batches landed. The
                # page never reached its terminal state, so the run must not be
                # reported as running or succeeded; resume uses the same cursor.
                return await self._source_failed(
                    run_id, source, supplier, cursor, page_count, accepted_count,
                    observed_count, quarantined_count, artifact_ids, snapshot_ids,
                    run_started, latest_update, _exception_reason(exc), page.observed_at,
                )
            if outcome.result is PageCommitResult.COMMITTED_INCOMPLETE:
                # The page is durable, but a post-commit requirement failed. The
                # checkpoint advanced, so resume continues after this page; the
                # run is failed with the adapter's reason and is never succeeded.
                return await self._committed_incomplete(
                    run_id=run_id, source=source, supplier=supplier,
                    cursor=page.next_cursor, page_count=next_page_count,
                    accepted_count=next_accepted_count, observed_count=next_observed_count,
                    quarantined_count=next_quarantined_count, artifact_ids=next_artifacts,
                    snapshot_ids=next_snapshots, started_at=run_started,
                    at=page.observed_at, code=outcome.reason_code,
                )
            if outcome.result is PageCommitResult.PARTIALLY_COMMITTED:
                return await self._source_failed(
                    run_id, source, supplier, cursor, page_count, accepted_count,
                    observed_count, quarantined_count, artifact_ids, snapshot_ids,
                    run_started, latest_update,
                    _specific_reason(outcome, "page_commit_partially_written"),
                    page.observed_at,
                )
            if outcome.result not in (PageCommitResult.COMMITTED, PageCommitResult.REPLAYED):
                code = _specific_reason(outcome, (
                    "checkpoint_cas_conflict"
                    if outcome.result is PageCommitResult.CHECKPOINT_CONFLICT
                    else "immutable_page_replay_conflict"
                ))
                return self._blocked(run_id, source, supplier, started, code, prior=prior)

            page_count, accepted_count = next_page_count, next_accepted_count
            observed_count, quarantined_count = next_observed_count, next_quarantined_count
            artifact_ids.append(artifact_id)
            snapshot_ids.append(snapshot_id)
            cursor = page.next_cursor
            expected_checkpoint = page.next_cursor
            latest_update = page.observed_at
            prior = run_record
            if cursor is None:
                return run_record

        return IngestionRun(
            run_id=run_id, source=source, supplier=supplier,
            status=IngestionRunStatus.PARTIAL, checkpoint_cursor=cursor,
            page_count=page_count, record_count=accepted_count,
            artifact_ids=tuple(artifact_ids), snapshot_ids=tuple(snapshot_ids),
            started_at=run_started, updated_at=latest_update,
            error_code="page_budget_exhausted", observed_count=observed_count,
            quarantined_count=quarantined_count,
        )

    async def record_failure(self, *, source_id: str, reason: str,
                             at: datetime | None = None) -> tuple[CatalogSnapshot | None, FreshnessState]:
        observed = at or datetime.now(UTC)
        _aware(observed)
        await self._evidence.mark_source_failure(source_id, at=observed, reason=reason)
        last_good = await self._evidence.read_last_good(source_id)
        if last_good is None:
            return None, FreshnessState.UNKNOWN
        return replace(last_good, freshness=FreshnessState.STALE), FreshnessState.STALE

    async def _source_failed(self, run_id: str, source: SourceIdentity,
                             supplier: SupplierIdentity, cursor: str | None,
                             page_count: int, accepted_count: int, observed_count: int,
                             quarantined_count: int, artifact_ids: Sequence[str],
                             snapshot_ids: Sequence[str], started_at: datetime,
                             updated_at: datetime, code: str, at: datetime) -> IngestionRun:
        _aware(at)
        if source.source_id:
            await self.record_failure(source_id=source.source_id, reason=code, at=at)
        failed_run = IngestionRun(
            run_id=run_id, source=source, supplier=supplier,
            status=IngestionRunStatus.FAILED, checkpoint_cursor=cursor,
            page_count=page_count, record_count=accepted_count,
            artifact_ids=tuple(artifact_ids), snapshot_ids=tuple(snapshot_ids),
            started_at=started_at, updated_at=at, error_code=code,
            observed_count=observed_count, quarantined_count=quarantined_count,
        )
        recorded = await self._evidence.record_run_failure(
            run=failed_run, expected_checkpoint=cursor,
        )
        if not recorded:
            return replace(failed_run, status=IngestionRunStatus.BLOCKED,
                           error_code="checkpoint_cas_conflict")
        return failed_run

    async def _committed_incomplete(
        self, *, run_id: str, source: SourceIdentity, supplier: SupplierIdentity,
        cursor: str | None, page_count: int, accepted_count: int, observed_count: int,
        quarantined_count: int, artifact_ids: Sequence[str],
        snapshot_ids: Sequence[str], started_at: datetime, at: datetime, code: str,
    ) -> IngestionRun:
        """Fail the run after a committed page whose post-commit step failed.

        The source is not at fault, so no source-failure marker is written. The
        run record is failed at the *advanced* checkpoint: the committed page is
        evidence and must not be re-ingested, while the run stays non-terminal
        until the post-commit step is repaired from that committed page.
        """
        _aware(at)
        failed_run = IngestionRun(
            run_id=run_id, source=source, supplier=supplier,
            status=IngestionRunStatus.FAILED, checkpoint_cursor=cursor,
            page_count=page_count, record_count=accepted_count,
            artifact_ids=tuple(artifact_ids), snapshot_ids=tuple(snapshot_ids),
            started_at=started_at, updated_at=at, error_code=code,
            observed_count=observed_count, quarantined_count=quarantined_count,
        )
        recorded = await self._evidence.record_run_failure(
            run=failed_run, expected_checkpoint=cursor,
        )
        if not recorded:
            return build_blocked_run(run_id=run_id, source=source, supplier=supplier,
                                     reason_code="checkpoint_cas_conflict", now=at,
                                     prior=failed_run)
        return failed_run

    @staticmethod
    def _offer_payload(offer: CatalogOffer) -> Mapping[str, Any]:
        return {
            "offer_id": offer.offer_id, "snapshot_id": offer.snapshot_id,
            "supplier_id": offer.supplier_id, "source_id": offer.source_id,
            "source_record_key": offer.source_record_key, "artifact_id": offer.artifact_id,
            "observed_at": offer.observed_at.isoformat(), "currency": offer.currency,
            "price": offer.price, "availability": offer.availability,
            "attributes": offer.attributes, "provenance_digest": offer.provenance_digest,
        }

    @staticmethod
    def _offer_error(offer: Any, *, source: SourceIdentity, supplier: SupplierIdentity,
                     snapshot_id: str, artifact_id: str, observed_at: datetime) -> str | None:
        if not isinstance(offer, CatalogOffer):
            return "normalizer_contract_invalid"
        if offer.source_id != source.source_id or offer.supplier_id != supplier.supplier_id:
            return "source_supplier_reference_mismatch"
        if offer.snapshot_id != snapshot_id or offer.artifact_id != artifact_id:
            return "snapshot_artifact_reference_mismatch"
        if not offer.source_record_key or not offer.source_record_key.strip():
            return "source_record_reference_missing"
        if offer.offer_id != offer_identity(offer.source_id, offer.supplier_id,
                                            offer.source_record_key):
            return "offer_identity_not_deterministic"
        if offer.observed_at != observed_at:
            return "offer_observed_at_mismatch"
        if not _CURRENCY_RE.fullmatch(offer.currency):
            return "currency_invalid"
        if offer.availability not in _ALLOWED_AVAILABILITY:
            return "availability_invalid"
        if not _valid_digest(offer.provenance_digest):
            return "source_provenance_invalid"
        try:
            _digest(EquipmentIngestionService._offer_payload(offer))
        except (TypeError, ValueError):
            return "offer_attributes_not_serializable"
        expected_provenance = offer_provenance_digest(
            source_id=offer.source_id, supplier_id=offer.supplier_id,
            source_record_key=offer.source_record_key, artifact_id=artifact_id,
            observed_at=observed_at,
        )
        if offer.provenance_digest != expected_provenance:
            return "source_provenance_mismatch"
        return None

    @staticmethod
    def _blocked(run_id: str, source: SourceIdentity, supplier: SupplierIdentity,
                 now: datetime, code: str, *, prior: IngestionRun | None = None) -> IngestionRun:
        return build_blocked_run(run_id=run_id, source=source, supplier=supplier,
                                 reason_code=code, now=now, prior=prior)


def _specific_reason(outcome: PageCommitOutcome, fallback: str) -> str:
    """Prefer an adapter-supplied redacted reason over the generic code."""
    if outcome.reason_code and outcome.reason_code != outcome.result.value:
        return outcome.reason_code
    return fallback


def _exception_reason(exc: BaseException) -> str:
    """Accept a redacted reason code from a durable adapter, else stay generic."""
    code = getattr(exc, "reason_code", None)
    if isinstance(code, str) and _REASON_CODE_RE.fullmatch(code):
        return code
    return "page_commit_failed"


def normalize_page_commit_outcome(value: Any) -> PageCommitOutcome:
    """Public normalizer for evidence adapters that wrap another adapter."""
    return _page_commit_outcome(value)


def build_blocked_run(*, run_id: str, source: SourceIdentity, supplier: SupplierIdentity,
                      reason_code: str, now: datetime,
                      prior: IngestionRun | None = None) -> IngestionRun:
    """Build a fail-closed run that carries the prior committed evidence."""
    _aware(now)
    if not isinstance(reason_code, str) or not _REASON_CODE_RE.fullmatch(reason_code):
        raise ValueError("blocked run reason_code must be a redacted code")
    return IngestionRun(
        run_id=run_id, source=source, supplier=supplier,
        status=IngestionRunStatus.BLOCKED,
        checkpoint_cursor=prior.checkpoint_cursor if prior else None,
        page_count=prior.page_count if prior else 0,
        record_count=prior.record_count if prior else 0,
        artifact_ids=prior.artifact_ids if prior else (),
        snapshot_ids=prior.snapshot_ids if prior else (),
        started_at=prior.started_at if prior else now, updated_at=now,
        error_code=reason_code,
        observed_count=prior.observed_count if prior else 0,
        quarantined_count=prior.quarantined_count if prior else 0,
    )


__all__ = [
    "CatalogOffer", "CatalogProjection", "CatalogSnapshot", "EquipmentIngestionService",
    "FreshnessState", "IngestionEvidencePort", "IngestionRun", "IngestionRunStatus",
    "PageCommitOutcome", "PageCommitResult", "ProjectionContract", "ProjectionRebuildPort",
    "QuarantinedRow", "RawArtifact", "SourceAdapter", "SourceIdentity", "SourcePage",
    "SupplierIdentity", "build_blocked_run", "build_catalog_projection", "evidence_digest",
    "normalize_page_commit_outcome", "offer_identity", "offer_provenance_digest",
]