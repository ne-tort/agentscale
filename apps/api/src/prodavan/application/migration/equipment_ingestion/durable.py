"""Durable equipment ingestion evidence over the ``equipment_v2_*`` tables.

This module is the concrete :class:`IngestionEvidencePort` for the migration
ingestion contract. It is deliberately co-located with the application contract
because the migration file zone only admits ``equipment_ingestion/**``; the
intended long-term home is ``infrastructure/persistence/equipment_ingestion/``.
Moving it must not change the port semantics. It imports the existing ORM rows
read-only and never edits models or Alembic revisions.

Schema reality (revision ``2026110305``) constrains the adapter:

* ``equipment_v2_ingestion_runs`` has no window, digest, supplier, or cursor
  column. The run id is therefore the idempotency key materialized in the
  primary key: ``eir_<window token>_<source digest token>``. The window token
  makes "same window, different digest" detectable by an indexed prefix lookup,
  and that case fails closed instead of creating a second run.
* ``equipment_v2_catalog_snapshots`` has no tenant/owner/supplier/accepted
  column and only a 40-character id. Page evidence therefore lives in
  ``metadata_json`` (tenant, owner, supplier, project, artifact digest and
  reference, cursor/checkpoint, page index, counters, quarantined row digests)
  while ``id`` is a deterministic 36-character digest of the logical snapshot
  id produced by the ingestion service.
* ``equipment_v2_catalog_offers`` requires ``title``, ``unit_price`` and
  ``available_quantity`` although the normalized offer has no quantity and its
  title lives in ``attributes``. Availability, the original price string, the
  offer identity and the provenance digest are stored under the reserved
  ``$ingestion`` attribute namespace; quantity is written as the documented
  column default ``0`` and is never read as stock truth. Sources without a
  mapped title or price fail closed instead of inventing one.
* There is no raw-artifact table or blob store. ``commit_page`` verifies the
  payload digest against ``artifact.content_digest`` and records the artifact
  identity/reference in snapshot metadata, but it cannot retain the bytes. That
  gap is a reported blocker, not simulated storage.
* ``equipment_v2`` has no ``project_id``/``cabinet_id`` columns. ``project_id``
  is carried as snapshot evidence and enforced by adapter comparisons; it is
  not a database scope, RLS predicate, or project-partitioned parity claim.

Every page commit is one transaction: run-row row lock, checkpoint CAS, page
snapshot, streamed offer batches with ``ON CONFLICT (snapshot_id,
external_key) DO NOTHING``, and the terminal run status in the same
transaction as the last batch. Exact replay returns ``REPLAYED`` without
writing; changed bytes or changed offer evidence for the same page returns a
typed conflict and never overwrites accepted evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import Insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    FreshnessState,
    IngestionRun,
    IngestionRunStatus,
    PageCommitOutcome,
    PageCommitResult,
    QuarantinedRow,
    RawArtifact,
    SourceIdentity,
    SupplierIdentity,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)
from prodavan.application.migration.equipment_ingestion.indexing import (
    IndexMarker,
    IndexingState,
    index_marker_id,
)
from prodavan.infrastructure.persistence.models.equipment_v2 import (
    EquipmentCatalogOfferRow,
    EquipmentCatalogSnapshotRow,
    EquipmentIngestionRunRow,
)

_REDACTED_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

RUN_ID_PREFIX = "eir_"
FAILURE_MARKER_PREFIX = "eir_fail_"
SNAPSHOT_ROW_PREFIX = "ecs_"
OFFER_ROW_PREFIX = "eco_"

#: Reserved JSONB key that carries normalized-offer evidence the table lacks.
OFFER_EVIDENCE_NAMESPACE = "$ingestion"
METADATA_VERSION = 1

_TENANT_ID_LIMIT = 64
_SOURCE_ID_LIMIT = 120
_SNAPSHOT_HASH_LIMIT = 128
_EXTERNAL_KEY_LIMIT = 240
_SUPPLIER_KEY_LIMIT = 160
_TITLE_LIMIT = 500
_UNIT = Decimal("0.0001")
_MAX_PRICE = Decimal("1" + "0" * 14)

_TERMINAL_RUN_STATUSES = frozenset({
    IngestionRunStatus.SUCCEEDED,
    IngestionRunStatus.FAILED,
    IngestionRunStatus.BLOCKED,
})
_RECORDED_FAILURE_STATUSES = frozenset({
    IngestionRunStatus.FAILED,
    IngestionRunStatus.BLOCKED,
})


class DurableEvidenceError(Exception):
    """Redacted, fail-closed durable evidence error.

    ``reason_code`` is the only text the orchestration may surface; it never
    contains payload, credential, supplier or row text.
    """

    def __init__(self, reason_code: str) -> None:
        if not isinstance(reason_code, str) or not _REDACTED_CODE_RE.fullmatch(reason_code):
            raise ValueError("durable evidence reason_code must be a redacted code")
        self.reason_code = reason_code
        super().__init__(reason_code)


class _PageConflict(Exception):
    """Internal typed conflict that must roll the page transaction back."""

    def __init__(self, code: str) -> None:
        if not _REDACTED_CODE_RE.fullmatch(code):
            raise ValueError("page conflict code must be redacted")
        self.code = code
        super().__init__(code)


class RunAdmissionState(StrEnum):
    CREATED = "created"
    RESUMED = "resumed"
    REPLAYS = "replays"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """Idempotent admission of one ``(source, window, digest)`` ingest run."""

    state: RunAdmissionState
    run_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RunAdmissionState):
            raise ValueError("run admission requires a typed state")
        if self.reason_code is not None and not _REDACTED_CODE_RE.fullmatch(self.reason_code):
            raise ValueError("run admission reason_code must be a redacted code")
        if self.state is RunAdmissionState.BLOCKED and not self.reason_code:
            raise ValueError("a blocked admission requires a reason code")
        if self.state in (RunAdmissionState.CREATED, RunAdmissionState.RESUMED,
                          RunAdmissionState.REPLAYS) and not self.run_id:
            raise ValueError("an admitted run requires a run id")


@dataclass(frozen=True, slots=True)
class IngestionWindow:
    """Half-open source window that bounds one idempotent ingest run."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _aware(self.start)
        _aware(self.end)
        if not self.start < self.end:
            raise ValueError("ingestion window must be a positive interval")

    def canonical(self) -> dict[str, str]:
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
        }

    @property
    def token(self) -> str:
        return _digest(self.canonical())[7:23]


