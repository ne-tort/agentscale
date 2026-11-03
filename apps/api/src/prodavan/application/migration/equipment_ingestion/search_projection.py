"""Pure searchable equipment projection builder; no OpenSearch or persistence I/O.

The ingestion contract has immutable snapshot/artifact evidence but deliberately
has no equipment-search field mapping. Callers must provide that mapping through
``SearchDocumentFieldAdapter``. The existing catalog indexer's company/catalog
identity is also not interchangeable with migration tenant/owner identity, so
this module returns a projection only and does not implement a writer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    FreshnessState,
    RawArtifact,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ProjectionScope:
    tenant_id: str
    owner_id: str
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("projection tenant_id is required")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("projection owner_id is required")
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("projection project_id must be absent or nonempty")


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """Snapshot, immutable artifact, and optional read-time freshness overlay."""

    snapshot: CatalogSnapshot
    artifact: RawArtifact
    freshness: FreshnessState | None = None


@dataclass(frozen=True, slots=True)
class SearchDocumentFields:
    """Explicit source-to-search mapping; no product fields are inferred."""

    title: str
    part_number: str | None = None
    brand: str | None = None
    supplier: str | None = None
    lead_time: str | None = None


class SearchDocumentFieldAdapter(Protocol):
    """Maps known source data to searchable fields; absent mappings stay absent."""

    def fields_for(
        self, offer: CatalogOffer, *, snapshot: CatalogSnapshot
    ) -> SearchDocumentFields: ...


class AcceptedSnapshotEvidencePort(Protocol):
    """Read-only port to accepted snapshots and their immutable raw artifacts."""

    async def accepted_snapshot_evidence(
        self, *, scope: ProjectionScope
    ) -> Sequence[SnapshotEvidence]: ...


class PublicationDecision(StrEnum):
    PUBLISH = "publish"
    REPLAY = "replay"
    GENERATION_CONFLICT = "generation_conflict"
    NO_SEARCHABLE_DOCUMENTS = "no_searchable_documents"


@dataclass(frozen=True, slots=True)
class SearchProjectionDocument:
    document_id: str
    source: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QuarantinedProjectionInput:
    snapshot_id: str | None
    artifact_id: str | None
    offer_id: str | None
    reason_code: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class EquipmentSearchProjection:
    projection_name: str
    projection_version: str
    scope: ProjectionScope
    generation: int
    generation_id: str
    source_snapshot_ids: tuple[str, ...]
    source_digest: str
    content_digest: str
    idempotency_key: str
    document_count: int
    documents: tuple[SearchProjectionDocument, ...]
    quarantined: tuple[QuarantinedProjectionInput, ...]
    rebuilt_at: datetime


@dataclass(frozen=True, slots=True)
class LastGoodProjectionPointer:
    """Authoritative pointer value; planning a publish never advances it."""

    scope: ProjectionScope
    projection_name: str
    projection_version: str
    generation: int
    generation_id: str
    source_digest: str
    content_digest: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ProjectionPublicationPlan:
    decision: PublicationDecision
    expected_generation: int
    candidate_pointer: LastGoodProjectionPointer | None
    current_pointer: LastGoodProjectionPointer | None


class ProjectionPublicationPort(Protocol):
    """Unimplemented integration boundary for atomic generation publication.

    The adapter reads the authoritative pointer and durably materializes a
    complete immutable generation before advancing it with generation and
    accepted-source-digest CAS. A conflict/failure returns no new pointer; a
    partial generation must never replace last-good.
    """

    async def read_last_good_pointer(
        self,
        *,
        scope: ProjectionScope,
        projection_name: str,
        projection_version: str,
    ) -> LastGoodProjectionPointer | None: ...

    async def publish_complete_generation(
        self,
        projection: EquipmentSearchProjection,
        *,
        expected_generation: int,
        expected_source_digest: str,
    ) -> LastGoodProjectionPointer | None: ...


async def build_search_projection_from_port(
    *,
    source: AcceptedSnapshotEvidencePort,
    scope: ProjectionScope,
    field_adapter: SearchDocumentFieldAdapter,
    projection_name: str,
    projection_version: str,
    expected_generation: int,
    rebuilt_at: datetime,
) -> EquipmentSearchProjection:
    """Acquire through the narrow read port and invoke the pure projection builder."""
    evidence = await source.accepted_snapshot_evidence(scope=scope)
    return build_search_projection(
        evidence=evidence,
        scope=scope,
        field_adapter=field_adapter,
        projection_name=projection_name,
        projection_version=projection_version,
        expected_generation=expected_generation,
        rebuilt_at=rebuilt_at,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _scope_payload(scope: ProjectionScope) -> dict[str, str | None]:
    return {
        "tenant_id": scope.tenant_id,
        "owner_id": scope.owner_id,
        "project_id": scope.project_id,
    }


def _quarantine(
    evidence: SnapshotEvidence,
    *,
    reason_code: str,
    offer: CatalogOffer | None = None,
) -> QuarantinedProjectionInput:
    snapshot = evidence.snapshot
    snapshot_id = getattr(snapshot, "snapshot_id", None)
    artifact_id = getattr(evidence.artifact, "artifact_id", None)
    offer_id = getattr(offer, "offer_id", None)
    record_digest = evidence_digest(
        {
            "reason_code": reason_code,
            "snapshot_id": snapshot_id,
            "snapshot_digest": getattr(snapshot, "content_digest", None),
            "tenant_id": getattr(snapshot, "tenant_id", None),
            "owner_id": getattr(snapshot, "owner_id", None),
            "project_id": getattr(snapshot, "project_id", None),
            "snapshot_source_id": getattr(snapshot, "source_id", None),
            "snapshot_supplier_id": getattr(snapshot, "supplier_id", None),
            "run_id": getattr(snapshot, "run_id", None),
            "snapshot_observed_at": getattr(snapshot, "observed_at", None),
            "snapshot_freshness": getattr(getattr(snapshot, "freshness", None), "value", None),
            "read_freshness": getattr(getattr(evidence, "freshness", None), "value", None),
            "snapshot_accepted": getattr(snapshot, "accepted", None),
            "artifact_id": artifact_id,
            "artifact_digest": getattr(evidence.artifact, "content_digest", None),
            "raw_ref": getattr(evidence.artifact, "retained_reference", None),
            "artifact_immutable": getattr(evidence.artifact, "immutable", None),
            "artifact_byte_length": getattr(evidence.artifact, "byte_length", None),
            "offer_id": offer_id,
            "source_id": getattr(offer, "source_id", None),
            "supplier_id": getattr(offer, "supplier_id", None),
            "source_record_key": getattr(offer, "source_record_key", None),
            "provenance_digest": getattr(offer, "provenance_digest", None),
        }
    )
    return QuarantinedProjectionInput(
        snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
        artifact_id=artifact_id if isinstance(artifact_id, str) else None,
        offer_id=offer_id if isinstance(offer_id, str) else None,
        reason_code=reason_code,
        record_digest=record_digest,
    )


def _snapshot_error(
    evidence: SnapshotEvidence, scope: ProjectionScope
) -> str | None:
    snapshot, artifact = evidence.snapshot, evidence.artifact
    required = (
        snapshot.snapshot_id,
        snapshot.tenant_id,
        snapshot.owner_id,
        snapshot.source_id,
        snapshot.supplier_id,
        snapshot.run_id,
        snapshot.artifact_id,
        snapshot.schema_version,
        snapshot.content_digest,
        artifact.artifact_id,
        artifact.source_id,
        artifact.run_id,
        artifact.content_digest,
        artifact.retained_reference,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required):
        return "snapshot_or_artifact_identity_incomplete"
    if (
        not _SHA256_PATTERN.fullmatch(artifact.content_digest)
        or not _SHA256_PATTERN.fullmatch(snapshot.content_digest)
    ):
        return "snapshot_or_artifact_identity_incomplete"
    if (not isinstance(artifact.byte_length, int) or isinstance(artifact.byte_length, bool)
            or artifact.byte_length < 0 or not artifact.immutable):
        return "artifact_integrity_invalid"
    if snapshot.tenant_id != scope.tenant_id:
        return "tenant_scope_mismatch"
    if snapshot.owner_id != scope.owner_id:
        return "owner_scope_mismatch"
    if snapshot.project_id != scope.project_id:
        return "project_scope_mismatch"
    if (
        snapshot.artifact_id != artifact.artifact_id
        or snapshot.source_id != artifact.source_id
        or snapshot.run_id != artifact.run_id
    ):
        return "artifact_provenance_mismatch"
    if not snapshot.accepted:
        return "snapshot_not_accepted"
    freshness = evidence.freshness if evidence.freshness is not None else snapshot.freshness
    if not isinstance(freshness, FreshnessState):
        return "snapshot_freshness_invalid"
    try:
        _utc(snapshot.observed_at)
    except (AttributeError, TypeError, ValueError):
        return "snapshot_observed_at_invalid"
    return None


def _normal_fields(value: Any) -> SearchDocumentFields:
    if not isinstance(value, SearchDocumentFields):
        raise ValueError("field adapter returned an unsupported value")
    if not isinstance(value.title, str) or not value.title.strip():
        raise ValueError("search title is required")
    for item in (value.part_number, value.brand, value.supplier, value.lead_time):
        if item is not None and not isinstance(item, str):
            raise ValueError("search fields must be strings or absent")
    if value.part_number is not None and not value.part_number.strip():
        raise ValueError("part number cannot be empty when supplied")
    if value.brand is not None and not value.brand.strip():
        raise ValueError("brand cannot be empty when supplied")
    if value.supplier is not None and not value.supplier.strip():
        raise ValueError("supplier cannot be empty when supplied")
    if value.lead_time is not None and not value.lead_time.strip():
        raise ValueError("lead time cannot be empty when supplied")
    return SearchDocumentFields(
        title=value.title.strip(),
        part_number=value.part_number.strip() if value.part_number else None,
        brand=value.brand.strip() if value.brand else None,
        supplier=value.supplier.strip() if value.supplier else None,
        lead_time=value.lead_time.strip() if value.lead_time else None,
    )


def _availability_in_stock(value: str) -> bool | None:
    if value == "in_stock":
        return True
    if value == "out_of_stock":
        return False
    return None


def _business_payload(
    offer: CatalogOffer, fields: SearchDocumentFields
) -> dict[str, Any]:
    return {
        "part_number": fields.part_number or "",
        "title": fields.title,
        "brand": fields.brand or "",
        "supplier": fields.supplier or "",
        "lead_time": fields.lead_time or "",
        "price": offer.price,
        "currency": offer.currency,
        "availability": offer.availability,
    }


def _document_payload(item: SearchProjectionDocument) -> dict[str, Any]:
    return {"document_id": item.document_id, "source": dict(item.source)}


def _document(
    evidence: SnapshotEvidence,
    offer: CatalogOffer,
    fields: SearchDocumentFields,
    scope: ProjectionScope,
) -> SearchProjectionDocument:
    snapshot, artifact = evidence.snapshot, evidence.artifact
    price_num: float | None = None
    if offer.price is not None:
        try:
            amount = Decimal(offer.price)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("search price is invalid") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("search price is invalid")
        price_num = float(amount)
        if not math.isfinite(price_num):
            raise ValueError("search price exceeds numeric index range")

    freshness = evidence.freshness if evidence.freshness is not None else snapshot.freshness
    source: dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "owner_id": scope.owner_id,
        "project_id": scope.project_id,
        "offer_id": offer.offer_id,
        "source_id": offer.source_id,
        "supplier_id": offer.supplier_id,
        "source_record_key": offer.source_record_key,
        "snapshot_id": snapshot.snapshot_id,
        "run_id": snapshot.run_id,
        "artifact_id": artifact.artifact_id,
        "raw_ref": artifact.retained_reference,
        "provenance_digest": offer.provenance_digest,
        "observed_at": _timestamp(offer.observed_at),
        "freshness": freshness.value,
        "part_number": fields.part_number or "",
        "title": fields.title,
        "brand": fields.brand or "",
        "supplier": fields.supplier or "",
        "lead_time": fields.lead_time or "",
        "price": offer.price,
        "price_original": offer.price,
        "price_num": price_num,
        "currency": offer.currency,
        "availability": offer.availability,
        "in_stock": _availability_in_stock(offer.availability),
    }
    return SearchProjectionDocument(
        document_id=offer.offer_id,
        source=MappingProxyType(source),
    )


def build_search_projection(
    *,
    evidence: Sequence[SnapshotEvidence],
    scope: ProjectionScope,
    field_adapter: SearchDocumentFieldAdapter,
    projection_name: str,
    projection_version: str,
    expected_generation: int,
    rebuilt_at: datetime,
) -> EquipmentSearchProjection:
    """Build a canonical, tenant-scoped searchable projection without side effects.

    Document IDs are the ingestion offer IDs, which bind source, canonical
    supplier, and source-record identity rather than part number alone. The
    digest covers the full accepted snapshot set, artifact references, selected
    documents, and redacted quarantine dispositions; it excludes generation and
    rebuild time so exact input replay has the same idempotency key.
    """
    if not isinstance(projection_name, str) or not projection_name.strip():
        raise ValueError("projection name and version are required")
    if not isinstance(projection_version, str) or not projection_version.strip():
        raise ValueError("projection name and version are required")
    if not isinstance(expected_generation, int) or isinstance(expected_generation, bool) or expected_generation < 0:
        raise ValueError("expected generation cannot be negative")
    if not isinstance(scope, ProjectionScope):
        raise ValueError("projection scope is required")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise ValueError("projection evidence must be a sequence")
    if not callable(getattr(field_adapter, "fields_for", None)):
        raise ValueError("search field adapter is required")
    normalized_rebuilt_at = _utc(rebuilt_at)

    for item in evidence:
        if not isinstance(item, SnapshotEvidence):
            raise ValueError("projection input must use SnapshotEvidence")
        if not isinstance(item.snapshot, CatalogSnapshot) or not isinstance(item.artifact, RawArtifact):
            raise ValueError("projection input contains invalid evidence types")
    ordered_evidence = tuple(
        sorted(
            evidence,
            key=lambda item: (
                getattr(item.snapshot, "snapshot_id", None)
                if isinstance(getattr(item.snapshot, "snapshot_id", None), str) else "",
                getattr(item.artifact, "artifact_id", None)
                if isinstance(getattr(item.artifact, "artifact_id", None), str) else "",
            ),
        )
    )
    snapshot_ids = [getattr(item.snapshot, "snapshot_id", None) for item in ordered_evidence]
    present_ids = [value for value in snapshot_ids if isinstance(value, str)]
    if len(set(present_ids)) != len(present_ids):
        raise ValueError("projection input contains duplicate snapshot identities")

    quarantined: list[QuarantinedProjectionInput] = []
    usable: list[SnapshotEvidence] = []
    for item in ordered_evidence:
        if not isinstance(item, SnapshotEvidence):
            raise ValueError("projection input must use SnapshotEvidence")
        if not isinstance(item.snapshot, CatalogSnapshot) or not isinstance(item.artifact, RawArtifact):
            raise ValueError("projection input contains invalid evidence types")
        snapshot_error = _snapshot_error(item, scope)
        if snapshot_error == "snapshot_not_accepted":
            raise ValueError("projection input contains an unaccepted snapshot")
        if snapshot_error is not None:
            quarantined.append(_quarantine(item, reason_code=snapshot_error))
            continue
        usable.append(item)

    if not usable and not quarantined:
        raise ValueError("projection requires evidence")

    # Build each valid offer before choosing the latest observation, so a bad
    # newer row cannot silently hide an older searchable offer.
    latest: dict[str, tuple[SnapshotEvidence, CatalogOffer, SearchDocumentFields,
                            SearchProjectionDocument]] = {}
    for item in usable:
        snapshot = item.snapshot
        seen_in_snapshot: set[str] = set()
        for offer in snapshot.offers:
            if not isinstance(offer, CatalogOffer):
                quarantined.append(
                    _quarantine(item, reason_code="offer_contract_invalid")
                )
                continue
            try:
                if (offer.snapshot_id, offer.source_id, offer.supplier_id, offer.artifact_id) != (
                    snapshot.snapshot_id,
                    snapshot.source_id,
                    snapshot.supplier_id,
                    snapshot.artifact_id,
                ):
                    raise ValueError("offer reference mismatch")
                if offer.observed_at != snapshot.observed_at:
                    raise ValueError("offer observation mismatch")
                expected_offer_id = offer_identity(
                    offer.source_id, offer.supplier_id, offer.source_record_key
                )
                if offer.offer_id != expected_offer_id:
                    raise ValueError("offer identity mismatch")
                expected_provenance = offer_provenance_digest(
                    source_id=offer.source_id,
                    supplier_id=offer.supplier_id,
                    source_record_key=offer.source_record_key,
                    artifact_id=offer.artifact_id,
                    observed_at=offer.observed_at,
                )
                if offer.provenance_digest != expected_provenance:
                    raise ValueError("offer provenance mismatch")
                if offer.offer_id in seen_in_snapshot:
                    raise ValueError("duplicate offer identity")
                seen_in_snapshot.add(offer.offer_id)
                fields = _normal_fields(
                    field_adapter.fields_for(offer, snapshot=snapshot)
                )
                document = _document(item, offer, fields, scope)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                quarantined.append(
                    _quarantine(item, reason_code="offer_or_search_fields_invalid", offer=offer)
                )
                continue

            current = latest.get(offer.offer_id)
            if current is None or offer.observed_at > current[1].observed_at:
                latest[offer.offer_id] = (item, offer, fields, document)
                continue
            if offer.observed_at < current[1].observed_at:
                continue
            if _business_payload(offer, fields) != _business_payload(current[1], current[2]):
                raise ValueError("conflicting offer evidence has the same observed time")
            if snapshot.snapshot_id < current[0].snapshot.snapshot_id:
                latest[offer.offer_id] = (item, offer, fields, document)

    documents = tuple(
        sorted((item[3] for item in latest.values()), key=lambda item: item.document_id)
    )
    quarantined_tuple = tuple(
        sorted(
            quarantined,
            key=lambda item: (
                item.snapshot_id or "",
                item.artifact_id or "",
                item.offer_id or "",
                item.reason_code,
                item.record_digest,
            ),
        )
    )
    scoped_snapshots = tuple(sorted(usable, key=lambda item: item.snapshot.snapshot_id))
    source_snapshot_ids = tuple(item.snapshot.snapshot_id for item in scoped_snapshots)
    source_digest = evidence_digest(
        {
            "scope": _scope_payload(scope),
            "snapshots": [
                {
                    "snapshot_id": item.snapshot.snapshot_id,
                    "snapshot_digest": item.snapshot.content_digest,
                    "artifact_id": item.artifact.artifact_id,
                    "artifact_digest": item.artifact.content_digest,
                    "raw_ref": item.artifact.retained_reference,
                    "freshness": (
                        item.freshness if item.freshness is not None
                        else item.snapshot.freshness
                    ).value,
                }
                for item in scoped_snapshots
            ],
            "quarantined": [
                {"reason_code": item.reason_code, "record_digest": item.record_digest}
                for item in quarantined_tuple
            ],
        }
    )
    content_digest = evidence_digest(
        {
            "documents": [
                _document_payload(item)
                for item in documents
            ],
            "quarantined": [
                {
                    "snapshot_id": item.snapshot_id,
                    "artifact_id": item.artifact_id,
                    "offer_id": item.offer_id,
                    "reason_code": item.reason_code,
                    "record_digest": item.record_digest,
                }
                for item in quarantined_tuple
            ],
        }
    )
    idempotency_key = evidence_digest(
        {
            "projection_name": projection_name,
            "projection_version": projection_version,
            "scope": _scope_payload(scope),
            "source_digest": source_digest,
            "content_digest": content_digest,
        }
    )
    generation = expected_generation + 1
    generation_id = evidence_digest(
        {"idempotency_key": idempotency_key, "generation": generation}
    )
    return EquipmentSearchProjection(
        projection_name=projection_name,
        projection_version=projection_version,
        scope=scope,
        generation=generation,
        generation_id=generation_id,
        source_snapshot_ids=source_snapshot_ids,
        source_digest=source_digest,
        content_digest=content_digest,
        idempotency_key=idempotency_key,
        document_count=len(documents),
        documents=documents,
        quarantined=quarantined_tuple,
        rebuilt_at=normalized_rebuilt_at,
    )


def plan_projection_publication(
    *,
    projection: EquipmentSearchProjection,
    current_pointer: LastGoodProjectionPointer | None,
    expected_generation: int,
) -> ProjectionPublicationPlan:
    """Plan CAS publication while retaining the prior pointer until commit.

    An exact replay returns the existing pointer unchanged even if the caller's
    generation token is stale. New content publishes only at current+1; the
    integration adapter must still commit a complete generation atomically.
    """
    if not isinstance(expected_generation, int) or isinstance(expected_generation, bool) or expected_generation < 0:
        raise ValueError("expected generation cannot be negative")
    if projection.generation != expected_generation + 1:
        raise ValueError("projection generation must equal expected_generation + 1")
    if current_pointer is not None:
        same_contract = (
            current_pointer.scope == projection.scope
            and current_pointer.projection_name == projection.projection_name
            and current_pointer.projection_version == projection.projection_version
        )
        if not same_contract:
            return ProjectionPublicationPlan(
                PublicationDecision.GENERATION_CONFLICT,
                expected_generation,
                None,
                current_pointer,
            )
        if current_pointer.idempotency_key == projection.idempotency_key:
            return ProjectionPublicationPlan(
                PublicationDecision.REPLAY,
                expected_generation,
                None,
                current_pointer,
            )
        observed_generation = current_pointer.generation
    else:
        observed_generation = 0

    if not projection.documents or not projection.source_snapshot_ids:
        return ProjectionPublicationPlan(
            PublicationDecision.NO_SEARCHABLE_DOCUMENTS,
            expected_generation,
            None,
            current_pointer,
        )

    if observed_generation != expected_generation:
        return ProjectionPublicationPlan(
            PublicationDecision.GENERATION_CONFLICT,
            expected_generation,
            None,
            current_pointer,
        )

    candidate_pointer = LastGoodProjectionPointer(
        scope=projection.scope,
        projection_name=projection.projection_name,
        projection_version=projection.projection_version,
        generation=projection.generation,
        generation_id=projection.generation_id,
        source_digest=projection.source_digest,
        content_digest=projection.content_digest,
        idempotency_key=projection.idempotency_key,
    )
    return ProjectionPublicationPlan(
        PublicationDecision.PUBLISH,
        expected_generation,
        candidate_pointer,
        current_pointer,
    )


__all__ = [
    "AcceptedSnapshotEvidencePort",
    "EquipmentSearchProjection",
    "LastGoodProjectionPointer",
    "ProjectionPublicationPlan",
    "ProjectionPublicationPort",
    "ProjectionScope",
    "PublicationDecision",
    "QuarantinedProjectionInput",
    "SearchDocumentFieldAdapter",
    "SearchDocumentFields",
    "SearchProjectionDocument",
    "SnapshotEvidence",
    "build_search_projection",
    "build_search_projection_from_port",
    "plan_projection_publication",
]
