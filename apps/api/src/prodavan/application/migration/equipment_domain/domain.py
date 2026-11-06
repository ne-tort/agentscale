"""Typed, fail-closed equipment domain rules, projections and human decisions.

Application ports and flow live in :mod:`service`; no persistence adapter or
external procurement side effect is implemented in this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any, Mapping, Sequence


def _stable_digest(value: Any) -> str:
    def encode(item: Any) -> Any:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, StrEnum):
            return item.value
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError(f"cannot canonically encode {type(item).__name__}")

    encoded = json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
        return left * right


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    max_adjusted = max(value.adjusted() for value in values)
    min_exponent = min(int(value.as_tuple().exponent) for value in values)
    with localcontext() as context:
        context.prec = max(28, max_adjusted - min_exponent + 1
                           + len(str(len(values))) + 2)
        return sum(values, Decimal("0"))


class EquipmentDomainStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class OfferFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DomainScope:
    tenant_id: str
    owner_id: str
    project_id: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (self.tenant_id, self.owner_id, self.project_id)):
            raise ValueError("tenant, owner and project scope are required")


@dataclass(frozen=True, slots=True)
class DomainEvidence:
    tenant_id: str | None
    owner_id: str | None
    source_id: str | None
    supplier_id: str | None
    source_schema_version: str | None
    project_id: str | None = None

    @property
    def complete(self) -> bool:
        return all(isinstance(value, str) and bool(value.strip()) for value in (
            self.tenant_id, self.owner_id, self.project_id, self.source_id,
            self.supplier_id, self.source_schema_version,
        ))

    @property
    def owner_source_complete(self) -> bool:
        return all(isinstance(value, str) and bool(value.strip()) for value in (
            self.tenant_id, self.owner_id, self.project_id, self.source_id,
            self.source_schema_version,
        ))

    @property
    def scope(self) -> tuple[str | None, str | None, str | None]:
        return self.tenant_id, self.owner_id, self.project_id

    @property
    def source_identity(self) -> tuple[str | None, str | None, str | None, str | None]:
        """Stable project/vendor/source/schema provenance for this evidence row."""
        return self.project_id, self.supplier_id, self.source_id, self.source_schema_version


@dataclass(frozen=True, slots=True)
class TypedRequest:
    request_id: str
    evidence: DomainEvidence
    title: str
    currency: str
    created_at: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
                self.request_id, self.title, self.currency, self.created_at,
        )):
            raise ValueError("request identity, title, currency and creation time are required")
        if len(self.currency) != 3 or not self.currency.isalpha() or self.currency.upper() != self.currency:
            raise ValueError("request currency must be a three-letter uppercase code")
        try:
            timestamp = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("request creation time must be ISO-8601") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("request creation time must include a timezone")


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
        if (not isinstance(self.line_id, str) or not self.line_id.strip()
                or not isinstance(self.request_id, str) or not self.request_id.strip()
                or isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int)
                or self.ordinal < 0):
            raise ValueError("line identity and nonnegative ordinal are required")
        if (not isinstance(self.description, str) or not self.description.strip()
                or not isinstance(self.unit, str) or not self.unit.strip()):
            raise ValueError("line description and unit are required")
        if (not isinstance(self.quantity, Decimal) or not self.quantity.is_finite()
                or self.quantity <= 0):
            raise ValueError("line quantity must be finite and positive")


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
        if (not isinstance(self.group_id, str) or not self.group_id.strip()
                or not isinstance(self.line_id, str) or not self.line_id.strip()
                or isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int)
                or self.ordinal < 0):
            raise ValueError("candidate group identity and nonnegative ordinal are required")
        if self.match_kind not in {"exact", "analog", "doubt"}:
            raise ValueError("unsupported candidate match kind")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("candidate aliases must be nonempty")


@dataclass(frozen=True, slots=True)
class BudgetProjection:
    line_id: str
    tender_id: str
    supplier_id: str
    source_id: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    currency: str = "RUB"
    freshness: OfferFreshness = OfferFreshness.UNKNOWN
    on_order: bool = False
    eligible_for_procurement: bool = False

    def __post_init__(self) -> None:
        if not all((self.line_id, self.tender_id, self.supplier_id, self.source_id)):
            raise ValueError("budget projection identity is required")
        if (not self.quantity.is_finite() or not self.unit_price.is_finite() or not self.total.is_finite()
                or self.quantity <= 0 or self.unit_price < 0 or self.total < 0):
            raise ValueError("budget projection values are invalid")
        if self.total != _exact_product(self.quantity, self.unit_price):
            raise ValueError("budget projection total must reconcile quantity and unit price")
        if self.eligible_for_procurement and (self.freshness is not OfferFreshness.FRESH or self.on_order):
            raise ValueError("stale or already-on-order offers cannot be procurement eligible")


class BudgetAllocationStatus(StrEnum):
    """Lifecycle of one durable budget reservation for a scoped request."""

    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class BudgetAllocation:
    """One ``equipment_v2_budget_allocations`` row scoped to a request.

    The durable table is the only source: an absent row means "budget not filled
    in yet" and is reported as an explicit empty allocation set, never invented
    from an offer price or a sheet total.
    """

    request_id: str
    cost_center: str
    amount: Decimal
    currency: str
    status: str = BudgetAllocationStatus.RESERVED

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (self.request_id, self.cost_center, self.status)):
            raise ValueError("budget allocation request, cost center and status are required")
        if len(self.currency) != 3 or not self.currency.isalpha() or self.currency.upper() != self.currency:
            raise ValueError("budget allocation currency must be a three-letter uppercase code")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("budget allocation amount must be finite and positive")

    @property
    def released(self) -> bool:
        return self.status == BudgetAllocationStatus.RELEASED.value


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
    freshness: OfferFreshness = OfferFreshness.UNKNOWN
    on_order: bool = False

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.tender_id, self.line_id, self.group_id,
                                               self.supplier_id, self.source_id,
                                               self.source_record_id, self.currency)):
            raise ValueError("tender and supplier/source identity are required")
        if len(self.currency) != 3 or not self.currency.isalpha() or self.currency.upper() != self.currency:
            raise ValueError("tender currency must be a three-letter uppercase code")
        if self.ordinal < 0 or not self.unit_price.is_finite() or self.unit_price < 0:
            raise ValueError("tender ordinal and finite price must be nonnegative")
        if not self.description.strip():
            raise ValueError("tender description is required")
        if self.available_quantity is not None and (
                not self.available_quantity.is_finite() or self.available_quantity < 0):
            raise ValueError("available quantity must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class JsonbAssociation:
    record_id: str
    association_kind: str
    related_ids: tuple[str, ...]
    legacy_json: Mapping[str, Any]
    scope: DomainScope | None = None


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
                   association_field: str, kind: str,
                   expected_scope: DomainScope | None = None,
                   tenant_field: str = "tenant_id", owner_field: str = "owner_id",
                   project_field: str = "project_id") -> "AssociationBackfill":
        if not all((id_field, association_field, kind)):
            raise ValueError("association backfill field names and kind are required")
        result: list[JsonbAssociation] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("JSONB association owner row must be an object")
            record_id = row.get(id_field)
            value = row.get(association_field)
            if not isinstance(record_id, str) or not record_id.strip():
                raise ValueError("association owner identity missing")
            if value is None:
                related: tuple[str, ...] = ()
            elif isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
                related = tuple(value)
            else:
                raise ValueError("JSONB association must be an ordered array of nonempty IDs")
            scope = None
            if expected_scope is not None:
                raw_scope = (row.get(tenant_field), row.get(owner_field), row.get(project_field))
                tenant_id, owner_id, project_id = raw_scope
                if not isinstance(tenant_id, str) or not tenant_id:
                    raise ValueError("association tenant/owner/project evidence missing")
                if not isinstance(owner_id, str) or not owner_id:
                    raise ValueError("association tenant/owner/project evidence missing")
                if not isinstance(project_id, str) or not project_id:
                    raise ValueError("association tenant/owner/project evidence missing")
                scope = DomainScope(tenant_id, owner_id, project_id)
                if scope != expected_scope:
                    raise ValueError("association owner crosses tenant, owner or project scope")
            result.append(JsonbAssociation(record_id, kind, related, dict(row), scope))
        return cls(tuple(result))


@dataclass(frozen=True, slots=True)
class ProjectionContract:
    name: str
    version: str
    input_digest: str
    deterministic: bool
    source_revision: str = ""
    source_generation: int = 0

    def __post_init__(self) -> None:
        if self.source_generation < 0:
            raise ValueError("source generation cannot be negative")


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
    selection_id: str = ""
    projection_digest: str = ""
    tenant_id: str = ""
    owner_id: str = ""
    project_id: str = ""
    supersedes_selection_id: str | None = None
    manual_override: bool = True
    supplier_id: str = ""
    source_id: str = ""
    source_record_id: str = ""
    source_schema_version: str = ""

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
                self.actor_id, self.line_id, self.tender_id, self.reason, self.selection_id,
        )):
            raise ValueError("human selection identity and reason are required")
        if not isinstance(self.manual_override, bool):
            raise ValueError("manual override marker must be boolean")


@dataclass(frozen=True, slots=True)
class ProcurementExportLine:
    selection_id: str
    line_id: str
    tender_id: str
    candidate_group_id: str
    supplier_id: str
    source_id: str
    source_record_id: str
    part_number: str | None
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    total: Decimal
    currency: str
    source_schema_version: str = ""
    source_revision: str = ""
    source_generation: int = 0
    manual_override: bool = True


@dataclass(frozen=True, slots=True)
class ProcurementExport:
    export_id: str
    actor_id: str
    selection_digest: str
    tender_ids: tuple[str, ...]
    status: str = "prepared"
    selection_ids: tuple[str, ...] = ()
    lines: tuple[ProcurementExportLine, ...] = ()
    currency_totals: tuple[tuple[str, Decimal], ...] = ()
    tenant_id: str = ""
    owner_id: str = ""
    project_id: str = ""
    projection_digest: str = ""
    source_revision: str = ""
    source_generation: int = 0
    idempotency_key: str = ""


def build_projection(*, requests: Sequence[TypedRequest], lines: Sequence[RequestLine],
                     candidate_groups: Sequence[CandidateGroup],
                     tenders: Sequence[EquipmentTender],
                     expected_scope: DomainScope | None = None,
                     source_revision: str = "",
                     source_generation: int = 0,
                     unresolved_reasons: Sequence[str] = ()) -> EquipmentProjection:
    """Build stable output, rejecting cross-scope links and identity ambiguity.

    ``unresolved_reasons`` carries evidence the source could not prove (an
    unmapped source, an unavailable currency, a detached selection).  Any such
    reason blocks the projection instead of being dropped, so a caller can never
    mistake partially resolved durable rows for a complete snapshot.
    """
    reqs = tuple(sorted(requests, key=lambda item: (item.request_id, _stable_digest(item))))
    line_rows = tuple(sorted(lines, key=lambda item: (item.request_id, item.ordinal,
                                                       item.line_id, _stable_digest(item))))
    groups = tuple(sorted(candidate_groups, key=lambda item: (item.line_id, item.ordinal,
                                                               item.group_id, _stable_digest(item))))
    tender_rows = tuple(sorted(tenders, key=lambda item: (item.line_id, item.ordinal,
                                                          item.supplier_id, item.source_id,
                                                          item.source_record_id, item.tender_id,
                                                          _stable_digest(item))))
    reasons: list[str] = []
    reasons.extend(unresolved_reasons)
    if source_generation < 0:
        reasons.append("negative_source_generation")
    request_by_id = {item.request_id: item for item in reqs}
    line_by_id = {item.line_id: item for item in line_rows}
    group_by_id = {item.group_id: item for item in groups}
    tender_by_id = {item.tender_id: item for item in tender_rows}
    if len(request_by_id) != len(reqs) or len(line_by_id) != len(line_rows):
        reasons.append("duplicate_request_or_line_identity")
    if len(group_by_id) != len(groups):
        reasons.append("duplicate_candidate_group_identity")
    if len(tender_by_id) != len(tender_rows):
        reasons.append("duplicate_tender_identity")
    if not reqs or not line_rows:
        reasons.append("empty_equipment_domain_snapshot")
    for request in reqs:
        if not request.evidence.owner_source_complete:
            reasons.append("request_owner_source_evidence_incomplete")
        if expected_scope is not None and request.evidence.scope != (
                expected_scope.tenant_id, expected_scope.owner_id, expected_scope.project_id):
            reasons.append("request_tenant_owner_project_mismatch")
    for line in line_rows:
        request = request_by_id.get(line.request_id)
        if request is None or not line.evidence.owner_source_complete:
            reasons.append("line_scope_or_evidence_missing")
        elif (line.evidence.scope, line.evidence.source_id, line.evidence.source_schema_version) != (
                request.evidence.scope, request.evidence.source_id, request.evidence.source_schema_version):
            reasons.append("line_tenant_owner_project_mismatch")
        if expected_scope is not None and line.evidence.scope != (
                expected_scope.tenant_id, expected_scope.owner_id, expected_scope.project_id):
            reasons.append("line_tenant_owner_project_mismatch")
    for group in groups:
        line = line_by_id.get(group.line_id)
        if line is None or not group.evidence.owner_source_complete:
            reasons.append("candidate_group_scope_or_evidence_missing")
        elif (group.evidence.scope, group.evidence.source_id, group.evidence.source_schema_version) != (
                line.evidence.scope, line.evidence.source_id, line.evidence.source_schema_version):
            reasons.append("candidate_group_scope_mismatch")
        if expected_scope is not None and group.evidence.scope != (
                expected_scope.tenant_id, expected_scope.owner_id, expected_scope.project_id):
            reasons.append("candidate_group_tenant_owner_project_mismatch")
    seen_source_records: set[tuple[str, str, str, str, str]] = set()
    for tender in tender_rows:
        line = line_by_id.get(tender.line_id)
        group = group_by_id.get(tender.group_id)
        if line is None or group is None or group.line_id != tender.line_id:
            reasons.append("tender_association_missing_or_cross_line")
            continue
        if not tender.evidence.complete or (tender.supplier_id, tender.source_id) != (
                tender.evidence.supplier_id, tender.evidence.source_id):
            reasons.append("tender_source_supplier_evidence_incomplete")
        if tender.evidence.scope != line.evidence.scope:
            reasons.append("tender_tenant_owner_project_mismatch")
        if tender.evidence.scope != group.evidence.scope:
            reasons.append("tender_candidate_group_scope_mismatch")
        if expected_scope is not None and tender.evidence.scope != (
                expected_scope.tenant_id, expected_scope.owner_id, expected_scope.project_id):
            reasons.append("tender_tenant_owner_project_mismatch")
        identity = (tender.line_id, tender.group_id, tender.supplier_id,
                    tender.source_id, tender.source_record_id)
        if identity in seen_source_records:
            reasons.append("duplicate_tender_source_identity")
        seen_source_records.add(identity)
    scopes = {row.evidence.scope for row in (*reqs, *line_rows, *groups, *tender_rows)}
    scopes.discard((None, None, None))
    if len(scopes) > 1:
        reasons.append("projection_mixes_tenant_owner_or_project_scope")
    reasons_tuple = tuple(sorted(set(reasons)))
    digest = _stable_digest({
        "requests": reqs, "lines": line_rows, "groups": groups, "tenders": tender_rows,
        "expected_scope": expected_scope, "source_revision": source_revision,
        "source_generation": source_generation,
    })
    ready = not reasons_tuple
    return EquipmentProjection(
        EquipmentDomainStatus.READY if ready else EquipmentDomainStatus.BLOCKED,
        reasons_tuple,
        reqs if ready else (), line_rows if ready else (), groups if ready else (), tender_rows if ready else (),
        tuple(BudgetProjection(
            t.line_id, t.tender_id, t.supplier_id, t.source_id,
            line_by_id[t.line_id].quantity, t.unit_price,
            _exact_product(line_by_id[t.line_id].quantity, t.unit_price), t.currency,
            t.freshness, t.on_order,
            t.freshness is OfferFreshness.FRESH and not t.on_order
            and t.available_quantity is not None
            and t.available_quantity >= line_by_id[t.line_id].quantity,
        ) for t in tender_rows) if ready else (),
        ProjectionContract("equipment-domain", "2", digest, True, source_revision, source_generation),
    )


def select_tender(*, projection: EquipmentProjection, actor_id: str,
                  line_id: str, tender_id: str, reason: str,
                  selection_id: str = "", supersedes_selection_id: str | None = None,
                  manual_override: bool = True) -> HumanSelection:
    if projection.status is not EquipmentDomainStatus.READY:
        raise ValueError("selection blocked until scoped projection is ready")
    if not actor_id.strip() or not reason.strip() or not selection_id.strip():
        raise ValueError("human actor, immutable selection identity and reason are required")
    line = next((item for item in projection.lines if item.line_id == line_id), None)
    if line is None:
        raise ValueError("selection line is outside projection")
    if not any(tender.tender_id == tender_id and tender.line_id == line_id
               for tender in projection.tenders):
        raise ValueError("selection tender is outside line")
    tender = next(tender for tender in projection.tenders if tender.tender_id == tender_id)
    group = next((group for group in projection.candidate_groups if group.group_id == tender.group_id), None)
    if group is None or group.line_id != line_id:
        raise ValueError("selected tender is not a member of a candidate group for this line")
    if (tender.freshness is not OfferFreshness.FRESH or tender.on_order
            or tender.available_quantity is None
            or tender.available_quantity < line.quantity):
        raise ValueError("stale, unavailable or already-on-order tenders cannot be selected")
    return HumanSelection(
        actor_id, line_id, tender_id, reason.strip(), selection_id,
        projection.contract.input_digest, line.evidence.tenant_id or "",
        line.evidence.owner_id or "", line.evidence.project_id or "", supersedes_selection_id,
        manual_override, tender.supplier_id, tender.source_id, tender.source_record_id,
        tender.evidence.source_schema_version or "",
    )


def active_human_selections(selections: Sequence[HumanSelection]) -> tuple[HumanSelection, ...]:
    by_id: dict[str, HumanSelection] = {}
    superseded_ids: set[str] = set()
    for selection in selections:
        if not selection.selection_id or selection.selection_id in by_id:
            raise ValueError("selection history has missing or duplicate immutable identity")
        by_id[selection.selection_id] = selection
    for selection in selections:
        previous_id = selection.supersedes_selection_id
        if previous_id is None:
            continue
        previous = by_id.get(previous_id)
        if (previous is None or previous.actor_id != selection.actor_id
                or previous.line_id != selection.line_id or previous_id in superseded_ids):
            raise ValueError("selection supersession is missing, cross-actor or ambiguous")
        superseded_ids.add(previous_id)
    for selection in selections:
        trail: set[str] = set()
        current: HumanSelection | None = selection
        while current is not None:
            if current.selection_id in trail:
                raise ValueError("selection supersession history contains a cycle")
            trail.add(current.selection_id)
            current = (by_id.get(current.supersedes_selection_id)
                       if current.supersedes_selection_id is not None else None)
    active = tuple(sorted(
        (item for item in selections if item.selection_id not in superseded_ids),
        key=lambda item: (item.line_id, item.selection_id),
    ))
    if selections and not active:
        raise ValueError("selection history has no active human decision")
    active_lines = [item.line_id for item in active]
    if len(active_lines) != len(set(active_lines)):
        raise ValueError("selection history has multiple active tenders for one request line")
    return active


def selection_evidence_digest(selections: Sequence[HumanSelection]) -> str:
    """Stable digest over the selected-offer evidence set (order independent).

    Shared by export preparation and the durable transaction adapter so a
    committed export can re-prove the selection set it was built from.
    """
    return _stable_digest(tuple(sorted(
        (item.selection_id, item.actor_id, item.line_id, item.tender_id, item.reason,
         item.projection_digest, item.supersedes_selection_id, item.manual_override,
         item.supplier_id, item.source_id, item.source_record_id, item.source_schema_version)
        for item in selections
    )))


def prepare_procurement_export(*, projection: EquipmentProjection, selections: Sequence[HumanSelection],
                               actor_id: str, export_id: str) -> ProcurementExport:
    """Materialize export rows from explicit, same-project human selections only."""
    if projection.status is not EquipmentDomainStatus.READY:
        raise ValueError("procurement export blocked until scoped projection is ready")
    if not actor_id.strip() or not export_id.strip() or not selections:
        raise ValueError("human actor, export identity and explicit selections are required")
    line_by_id = {line.line_id: line for line in projection.lines}
    group_by_id = {group.group_id: group for group in projection.candidate_groups}
    tender_by_id = {tender.tender_id: tender for tender in projection.tenders}
    selections = active_human_selections(selections)
    selected_tenders: set[str] = set()
    selected_lines: set[str] = set()
    seen_selection_ids: set[str] = set()
    rows: list[tuple[tuple[str, int, str, int, str], ProcurementExportLine]] = []
    for selection in selections:
        tender = tender_by_id.get(selection.tender_id)
        line = line_by_id.get(selection.line_id)
        if selection.actor_id != actor_id or tender is None or line is None or tender.line_id != selection.line_id:
            raise ValueError("selection actor or tender scope mismatch")
        if (not selection.selection_id or not selection.reason.strip()
                or selection.projection_digest != projection.contract.input_digest):
            raise ValueError("selection identity, reason or projection digest is missing or stale")
        if (selection.tenant_id, selection.owner_id, selection.project_id) != line.evidence.scope:
            raise ValueError("selection crosses tenant, owner or project scope")
        if (selection.supplier_id, selection.source_id, selection.source_record_id,
                selection.source_schema_version) != (
                tender.supplier_id, tender.source_id, tender.source_record_id,
                tender.evidence.source_schema_version):
            raise ValueError("selection source/supplier provenance does not match the current offer")
        group = group_by_id.get(tender.group_id)
        if group is None or group.line_id != line.line_id:
            raise ValueError("selected tender is not a member of this line's candidate group")
        if (tender.freshness is not OfferFreshness.FRESH or tender.on_order
                or tender.available_quantity is None
                or tender.available_quantity < line.quantity):
            raise ValueError("stale, unavailable or already-on-order tenders cannot be exported")
        if tender.tender_id in selected_tenders or line.line_id in selected_lines:
            raise ValueError("procurement export allows at most one explicit offer per request line")
        if selection.selection_id and selection.selection_id in seen_selection_ids:
            raise ValueError("duplicate human selection identity")
        selected_tenders.add(tender.tender_id)
        selected_lines.add(line.line_id)
        if selection.selection_id:
            seen_selection_ids.add(selection.selection_id)
        export_line = ProcurementExportLine(
            selection_id=selection.selection_id,
            line_id=line.line_id,
            tender_id=tender.tender_id,
            candidate_group_id=tender.group_id,
            supplier_id=tender.supplier_id,
            source_id=tender.source_id,
            source_record_id=tender.source_record_id,
            part_number=tender.part_number,
            description=tender.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=tender.unit_price,
            total=_exact_product(line.quantity, tender.unit_price),
            currency=tender.currency,
            source_schema_version=tender.evidence.source_schema_version or "",
            source_revision=projection.contract.source_revision,
            source_generation=projection.contract.source_generation,
            manual_override=selection.manual_override,
        )
        rows.append(((line.request_id, line.ordinal, line.line_id,
                      group.ordinal, tender.tender_id), export_line))

    ordered_rows = tuple(row for _, row in sorted(rows, key=lambda item: item[0]))
    ordered_ids = tuple(row.tender_id for row in ordered_rows)
    selection_ids = tuple(row.selection_id for row in ordered_rows if row.selection_id)
    totals: dict[str, Decimal] = {}
    for row in ordered_rows:
        totals[row.currency] = _exact_sum((totals.get(row.currency, Decimal("0")), row.total))
    selection_digest = selection_evidence_digest(selections)
    first_line = line_by_id[ordered_rows[0].line_id]
    idempotency_key = _stable_digest({
        "tenant_id": first_line.evidence.tenant_id,
        "owner_id": first_line.evidence.owner_id,
        "project_id": first_line.evidence.project_id,
        "export_id": export_id,
    })
    return ProcurementExport(
        export_id, actor_id, selection_digest, ordered_ids, "prepared", selection_ids,
        ordered_rows, tuple(sorted(totals.items())), first_line.evidence.tenant_id or "",
        first_line.evidence.owner_id or "", first_line.evidence.project_id or "",
        projection.contract.input_digest, projection.contract.source_revision,
        projection.contract.source_generation, idempotency_key,
    )


__all__ = [
    "AssociationBackfill", "BudgetAllocation", "BudgetAllocationStatus", "BudgetProjection",
    "CandidateGroup", "DomainEvidence", "DomainScope",
    "EquipmentDomainStatus", "EquipmentProjection", "EquipmentTender", "HumanSelection",
    "JsonbAssociation", "OfferFreshness", "ProcurementExport", "ProcurementExportLine",
    "ProjectionContract", "RequestLine", "TypedRequest",
    "active_human_selections", "build_projection", "prepare_procurement_export",
    "select_tender", "selection_evidence_digest",
]