@dataclass(frozen=True, slots=True)
class StoredPage:
    """One committed page snapshot row and its JSONB evidence."""

    row_id: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StoredTotals:
    pages: int
    accepted: int
    quarantined: int
    observed: int


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        _aware(value)
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def _json_safe(value: Any, *, code: str) -> Any:
    try:
        return json.loads(json.dumps(value, default=_json_default, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise DurableEvidenceError(code) from exc


def run_row_id(*, tenant_id: str, source_id: str, window: IngestionWindow,
               source_digest: str) -> str:
    """Deterministic run primary key: window token plus declared digest token."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant identity is required")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source identity is required")
    if not isinstance(window, IngestionWindow):
        raise ValueError("an ingestion window is required")
    if not isinstance(source_digest, str) or not _SHA256_RE.fullmatch(source_digest):
        raise ValueError("source_digest must be a sha256 digest")
    return f"{RUN_ID_PREFIX}{window.token}_{source_digest[7:23]}"


def run_window_prefix(*, tenant_id: str, source_id: str, window: IngestionWindow) -> str:
    """Indexed prefix that finds every run admitted for the same window."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant identity is required")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source identity is required")
    if not isinstance(window, IngestionWindow):
        raise ValueError("an ingestion window is required")
    window_token = _digest({"tenant_id": tenant_id, "source_id": source_id,
                            **window.canonical()})[7:23]
    return f"{RUN_ID_PREFIX}{window_token}_"


def failure_marker_id(*, tenant_id: str, source_id: str, at: datetime,
                      reason_code: str) -> str:
    """Append-only durable source-failure marker (never a resumable run)."""
    _aware(at)
    if not _REDACTED_CODE_RE.fullmatch(reason_code):
        raise ValueError("failure marker reason_code must be a redacted code")
    token = _digest({"tenant_id": tenant_id, "source_id": source_id,
                     "at": at.astimezone(UTC).isoformat(),
                     "reason_code": reason_code})[7:31]
    return f"{FAILURE_MARKER_PREFIX}{token}"


def snapshot_row_id(snapshot_id: str) -> str:
    """Bounded row id for a logical snapshot id produced by the service."""
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("snapshot identity is required")
    return f"{SNAPSHOT_ROW_PREFIX}{_digest({'snapshot_id': snapshot_id})[7:39]}"


def offer_row_id(offer_id: str) -> str:
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise ValueError("offer identity is required")
    return f"{OFFER_ROW_PREFIX}{_digest({'offer_id': offer_id})[7:39]}"


def resolve_admission(*, computed_run_id: str, window_run_ids: Sequence[str],
                      stored_status: IngestionRunStatus | None,
                      stored_pages: int) -> RunAdmission:
    """Pure admission decision over rows already read for the same window."""
    if not isinstance(computed_run_id, str) or not computed_run_id:
        raise ValueError("computed run id is required")
    if stored_pages < 0:
        raise ValueError("stored page count cannot be negative")
    window_ids = tuple(window_run_ids)
    if computed_run_id in window_ids:
        if stored_status is None:
            raise DurableEvidenceError("run_state_inconsistent")
        if stored_status is IngestionRunStatus.SUCCEEDED:
            if stored_pages < 1:
                return RunAdmission(RunAdmissionState.CONFLICT, computed_run_id,
                                    "run_state_inconsistent")
            return RunAdmission(RunAdmissionState.REPLAYS, computed_run_id)
        return RunAdmission(RunAdmissionState.RESUMED, computed_run_id)
    if window_ids:
        # The same window is already bound to another declared digest. That is a
        # conflict, never a second run and never an overwrite of evidence.
        return RunAdmission(RunAdmissionState.CONFLICT, computed_run_id,
                            "run_window_digest_conflict")
    return RunAdmission(RunAdmissionState.CREATED, computed_run_id)


def run_status(value: str) -> IngestionRunStatus:
    try:
        return IngestionRunStatus(value)
    except ValueError as exc:
        raise DurableEvidenceError("run_status_unknown") from exc


def offer_row_values(*, snapshot_row: str, offer: CatalogOffer) -> dict[str, Any]:
    """Map one normalized offer to a bounded ``equipment_v2_catalog_offers`` row."""
    if not isinstance(offer, CatalogOffer):
        raise DurableEvidenceError("offer_contract_invalid")
    attributes = _json_safe(dict(offer.attributes), code="offer_attributes_not_serializable")
    if OFFER_EVIDENCE_NAMESPACE in attributes:
        raise DurableEvidenceError("offer_attributes_reserved_key")
    title = attributes.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DurableEvidenceError("offer_title_missing")
    title = title.strip()
    if len(title) > _TITLE_LIMIT:
        raise DurableEvidenceError("offer_title_too_long")
    if len(offer.source_record_key) > _EXTERNAL_KEY_LIMIT:
        raise DurableEvidenceError("offer_external_key_too_long")
    if len(offer.supplier_id) > _SUPPLIER_KEY_LIMIT:
        raise DurableEvidenceError("offer_supplier_key_too_long")
    if offer.price is None:
        raise DurableEvidenceError("offer_price_missing")
    try:
        price = Decimal(offer.price)
    except (InvalidOperation, ValueError) as exc:
        raise DurableEvidenceError("offer_price_invalid") from exc
    if not price.is_finite() or price < 0 or price >= _MAX_PRICE:
        raise DurableEvidenceError("offer_price_out_of_range")
    if price != price.quantize(_UNIT):
        raise DurableEvidenceError("offer_price_scale_invalid")
    return {
        "id": offer_row_id(offer.offer_id),
        "snapshot_id": snapshot_row,
        "supplier_key": offer.supplier_id,
        "external_key": offer.source_record_key,
        "title": title,
        "unit_price": price,
        "currency": offer.currency,
        # The normalized offer carries availability, not a quantity. The column
        # default is written and never interpreted as stock truth.
        "available_quantity": Decimal(0),
        "status": "active",
        "attributes": {
            **attributes,
            OFFER_EVIDENCE_NAMESPACE: {
                "availability": offer.availability,
                "offer_id": offer.offer_id,
                "price": offer.price,
                "provenance_digest": offer.provenance_digest,
            },
        },
    }


def offer_from_row(row: EquipmentCatalogOfferRow, *, snapshot_id: str, source_id: str,
                   artifact_id: str, observed_at: datetime) -> CatalogOffer:
    """Rebuild the normalized offer from a stored row, failing closed."""
    _aware(observed_at)
    attributes = dict(row.attributes or {})
    envelope = attributes.pop(OFFER_EVIDENCE_NAMESPACE, None)
    if not isinstance(envelope, Mapping):
        raise DurableEvidenceError("offer_attributes_evidence_missing")
    expected_offer_id = offer_identity(source_id, row.supplier_key, row.external_key)
    if envelope.get("offer_id") != expected_offer_id:
        raise DurableEvidenceError("offer_identity_mismatch")
    expected_provenance = offer_provenance_digest(
        source_id=source_id, supplier_id=row.supplier_key,
        source_record_key=row.external_key, artifact_id=artifact_id,
        observed_at=observed_at,
    )
    if envelope.get("provenance_digest") != expected_provenance:
        raise DurableEvidenceError("offer_provenance_mismatch")
    price = envelope.get("price")
    if not isinstance(price, str) or not price:
        raise DurableEvidenceError("offer_price_evidence_missing")
    try:
        if Decimal(price) != row.unit_price:
            raise DurableEvidenceError("offer_price_evidence_mismatch")
    except (InvalidOperation, ValueError) as exc:
        raise DurableEvidenceError("offer_price_evidence_invalid") from exc
    return CatalogOffer(
        offer_id=expected_offer_id,
        snapshot_id=snapshot_id,
        supplier_id=row.supplier_key,
        source_id=source_id,
        source_record_key=row.external_key,
        observed_at=observed_at,
        currency=row.currency,
        price=price,
        availability=envelope.get("availability"),
        attributes=attributes,
        provenance_digest=expected_provenance,
        artifact_id=artifact_id,
    )


def page_metadata(*, snapshot: CatalogSnapshot, artifact: RawArtifact, run: IngestionRun,
                  page_index: int, cursor: str | None) -> dict[str, Any]:
    """Build the JSONB page evidence committed with one snapshot row."""
    if page_index < 0:
        raise ValueError("page index cannot be negative")
    if snapshot.run_id != run.run_id:
        raise DurableEvidenceError("snapshot_run_mismatch")
    if snapshot.artifact_id != artifact.artifact_id or artifact.run_id != run.run_id:
        raise DurableEvidenceError("snapshot_artifact_mismatch")
    if snapshot.source_id != artifact.source_id:
        raise DurableEvidenceError("artifact_source_mismatch")
    if (run.source.source_id, run.source.tenant_id, run.source.canonical_owner_id,
            run.source.project_id, run.supplier.supplier_id) != (
            snapshot.source_id, snapshot.tenant_id, snapshot.owner_id,
            snapshot.project_id, snapshot.supplier_id):
        raise DurableEvidenceError("snapshot_identity_mismatch")
    if run.page_count != page_index + 1:
        raise DurableEvidenceError("run_page_count_mismatch")
    if run.checkpoint_cursor == cursor and run.checkpoint_cursor is not None:
        raise DurableEvidenceError("page_checkpoint_did_not_advance")
    return {
        "metadata_version": METADATA_VERSION,
        "snapshot_key": snapshot.snapshot_id,
        "tenant_id": snapshot.tenant_id,
        "owner_id": snapshot.owner_id,
        "source_id": snapshot.source_id,
        "supplier_id": snapshot.supplier_id,
        "source_supplier_key": run.supplier.source_supplier_key,
        "supplier_display_name": run.supplier.display_name,
        "supplier_verified": bool(run.supplier.verified),
        "project_id": snapshot.project_id,
        "schema_version": snapshot.schema_version,
        "source_kind": run.source.source_kind,
        "source_revision": snapshot.source_revision,
        "accepted": bool(snapshot.accepted),
        "freshness": snapshot.freshness.value,
        "observed_at": snapshot.observed_at.astimezone(UTC).isoformat(),
        "content_digest": snapshot.content_digest,
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "source_id": artifact.source_id,
            "run_id": artifact.run_id,
            "content_digest": artifact.content_digest,
            "byte_length": artifact.byte_length,
            "media_type": artifact.media_type,
            "immutable": bool(artifact.immutable),
            "retained_reference": artifact.retained_reference,
        },
        "page": {
            "page_index": page_index,
            "cursor": cursor,
            "next_cursor": run.checkpoint_cursor,
            "page_count": run.page_count,
            "record_count": run.record_count,
            "observed_count": run.observed_count,
            "quarantined_count": run.quarantined_count,
        },
        "run_status": run.status.value,
        "run_checkpoint": run.checkpoint_cursor,
        "quarantined_rows": [
            {"row_number": item.row_number, "record_digest": item.record_digest,
             "error_code": item.error_code}
            for item in snapshot.quarantined_rows
        ],
    }


