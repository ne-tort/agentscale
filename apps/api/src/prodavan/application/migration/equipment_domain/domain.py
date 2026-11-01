"""Fail-closed, deterministic equipment-domain projection and human decisions.

This module defines contracts only. It does not infer ownership, connect to a DB,
or execute procurement/export side effects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, default=lambda item: str(item) if isinstance(item, Decimal) else asdict(item),
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class EquipmentDomainStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DomainEvidence:
    tenant_id: str | None
    owner_id: str | None
    source_id: str | None
    supplier_id: str | None
    source_schema_version: str | None

    @property
    def complete(self) -> bool:
        return all((self.tenant_id, self.owner_id, self.source_id,
                    self.supplier_id, self.source_schema_version))


@dataclass(frozen=True, slots=True)
class TypedRequest:
    request_id: str
    evidence: DomainEvidence
    title: str
    currency: str
    created_at: str

    def __post_init__(self) -> None:
        if not self.request_id or not self.currency:
            raise ValueError("request identity and currency are required")


@dataclass(frozen=True, slots=True)
class RequestLine:
    line_id: str
    request_id: str
    evidence: DomainEvidence
    ordinal: int
    description: str
    quantity: Decimal
    unit: str
    part_number: str | None = None

    def __post_init__(self) -> None:
        if not self.line_id or not self.request_id or self.ordinal < 0 or self.quantity <= 0:
            raise ValueError("line identity, nonnegative ordinal and positive quantity are required")


@dataclass(frozen=True, slots=True)
class CandidateGroup:
    group_id: str
    line_id: str
    evidence: DomainEvidence
    ordinal: int
    match_kind: str
    part_number: str | None
    aliases: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.group_id or not self.line_id or self.ordinal < 0:
            raise ValueError("candidate group identity and nonnegative ordinal are required")
        if self.match_kind not in {"exact", "analog", "doubt"}:
            raise ValueError("unsupported candidate match kind")


@dataclass(frozen=True, slots=True)
class BudgetProjection:
    line_id: str
    tender_id: str
    supplier_id: str
    source_id: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal

    def __post_init__(self) -> None:
        if not all((self.line_id, self.tender_id, self.supplier_id, self.source_id)):
            raise ValueError("budget projection identity is required")
        if self.quantity <= 0 or self.unit_price < 0 or self.total < 0:
            raise ValueError("budget projection values are invalid")


@dataclass(frozen=True, slots=True)
class EquipmentTender:
    tender_id: str
    line_id: str
    group_id: str
    evidence: DomainEvidence
    ordinal: int
    supplier_id: str
    source_id: str
    source_record_id: str
    part_number: str | None
    description: str
    currency: str
    unit_price: Decimal
    available_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        if not all((self.tender_id, self.line_id, self.group_id, self.supplier_id,
                    self.source_id, self.source_record_id, self.currency)):
            raise ValueError("tender and supplier/source identity are required")
        if self.ordinal < 0 or self.unit_price < 0:
            raise ValueError("tender ordinal and price must be nonnegative")


@dataclass(frozen=True, slots=True)
class JsonbAssociation:
    record_id: str
    association_kind: str
    related_ids: tuple[str, ...]
    legacy_json: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AssociationBackfill:
    """Converts JSONB id arrays to typed edges without sorting or deduplicating."""

    associations: tuple[JsonbAssociation, ...]

    def ordered_edges(self) -> tuple[tuple[str, str, int, str], ...]:
        edges: list[tuple[str, str, int, str]] = []
        for association in self.associations:
            for ordinal, related_id in enumerate(association.related_ids):
                if not related_id:
                    raise ValueError("empty association id blocks backfill")
                edges.append((association.record_id, association.association_kind,
                              ordinal, related_id))
        return tuple(edges)

    @classmethod
    def from_jsonb(cls, rows: Sequence[Mapping[str, Any]], *, id_field: str,
                   association_field: str, kind: str) -> "AssociationBackfill":
        result: list[JsonbAssociation] = []
        for row in rows:
            record_id = row.get(id_field)
            value = row.get(association_field)
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("association owner identity missing")
            if value is None:
                related: tuple[str, ...] = ()
            elif isinstance(value, list) and all(isinstance(item, str) and item for item in value):
                related = tuple(value)
            else:
                raise ValueError("JSONB association must be an ordered array of nonempty IDs")
            result.append(JsonbAssociation(record_id, kind, related, dict(row)))
        return cls(tuple(result))


@dataclass(frozen=True, slots=True)
class ProjectionContract:
    name: str
    version: str
    input_digest: str
    deterministic: bool


@dataclass(frozen=True, slots=True)
class EquipmentProjection:
    status: EquipmentDomainStatus
    blocked_reasons: tuple[str, ...]
    requests: tuple[TypedRequest, ...]
    lines: tuple[RequestLine, ...]
    candidate_groups: tuple[CandidateGroup, ...]
    tenders: tuple[EquipmentTender, ...]
    budgets: tuple[BudgetProjection, ...]
    contract: ProjectionContract


@dataclass(frozen=True, slots=True)
class HumanSelection:
    actor_id: str
    line_id: str
    tender_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProcurementExport:
    export_id: str
    actor_id: str
    selection_digest: str
    tender_ids: tuple[str, ...]
    status: str = "prepared"


class HumanDecisionPort(Protocol):
    async def append_selection(self, selection: HumanSelection) -> bool: ...
    async def prepare_procurement_export(self, export: ProcurementExport) -> bool: ...


def build_projection(*, requests: Sequence[TypedRequest], lines: Sequence[RequestLine],
                     candidate_groups: Sequence[CandidateGroup],
                     tenders: Sequence[EquipmentTender]) -> EquipmentProjection:
    """Build stable output, rejecting cross-scope links and identity ambiguity."""
    reqs = tuple(sorted(requests, key=lambda item: item.request_id))
    line_rows = tuple(sorted(lines, key=lambda item: (item.request_id, item.ordinal, item.line_id)))
    groups = tuple(sorted(candidate_groups, key=lambda item: (item.line_id, item.ordinal, item.group_id)))
    tender_rows = tuple(sorted(tenders, key=lambda item: (item.line_id, item.ordinal, item.tender_id)))
    reasons: list[str] = []
    request_by_id = {item.request_id: item for item in reqs}
    line_by_id = {item.line_id: item for item in line_rows}
    group_by_id = {item.group_id: item for item in groups}
    if len(request_by_id) != len(reqs) or len(line_by_id) != len(line_rows):
        reasons.append("duplicate_request_or_line_identity")
    if len(group_by_id) != len(groups):
        reasons.append("duplicate_candidate_group_identity")
    for request in reqs:
        if not request.evidence.complete:
            reasons.append("request_owner_source_evidence_incomplete")
    for line in line_rows:
        request = request_by_id.get(line.request_id)
        if request is None or not line.evidence.complete:
            reasons.append("line_scope_or_evidence_missing")
        elif (line.evidence.tenant_id, line.evidence.owner_id, line.evidence.source_id) != (
                request.evidence.tenant_id, request.evidence.owner_id, request.evidence.source_id):
            reasons.append("line_scope_mismatch")
    for group in groups:
        line = line_by_id.get(group.line_id)
        if line is None or not group.evidence.complete:
            reasons.append("candidate_group_scope_or_evidence_missing")
        elif (group.evidence.tenant_id, group.evidence.owner_id, group.evidence.source_id,
              group.evidence.supplier_id) != (line.evidence.tenant_id, line.evidence.owner_id,
                                               line.evidence.source_id, line.evidence.supplier_id):
            reasons.append("candidate_group_scope_mismatch")
    seen_source_records: set[tuple[str, str, str]] = set()
    for tender in tender_rows:
        line = line_by_id.get(tender.line_id)
        group = group_by_id.get(tender.group_id)
        if line is None or group is None or group.line_id != tender.line_id:
            reasons.append("tender_association_missing_or_cross_line")
            continue
        if not tender.evidence.complete or (tender.supplier_id, tender.source_id) != (
                tender.evidence.supplier_id, tender.evidence.source_id):
            reasons.append("tender_source_supplier_evidence_incomplete")
        if (tender.evidence.tenant_id, tender.evidence.owner_id) != (
                line.evidence.tenant_id, line.evidence.owner_id):
            reasons.append("tender_owner_scope_mismatch")
        key = (tender.supplier_id, tender.source_id, tender.source_record_id)
        if key in seen_source_records:
            reasons.append("duplicate_tender_source_identity")
        seen_source_records.add(key)
    reasons_tuple = tuple(sorted(set(reasons)))
    digest = _stable_digest({
        "requests": reqs, "lines": line_rows, "groups": groups, "tenders": tender_rows,
    })
    ready = not reasons_tuple
    return EquipmentProjection(
        EquipmentDomainStatus.READY if ready else EquipmentDomainStatus.BLOCKED,
        reasons_tuple,
        reqs if ready else (), line_rows if ready else (), groups if ready else (), tender_rows if ready else (),
        tuple(BudgetProjection(t.line_id, t.tender_id, t.supplier_id, t.source_id,
                               line_by_id[t.line_id].quantity, t.unit_price,
                               line_by_id[t.line_id].quantity * t.unit_price)
              for t in tender_rows) if ready else (),
        ProjectionContract("equipment-domain", "1", digest, True),
    )


def select_tender(*, projection: EquipmentProjection, actor_id: str,
                  line_id: str, tender_id: str, reason: str) -> HumanSelection:
    if projection.status is not EquipmentDomainStatus.READY:
        raise ValueError("selection blocked until scoped projection is ready")
    if not actor_id or not reason.strip():
        raise ValueError("human actor and selection reason are required")
    if not any(line.line_id == line_id for line in projection.lines):
        raise ValueError("selection line is outside projection")
    if not any(tender.tender_id == tender_id and tender.line_id == line_id
               for tender in projection.tenders):
        raise ValueError("selection tender is outside line")
    return HumanSelection(actor_id, line_id, tender_id, reason.strip())


def prepare_procurement_export(*, projection: EquipmentProjection, selections: Sequence[HumanSelection],
                               actor_id: str, export_id: str) -> ProcurementExport:
    if projection.status is not EquipmentDomainStatus.READY:
        raise ValueError("procurement export blocked until scoped projection is ready")
    if not actor_id or not export_id or not selections:
        raise ValueError("human actor, export identity and explicit selections are required")
    tender_by_id = {tender.tender_id: tender for tender in projection.tenders}
    selected: list[str] = []
    for selection in selections:
        tender = tender_by_id.get(selection.tender_id)
        if selection.actor_id != actor_id or tender is None or tender.line_id != selection.line_id:
            raise ValueError("selection actor or tender scope mismatch")
        selected.append(selection.tender_id)
    ordered_ids = tuple(sorted(set(selected)))
    return ProcurementExport(export_id, actor_id, _stable_digest(ordered_ids), ordered_ids)


__all__ = [
    "AssociationBackfill", "CandidateGroup", "DomainEvidence", "EquipmentDomainStatus",
    "EquipmentProjection", "EquipmentTender", "HumanSelection", "JsonbAssociation",
    "ProcurementExport", "ProjectionContract", "RequestLine", "TypedRequest",
    "build_projection", "prepare_procurement_export", "select_tender",
]