def page_chain(pages: Sequence[StoredPage]) -> tuple[StoredPage, ...]:
    """Validate and order committed page evidence by contiguous page index."""
    ordered = sorted(pages, key=lambda item: item.metadata.get("page", {}).get("page_index", -1)) \
        if pages else []
    seen: set[int] = set()
    for item in ordered:
        metadata = item.metadata
        if not isinstance(metadata, Mapping) or metadata.get("metadata_version") != METADATA_VERSION:
            raise DurableEvidenceError("snapshot_metadata_invalid")
        page = metadata.get("page")
        index = page.get("page_index") if isinstance(page, Mapping) else None
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise DurableEvidenceError("snapshot_page_index_invalid")
        if index in seen:
            raise DurableEvidenceError("snapshot_page_index_duplicate")
        seen.add(index)
    chain = tuple(ordered)
    if tuple(item.metadata["page"]["page_index"] for item in chain) != tuple(range(len(chain))):
        raise DurableEvidenceError("snapshot_page_sequence_broken")
    return chain


def stored_totals(chain: Sequence[StoredPage]) -> StoredTotals:
    accepted = quarantined = observed = 0
    for item in chain:
        page = item.metadata["page"]
        accepted += int(page["record_count"])
        quarantined += int(page["quarantined_count"])
        observed += int(page["observed_count"])
    if accepted + quarantined != observed:
        raise DurableEvidenceError("snapshot_counters_inconsistent")
    return StoredTotals(pages=len(chain), accepted=accepted,
                        quarantined=quarantined, observed=observed)


def artifact_from_page(page: StoredPage) -> RawArtifact:
    metadata = page.metadata
    artifact = metadata.get("artifact")
    if not isinstance(artifact, Mapping):
        raise DurableEvidenceError("snapshot_artifact_evidence_missing")
    try:
        return RawArtifact(
            artifact_id=artifact["artifact_id"],
            source_id=artifact["source_id"],
            run_id=artifact["run_id"],
            content_digest=artifact["content_digest"],
            byte_length=artifact["byte_length"],
            media_type=artifact["media_type"],
            immutable=artifact["immutable"],
            retained_reference=artifact["retained_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DurableEvidenceError("snapshot_artifact_evidence_invalid") from exc


def snapshot_from_page(page: StoredPage, offers: Sequence[CatalogOffer],
                       quarantined: Sequence[QuarantinedRow] = ()) -> CatalogSnapshot:
    metadata = page.metadata
    try:
        return CatalogSnapshot(
            snapshot_id=metadata["snapshot_key"],
            tenant_id=metadata["tenant_id"],
            owner_id=metadata["owner_id"],
            source_id=metadata["source_id"],
            supplier_id=metadata["supplier_id"],
            run_id=metadata["artifact"]["run_id"],
            artifact_id=metadata["artifact"]["artifact_id"],
            schema_version=metadata["schema_version"],
            source_revision=metadata.get("source_revision"),
            observed_at=datetime.fromisoformat(metadata["observed_at"]),
            freshness=FreshnessState(metadata["freshness"]),
            offers=tuple(offers),
            content_digest=metadata["content_digest"],
            accepted=bool(metadata["accepted"]),
            quarantined_rows=tuple(quarantined),
            project_id=metadata.get("project_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DurableEvidenceError("snapshot_metadata_invalid") from exc


def quarantined_from_page(page: StoredPage) -> tuple[QuarantinedRow, ...]:
    rows = page.metadata.get("quarantined_rows", ())
    if not isinstance(rows, Sequence):
        raise DurableEvidenceError("snapshot_quarantine_evidence_invalid")
    artifact_id = page.metadata.get("artifact", {}).get("artifact_id")
    try:
        return tuple(
            QuarantinedRow(row_number=item["row_number"], artifact_id=artifact_id,
                           record_digest=item["record_digest"], error_code=item["error_code"])
            for item in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DurableEvidenceError("snapshot_quarantine_evidence_invalid") from exc


def build_run(*, row_id: str, tenant_id: str, source_id: str, status_value: str,
              error_code: str | None, started_at: datetime | None,
              created_at: datetime | None, pages: Sequence[StoredPage],
              fallback_now: datetime) -> IngestionRun:
    """Rebuild the resumable run record from committed page evidence."""
    chain = page_chain(pages)
    if not chain:
        raise DurableEvidenceError("run_has_no_committed_pages")
    first = chain[0].metadata
    totals = stored_totals(chain)
    status = run_status(status_value)
    if error_code is not None and not _REDACTED_CODE_RE.fullmatch(error_code):
        raise DurableEvidenceError("run_error_code_invalid")
    if status in (IngestionRunStatus.FAILED, IngestionRunStatus.PARTIAL,
                  IngestionRunStatus.BLOCKED) and not error_code:
        raise DurableEvidenceError("run_error_code_missing")
    observed_at = max(datetime.fromisoformat(item.metadata["observed_at"]) for item in chain)
    return IngestionRun(
        run_id=row_id,
        source=SourceIdentity(
            source_id=source_id,
            source_kind=first.get("source_kind"),
            schema_version=first.get("schema_version"),
            canonical_owner_id=first.get("owner_id"),
            tenant_id=tenant_id,
            project_id=first.get("project_id"),
        ),
        supplier=SupplierIdentity(
            supplier_id=first.get("supplier_id"),
            source_supplier_key=first.get("source_supplier_key"),
            display_name=first.get("supplier_display_name"),
            verified=bool(first.get("supplier_verified")),
        ),
        status=status,
        checkpoint_cursor=chain[-1].metadata["run_checkpoint"],
        page_count=totals.pages,
        record_count=totals.accepted,
        artifact_ids=tuple(item.metadata["artifact"]["artifact_id"] for item in chain),
        snapshot_ids=tuple(item.metadata["snapshot_key"] for item in chain),
        started_at=started_at or created_at or fallback_now,
        updated_at=observed_at,
        error_code=error_code,
        observed_count=totals.observed,
        quarantined_count=totals.quarantined,
    )


def batch_rows(rows: Sequence[Mapping[str, Any]], size: int) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    """Split offer rows into bounded write batches; order is preserved."""
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError("batch size must be a positive integer")
    return tuple(tuple(rows[index:index + size]) for index in range(0, len(rows), size))


def offer_insert_statement(rows: Sequence[Mapping[str, Any]]) -> Insert:
    """Bounded offer insert that never overwrites an existing external key."""
    if not rows:
        raise ValueError("offer insert requires at least one row")
    return (
        pg_insert(EquipmentCatalogOfferRow)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["snapshot_id", "external_key"])
    )


def _offer_fingerprint(row: Mapping[str, Any]) -> str:
    return _digest({
        "supplier_key": row.get("supplier_key"),
        "title": row.get("title"),
        "unit_price": str(row.get("unit_price")),
        "currency": row.get("currency"),
        "attributes": row.get("attributes"),
    })


def _row_fingerprint(row: EquipmentCatalogOfferRow) -> str:
    return _offer_fingerprint({
        "supplier_key": row.supplier_key,
        "title": row.title,
        "unit_price": row.unit_price,
        "currency": row.currency,
        "attributes": row.attributes,
    })


def _conflict_code(*, stored_rows: Sequence[EquipmentCatalogOfferRow],
                   incoming: Sequence[Mapping[str, Any]]) -> str:
    """Distinguish a per-key offer collision from a whole-page replay conflict."""
    stored = {row.external_key: _row_fingerprint(row) for row in stored_rows}
    for row in incoming:
        key = row.get("external_key")
        if key in stored and stored[key] != _offer_fingerprint(row):
            return "offer_external_key_conflict"
    return "immutable_page_replay_conflict"


def _incoming_fingerprints(snapshot_row: str,
                           offers: Sequence[CatalogOffer]) -> list[dict[str, Any]]:
    """Fingerprint incoming offers without letting a bad row mask the conflict."""
    rows: list[dict[str, Any]] = []
    for offer in offers:
        try:
            rows.append(offer_row_values(snapshot_row=snapshot_row, offer=offer))
        except DurableEvidenceError:
            rows.append({
                "external_key": offer.source_record_key,
                "supplier_key": offer.supplier_id,
                "title": None,
                "unit_price": offer.price,
                "currency": offer.currency,
                "attributes": None,
            })
    return rows


class DurableEquipmentIngestionStore:
    """Tenant-scoped, transactional :class:`IngestionEvidencePort` implementation."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], tenant_id: str,
                 offer_batch_size: int = 500,
                 clock: Callable[[], datetime] | None = None) -> None:
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or len(tenant_id) > _TENANT_ID_LIMIT):
            raise ValueError("a bounded tenant scope is required")
        if (not isinstance(offer_batch_size, int) or isinstance(offer_batch_size, bool)
                or not 1 <= offer_batch_size <= 5000):
            raise ValueError("offer_batch_size must be between 1 and 5000")
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._offer_batch_size = offer_batch_size
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # -- run admission ---------------------------------------------------

    async def admit_run(self, *, source: SourceIdentity, supplier: SupplierIdentity,
                        window: IngestionWindow, source_digest: str,
                        now: datetime | None = None) -> RunAdmission:
        """Create or re-admit the one run for ``(tenant, source, window, digest)``."""
        if not isinstance(window, IngestionWindow):
            raise ValueError("an ingestion window is required")
        if not isinstance(source, SourceIdentity) or not source.complete:
            return RunAdmission(RunAdmissionState.BLOCKED, None,
                                "canonical_source_owner_identity_incomplete")
        if not isinstance(supplier, SupplierIdentity) or not supplier.complete:
            return RunAdmission(RunAdmissionState.BLOCKED, None,
                                "canonical_supplier_identity_incomplete")
        if source.tenant_id != self._tenant_id:
            return RunAdmission(RunAdmissionState.BLOCKED, None, "tenant_scope_mismatch")
        if len(source.source_id or "") > _SOURCE_ID_LIMIT:
            return RunAdmission(RunAdmissionState.BLOCKED, None, "source_id_too_long")
        try:
            run_id = run_row_id(tenant_id=self._tenant_id, source_id=source.source_id or "",
                                window=window, source_digest=source_digest)
            prefix = run_window_prefix(tenant_id=self._tenant_id,
                                       source_id=source.source_id or "", window=window)
        except ValueError:
            return RunAdmission(RunAdmissionState.BLOCKED, None, "source_digest_invalid")
        started = now or self._clock()
        _aware(started)

        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": prefix},
            )
            rows = (await session.scalars(
                select(EquipmentIngestionRunRow)
                .where(EquipmentIngestionRunRow.id.like(f"{prefix}%"))
                .order_by(EquipmentIngestionRunRow.id)
            )).all()
            existing = next((row for row in rows if row.id == run_id), None)
            pages = await self._page_rows(session, run_id) if existing is not None else ()
            decision = resolve_admission(
                computed_run_id=run_id,
                window_run_ids=tuple(row.id for row in rows),
                stored_status=run_status(existing.status) if existing is not None else None,
                stored_pages=len(pages),
            )
            if decision.state is not RunAdmissionState.CREATED:
                return decision
            await session.execute(
                pg_insert(EquipmentIngestionRunRow).values(
                    id=run_id,
                    tenant_id=self._tenant_id,
                    source_id=source.source_id,
                    status=IngestionRunStatus.RUNNING.value,
                    item_count=0,
                    error_message=None,
                    started_at=started,
                    finished_at=None,
                ).on_conflict_do_nothing(index_elements=["id"])
            )
            winner = await session.scalar(select(EquipmentIngestionRunRow).where(
                EquipmentIngestionRunRow.id == run_id).with_for_update())
            if winner is None:
                raise DurableEvidenceError("run_row_missing")
            pages = await self._page_rows(session, run_id)
            if pages or winner.status == IngestionRunStatus.SUCCEEDED.value:
                return resolve_admission(
                    computed_run_id=run_id, window_run_ids=(run_id,),
                    stored_status=run_status(winner.status), stored_pages=len(pages),
                )
            return decision

    # -- reads -----------------------------------------------------------

    async def load_run(self, run_id: str) -> IngestionRun | None:
        """Resumable run state; ``None`` when no page evidence exists yet."""
        if not isinstance(run_id, str) or not run_id:
            return None
        async with self._session_factory() as session:
            row = await session.get(EquipmentIngestionRunRow, run_id)
            if row is None or row.tenant_id != self._tenant_id:
                return None
            pages = await self._page_rows(session, run_id)
        if not pages:
            return None
        return build_run(
            row_id=row.id, tenant_id=row.tenant_id, source_id=row.source_id,
            status_value=row.status, error_code=row.error_message,
            started_at=row.started_at, created_at=row.created_at, pages=pages,
            fallback_now=self._clock(),
        )

    async def read_last_good(self, source_id: str) -> CatalogSnapshot | None:
        """Latest accepted immutable snapshot for the source, read-only."""
        if not isinstance(source_id, str) or not source_id:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EquipmentCatalogSnapshotRow)
                .join(EquipmentIngestionRunRow,
                      EquipmentIngestionRunRow.id == EquipmentCatalogSnapshotRow.ingestion_run_id)
                .where(
                    EquipmentIngestionRunRow.tenant_id == self._tenant_id,
                    EquipmentCatalogSnapshotRow.source_id == source_id,
                    EquipmentCatalogSnapshotRow.metadata_json["accepted"].astext == "true",
                )
                .order_by(EquipmentCatalogSnapshotRow.captured_at.desc(),
                          EquipmentCatalogSnapshotRow.id)
                .limit(1)
            )
            if row is None:
                return None
            offers = await self._offer_rows(session, row.id)
        page = StoredPage(row_id=row.id, metadata=row.metadata_json)
        return snapshot_from_page(
            page, self._offers_from_rows(page, offers),
            quarantined_from_page(page),
        )

    async def load_snapshot_evidence(
        self, snapshot_id: str,
    ) -> tuple[CatalogSnapshot, RawArtifact] | None:
        """Committed page snapshot plus its artifact evidence, for repair re-indexing."""
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return None
        row_id = snapshot_row_id(snapshot_id)
        async with self._session_factory() as session:
            row = await session.get(EquipmentCatalogSnapshotRow, row_id)
            if row is None:
                return None
            run = await session.get(EquipmentIngestionRunRow, row.ingestion_run_id)
            if run is None or run.tenant_id != self._tenant_id:
                return None
            offers = await self._offer_rows(session, row_id)
        page = StoredPage(row_id=row.id, metadata=row.metadata_json)
        snapshot = snapshot_from_page(
            page, self._offers_from_rows(page, offers), quarantined_from_page(page),
        )
        if snapshot.snapshot_id != snapshot_id:
            raise DurableEvidenceError("snapshot_identity_mismatch")
        return snapshot, artifact_from_page(page)

    # -- writes ----------------------------------------------------------

    async def commit_page(self, *, artifact: RawArtifact, payload: bytes,
                          snapshot: CatalogSnapshot, run: IngestionRun,
                          expected_checkpoint: str | None) -> PageCommitOutcome:
        """One transaction: run lock, CAS, snapshot, offer batches, run status."""
        self._validate_page(artifact=artifact, payload=payload, snapshot=snapshot, run=run)
        offers = tuple(snapshot.offers)
        snapshot_row = snapshot_row_id(snapshot.snapshot_id)
        try:
            async with self._session_factory() as session, session.begin():
                row = await session.scalar(select(EquipmentIngestionRunRow).where(
                    EquipmentIngestionRunRow.id == run.run_id).with_for_update())
                if row is None:
                    await session.execute(
                        pg_insert(EquipmentIngestionRunRow).values(
                            id=run.run_id,
                            tenant_id=self._tenant_id,
                            source_id=snapshot.source_id,
                            status=IngestionRunStatus.RUNNING.value,
                            item_count=0,
                            error_message=None,
                            started_at=run.started_at,
                            finished_at=None,
                        ).on_conflict_do_nothing(index_elements=["id"])
                    )
                    row = await session.scalar(select(EquipmentIngestionRunRow).where(
                        EquipmentIngestionRunRow.id == run.run_id).with_for_update())
                if row is None:
                    raise DurableEvidenceError("run_row_missing")
                if row.tenant_id != self._tenant_id or row.source_id != snapshot.source_id:
                    return PageCommitOutcome(PageCommitResult.CONFLICT, "run_scope_conflict",
                                             offers_conflicted=len(offers))

                chain = page_chain(await self._page_rows(session, run.run_id))
                if chain:
                    first = chain[0].metadata
                    if (first.get("tenant_id"), first.get("owner_id"),
                            first.get("supplier_id"), first.get("source_kind"),
                            first.get("schema_version"), first.get("project_id")) != (
                            snapshot.tenant_id, snapshot.owner_id, snapshot.supplier_id,
                            run.source.source_kind, snapshot.schema_version,
                            snapshot.project_id):
                        return PageCommitOutcome(PageCommitResult.CONFLICT,
                                                 "run_identity_conflict",
                                                 offers_conflicted=len(offers))

                existing = await session.get(EquipmentCatalogSnapshotRow, snapshot_row)
                if existing is not None:
                    stored_offers = await self._offer_rows(session, snapshot_row)
                    if self._page_matches(existing=existing, snapshot=snapshot,
                                          artifact=artifact, expected_checkpoint=expected_checkpoint,
                                          stored_offers=stored_offers):
                        return PageCommitOutcome(PageCommitResult.REPLAYED, "page_replayed",
                                                 offers_replayed=len(offers))
                    return PageCommitOutcome(
                        PageCommitResult.CONFLICT,
                        _conflict_code(
                            stored_rows=stored_offers,
                            incoming=_incoming_fingerprints(snapshot_row, offers),
                        ),
                        offers_conflicted=len(offers),
                    )

                totals = stored_totals(chain)
                page_index = totals.pages
                if (run.page_count != page_index + 1
                        or run.record_count != totals.accepted + len(offers)
                        or run.quarantined_count != totals.quarantined + len(snapshot.quarantined_rows)
                        or run.observed_count != totals.observed + len(offers)
                        + len(snapshot.quarantined_rows)):
                    return PageCommitOutcome(PageCommitResult.CONFLICT,
                                             "run_counter_mismatch",
                                             offers_conflicted=len(offers))
                stored_checkpoint = chain[-1].metadata["run_checkpoint"] if chain else None
                if stored_checkpoint != expected_checkpoint:
                    return PageCommitOutcome(PageCommitResult.CHECKPOINT_CONFLICT,
                                             "checkpoint_stale",
                                             offers_conflicted=len(offers))

                metadata = page_metadata(snapshot=snapshot, artifact=artifact, run=run,
                                         page_index=page_index, cursor=expected_checkpoint)
                try:
                    await session.execute(pg_insert(EquipmentCatalogSnapshotRow).values(
                        id=snapshot_row,
                        ingestion_run_id=run.run_id,
                        source_id=snapshot.source_id,
                        content_hash=snapshot.content_digest,
                        captured_at=snapshot.observed_at,
                        metadata_json=metadata,
                    ))
                except IntegrityError as exc:
                    raise _PageConflict("snapshot_content_conflict") from exc

                rows = [offer_row_values(snapshot_row=snapshot_row, offer=offer)
                        for offer in offers]
                written = 0
                for batch in batch_rows(rows, self._offer_batch_size):
                    inserted = (await session.execute(
                        offer_insert_statement(batch).returning(EquipmentCatalogOfferRow.id)
                    )).scalars().all()
                    if len(inserted) != len(batch):
                        # ON CONFLICT DO NOTHING skipped a key that the pre-check
                        # could not see; the whole page transaction rolls back.
                        raise _PageConflict("offer_external_key_conflict")
                    written += len(inserted)

                # Terminal run status is written by the same transaction that
                # wrote the last offer batch of this page.
                await self._write_run_row(session, run)
            return PageCommitOutcome(PageCommitResult.COMMITTED, "page_committed",
                                     offers_written=written)
        except _PageConflict as conflict:
            return PageCommitOutcome(PageCommitResult.CONFLICT, conflict.code,
                                     offers_conflicted=len(offers))
        except IntegrityError as exc:
            raise DurableEvidenceError("page_integrity_conflict") from exc

    async def record_run_failure(self, *, run: IngestionRun,
                                 expected_checkpoint: str | None) -> bool:
        """CAS the failure onto the stored run; false means stale observation."""
        if not isinstance(run, IngestionRun):
            raise ValueError("a run record is required")
        if run.status not in _RECORDED_FAILURE_STATUSES:
            return False
        if run.error_code is not None and not _REDACTED_CODE_RE.fullmatch(run.error_code):
            return False
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(EquipmentIngestionRunRow).where(
                EquipmentIngestionRunRow.id == run.run_id).with_for_update())
            if row is None:
                if expected_checkpoint is not None or run.page_count:
                    return False
                await session.execute(
                    pg_insert(EquipmentIngestionRunRow).values(
                        id=run.run_id,
                        tenant_id=self._tenant_id,
                        source_id=run.source.source_id or "",
                        status=run.status.value,
                        item_count=run.observed_count,
                        error_message=run.error_code,
                        started_at=run.started_at,
                        finished_at=run.updated_at,
                    ).on_conflict_do_nothing(index_elements=["id"])
                )
                return True
            if row.tenant_id != self._tenant_id:
                return False
            chain = page_chain(await self._page_rows(session, run.run_id))
            stored_checkpoint = chain[-1].metadata["run_checkpoint"] if chain else None
            totals = stored_totals(chain)
            if (stored_checkpoint != expected_checkpoint or totals.pages != run.page_count
                    or totals.accepted != run.record_count
                    or totals.quarantined != run.quarantined_count):
                return False
            await self._write_run_row(session, run)
            return True

    async def mark_source_failure(self, source_id: str, *, at: datetime,
                                  reason: str) -> None:
        """Append a durable failure marker without touching accepted snapshots."""
        _aware(at)
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source identity is required")
        code = reason if isinstance(reason, str) and _REDACTED_CODE_RE.fullmatch(reason) \
            else "source_failure_unclassified"
        marker = failure_marker_id(tenant_id=self._tenant_id, source_id=source_id,
                                   at=at, reason_code=code)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                pg_insert(EquipmentIngestionRunRow).values(
                    id=marker,
                    tenant_id=self._tenant_id,
                    source_id=source_id,
                    status=IngestionRunStatus.FAILED.value,
                    item_count=0,
                    error_message=code,
                    started_at=at,
                    finished_at=at,
                ).on_conflict_do_nothing(index_elements=["id"])
            )

    # -- indexing evidence ledger ----------------------------------------

    async def read_index_marker(self, *, snapshot_id: str, content_digest: str,
                                projection_name: str, projection_version: str
                                ) -> IndexMarker | None:
        """Durable post-commit indexing evidence for one immutable page."""
        marker_id = index_marker_id(snapshot_id=snapshot_id, content_digest=content_digest,
                                    projection_name=projection_name,
                                    projection_version=projection_version)
        async with self._session_factory() as session:
            row = await session.get(EquipmentIngestionRunRow, marker_id)
        if row is None or row.tenant_id != self._tenant_id:
            return None
        try:
            state = IndexingState(row.status)
        except ValueError as exc:
            raise DurableEvidenceError("index_marker_state_unknown") from exc
        return IndexMarker(
            marker_id=row.id,
            snapshot_id=snapshot_id,
            content_digest=content_digest,
            source_id=row.source_id,
            projection_name=projection_name,
            projection_version=projection_version,
            state=state,
            document_count=row.item_count,
            recorded_at=row.finished_at or row.started_at or row.created_at,
            reason_code=row.error_message,
        )

    async def record_index_marker(self, marker: IndexMarker) -> None:
        """Append-only indexing evidence; repeats of the same marker are no-ops."""
        if not isinstance(marker, IndexMarker):
            raise ValueError("an index marker is required")
        async with self._session_factory() as session, session.begin():
            await session.execute(
                pg_insert(EquipmentIngestionRunRow).values(
                    id=marker.marker_id,
                    tenant_id=self._tenant_id,
                    source_id=marker.source_id,
                    status=marker.state.value,
                    item_count=marker.document_count,
                    error_message=marker.reason_code,
                    started_at=marker.recorded_at,
                    finished_at=marker.recorded_at,
                ).on_conflict_do_nothing(index_elements=["id"])
            )

    async def list_run_pages(self, run_id: str) -> tuple[StoredPage, ...]:
        """Committed and contiguous page evidence for a run, in page order."""
        if not isinstance(run_id, str) or not run_id:
            return ()
        async with self._session_factory() as session:
            row = await session.get(EquipmentIngestionRunRow, run_id)
            if row is None or row.tenant_id != self._tenant_id:
                return ()
            return page_chain(await self._page_rows(session, run_id))

    async def mark_run_succeeded(self, *, run_id: str, at: datetime) -> bool:
        """Promote a run only when the source was fully consumed from page evidence."""
        _aware(at)
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(EquipmentIngestionRunRow).where(
                EquipmentIngestionRunRow.id == run_id).with_for_update())
            if row is None or row.tenant_id != self._tenant_id:
                return False
            if row.status == IngestionRunStatus.SUCCEEDED.value:
                return True
            chain = page_chain(await self._page_rows(session, run_id))
            if not chain or chain[-1].metadata["run_checkpoint"] is not None:
                return False
            await session.execute(
                update(EquipmentIngestionRunRow)
                .where(EquipmentIngestionRunRow.id == run_id,
                       EquipmentIngestionRunRow.tenant_id == self._tenant_id)
                .values(status=IngestionRunStatus.SUCCEEDED.value, error_message=None,
                        item_count=stored_totals(chain).observed, finished_at=at)
            )
            return True

    async def mark_run_failed(self, *, run_id: str, error_code: str,
                              at: datetime) -> bool:
        """Record a non-terminal run outcome without touching committed evidence."""
        _aware(at)
        if not isinstance(error_code, str) or not _REDACTED_CODE_RE.fullmatch(error_code):
            raise ValueError("run failure code must be a redacted code")
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(EquipmentIngestionRunRow).where(
                EquipmentIngestionRunRow.id == run_id).with_for_update())
            if row is None or row.tenant_id != self._tenant_id:
                return False
            if row.status == IngestionRunStatus.SUCCEEDED.value:
                return False
            await session.execute(
                update(EquipmentIngestionRunRow)
                .where(EquipmentIngestionRunRow.id == run_id,
                       EquipmentIngestionRunRow.tenant_id == self._tenant_id)
                .values(status=IngestionRunStatus.FAILED.value, error_message=error_code,
                        finished_at=at)
            )
            return True

    # -- internals -------------------------------------------------------

    @staticmethod
    def _validate_page(*, artifact: RawArtifact, payload: bytes,
                       snapshot: CatalogSnapshot, run: IngestionRun) -> None:
        if not isinstance(payload, bytes):
            raise DurableEvidenceError("page_payload_invalid")
        if hashlib.sha256(payload).hexdigest() != artifact.content_digest.removeprefix("sha256:"):
            raise DurableEvidenceError("page_payload_digest_mismatch")
        if artifact.byte_length != len(payload):
            raise DurableEvidenceError("page_payload_length_mismatch")
        if not artifact.immutable:
            raise DurableEvidenceError("page_artifact_not_immutable")
        if (snapshot.artifact_id, snapshot.run_id, snapshot.source_id) != (
                artifact.artifact_id, artifact.run_id, artifact.source_id):
            raise DurableEvidenceError("page_artifact_provenance_mismatch")
        if run.run_id != snapshot.run_id:
            raise DurableEvidenceError("page_run_mismatch")
        if run.status not in (IngestionRunStatus.SUCCEEDED, IngestionRunStatus.RUNNING,
                              IngestionRunStatus.PARTIAL):
            raise DurableEvidenceError("page_commit_run_not_active")
        if run.error_code is not None and not _REDACTED_CODE_RE.fullmatch(run.error_code):
            raise DurableEvidenceError("run_error_code_invalid")

    def _page_matches(self, *, existing: EquipmentCatalogSnapshotRow,
                      snapshot: CatalogSnapshot, artifact: RawArtifact,
                      expected_checkpoint: str | None,
                      stored_offers: Sequence[EquipmentCatalogOfferRow]) -> bool:
        metadata = existing.metadata_json or {}
        page = metadata.get("page") or {}
        stored_artifact = metadata.get("artifact") or {}
        if metadata.get("snapshot_key") != snapshot.snapshot_id:
            return False
        if existing.content_hash != snapshot.content_digest:
            return False
        if page.get("cursor") != expected_checkpoint:
            return False
        if stored_artifact.get("content_digest") != artifact.content_digest:
            return False
        if stored_artifact.get("byte_length") != artifact.byte_length:
            return False
        if stored_artifact.get("retained_reference") != artifact.retained_reference:
            return False
        incoming = {offer.source_record_key: offer for offer in snapshot.offers}
        stored = {row.external_key: row for row in stored_offers}
        if set(incoming) != set(stored):
            return False
        for key, offer in incoming.items():
            row = stored[key]
            values = offer_row_values(snapshot_row=existing.id, offer=offer)
            if row.unit_price != values["unit_price"] or row.currency != values["currency"]:
                return False
            if row.title != values["title"] or row.supplier_key != values["supplier_key"]:
                return False
            if dict(row.attributes or {}) != values["attributes"]:
                return False
        return True

    async def _write_run_row(self, session: AsyncSession, run: IngestionRun) -> None:
        if run.error_code is not None and not _REDACTED_CODE_RE.fullmatch(run.error_code):
            raise DurableEvidenceError("run_error_code_invalid")
        await session.execute(
            update(EquipmentIngestionRunRow)
            .where(EquipmentIngestionRunRow.id == run.run_id,
                   EquipmentIngestionRunRow.tenant_id == self._tenant_id)
            .values(
                status=run.status.value,
                item_count=run.observed_count,
                error_message=run.error_code,
                started_at=run.started_at,
                finished_at=run.updated_at if run.status in _TERMINAL_RUN_STATUSES else None,
            )
        )

    async def _page_rows(self, session: AsyncSession, run_id: str) -> tuple[StoredPage, ...]:
        rows = (await session.scalars(
            select(EquipmentCatalogSnapshotRow)
            .where(EquipmentCatalogSnapshotRow.ingestion_run_id == run_id)
            .order_by(EquipmentCatalogSnapshotRow.captured_at, EquipmentCatalogSnapshotRow.id)
        )).all()
        return tuple(StoredPage(row_id=row.id, metadata=row.metadata_json or {}) for row in rows)

    @staticmethod
    async def _offer_rows(session: AsyncSession,
                          snapshot_row: str) -> tuple[EquipmentCatalogOfferRow, ...]:
        return tuple((await session.scalars(
            select(EquipmentCatalogOfferRow)
            .where(EquipmentCatalogOfferRow.snapshot_id == snapshot_row)
            .order_by(EquipmentCatalogOfferRow.external_key)
        )).all())

    @staticmethod
    def _offers_from_rows(page: StoredPage,
                          rows: Sequence[EquipmentCatalogOfferRow]) -> tuple[CatalogOffer, ...]:
        metadata = page.metadata
        snapshot_id = metadata["snapshot_key"]
        observed_at = datetime.fromisoformat(metadata["observed_at"])
        artifact_id = metadata["artifact"]["artifact_id"]
        return tuple(
            offer_from_row(row, snapshot_id=snapshot_id,
                           source_id=metadata["source_id"], artifact_id=artifact_id,
                           observed_at=observed_at)
            for row in rows
        )


def is_storable_offer(offer: CatalogOffer) -> bool:
    """True when the normalized offer satisfies the bounded column contract."""
    try:
        offer_row_values(snapshot_row="ecs_pending", offer=offer)
    except DurableEvidenceError:
        return False
    return True


__all__ = [
    "DurableEquipmentIngestionStore",
    "DurableEvidenceError",
    "IngestionWindow",
    "METADATA_VERSION",
    "OFFER_EVIDENCE_NAMESPACE",
    "RunAdmission",
    "RunAdmissionState",
    "StoredPage",
    "StoredTotals",
    "artifact_from_page",
    "batch_rows",
    "build_run",
    "failure_marker_id",
    "is_storable_offer",
    "offer_from_row",
    "offer_insert_statement",
    "offer_row_id",
    "offer_row_values",
    "page_chain",
    "page_metadata",
    "quarantined_from_page",
    "resolve_admission",
    "run_row_id",
    "run_status",
    "run_window_prefix",
    "snapshot_from_page",
    "snapshot_row_id",
    "stored_totals",
]
