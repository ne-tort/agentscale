"""Durable equipment read/selection/export repository over the ``equipment_v2_*`` tables.

Scope of this module
--------------------
It implements the equipment-domain ports — :class:`~.service.EquipmentDomainReadPort`,
``HumanDecisionPort``, ``EquipmentActorPort``, ``EquipmentBudgetPort`` and both write
ports of :mod:`~.transactions` — against the tables created by revision ``2026110305``:

``equipment_v2_requests`` → ``equipment_v2_request_lines`` →
``equipment_v2_candidate_groups`` ← ``equipment_v2_catalog_snapshots`` /
``equipment_v2_catalog_offers``, plus ``equipment_v2_budget_allocations`` and
``equipment_v2_ingestion_runs`` for tenant ownership of the source chain.

No DDL, ORM model or Alembic revision is added or changed here: every column used
below exists in that revision.  Legacy ``mod_equipment`` JSON rows are never read
or written.

A selection is the durable state, not a memory structure
-------------------------------------------------------
The human decision is the scalar ``equipment_v2_candidate_groups.selected_offer_id``:

* the write is one ``UPDATE ... WHERE`` whose predicate proves candidate-group
  membership (``:offer_id = ANY(offer_ids)``), verified snapshot/tenant ownership
  of the offer and the expected prior selection.  A concurrent writer therefore
  cannot select a non-member, cannot select another tenant's offer and cannot
  overwrite a decision it did not observe;
* the group row is read ``FOR UPDATE`` first, so the compare-and-swap reads and
  writes one row version;
* repeating the same selection issues no UPDATE and no second audit row and
  reports ``replayed`` / ``equipment_selection_unchanged``;
* switching requires ``expected_prior_selection_id`` to equal the current value;
  anything else is ``conflict`` / ``equipment_selection_cas_conflict``;
* ``ondelete="SET NULL"`` detaches a deleted chosen offer without breaking the
  row.  That state is detected (``equipment_selected_offer_detached``) and blocks
  export until a human selects again; it is never silently re-pointed.

Carriers the schema lacks
-------------------------
``2026110305`` has no project/owner/source column on requests, no schema-version,
no freshness, no selection history and no idempotency table.  Instead of
inventing columns (outside this task zone) or inferring scope from payloads:

* tenant/project/cabinet ownership is proven through ``scope_owner_project_anchors``
  plus the transaction-local RLS context written by
  ``infrastructure/persistence/rls`` — a missing context or a context that
  differs from the server-resolved principal is refused before any read;
* the append-only decision/idempotency ledger lives in ``platform_events``, the
  existing bus whose ``uq_platform_events_idempotency_key`` gives race-proof
  replay identity (the same carrier ``dynamic_apps`` already uses).  Payloads
  hold identities, digests, the redacted human reason and result IDs only —
  never provider payloads, offer attributes or credentials;
* the two facts the tables cannot express — the SoT ``owner_id`` and the
  per-source ``schema_version`` mapping — are supplied explicitly through
  :class:`EquipmentDurableConfig`.  A missing mapping is *unresolved evidence*
  and blocks the projection; it is never guessed;
* request→project binding is an append-only ``equipment.request.bound`` event
  written by :meth:`SqlAlchemyEquipmentRepository.bind_request`.

The remaining DDL gap is recorded in the promotion record
(``equipment_v2_request_scope_columns_missing``): request
``project_id``/``owner_id``/``source_id``/``source_schema_version`` columns plus a
dedicated selection-history and idempotency table are the correct long-term
carriers.

Fail-closed summary
-------------------
Missing project anchor, inactive owner, absent RLS context, cross-tenant
principal, unbound request, unmapped source, absent/ambiguous request currency,
ambiguous group membership, changed payload digest and unknown commit all yield a
``blocked``/``conflict`` result (or raise) instead of a positive outcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    BudgetAllocation,
    CandidateGroup,
    DomainEvidence,
    DomainScope,
    EquipmentTender,
    HumanSelection,
    OfferFreshness,
    RequestLine,
    TypedRequest,
    _stable_digest,
    selection_evidence_digest,
)
from .service import DecisionActor, DecisionActorKind, EquipmentDomainSnapshot
from .transactions import (
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
    ExportTransaction,
    SelectionTransaction,
)


#: ``platform_events.event_type`` values owned by this repository.
REQUEST_BOUND_EVENT = "equipment.request.bound"
SELECTION_COMMITTED_EVENT = "equipment.selection.committed"
EXPORT_PREPARED_EVENT = "equipment.export.prepared"

#: Reserved ``platform_events.payload._kind`` marker so these rows stay
#: distinguishable from every other producer of the shared bus.
EVENT_PAYLOAD_KIND = "equipment.v2.migration"

#: Unique constraint that turns a concurrent duplicate append into a replay.
IDEMPOTENCY_CONSTRAINT = "uq_platform_events_idempotency_key"

#: Stable, redacted, API-mappable reason codes.
REASON_SELECTION_UNCHANGED = "equipment_selection_unchanged"
REASON_SELECTION_CAS_CONFLICT = "equipment_selection_cas_conflict"
REASON_SELECTION_LINE_MISSING = "equipment_selection_line_missing"
REASON_SELECTION_GROUP_AMBIGUOUS = "equipment_selection_group_ambiguous"
REASON_SELECTION_MEMBERSHIP_UNPROVEN = "equipment_candidate_membership_unproven"
REASON_SELECTION_IDEMPOTENCY_CONFLICT = "equipment_selection_idempotency_conflict"
REASON_SELECTED_OFFER_DETACHED = "equipment_selected_offer_detached"
REASON_REQUEST_BINDING_MISSING = "equipment_request_binding_missing"
REASON_EXPORT_SELECTION_SET_CHANGED = "equipment_export_selection_set_changed"
REASON_EXPORT_LINE_MISMATCH = "equipment_export_line_mismatch"
REASON_EXPORT_IDEMPOTENCY_CONFLICT = "equipment_export_idempotency_conflict"
REASON_COMMIT_UNKNOWN = "equipment_transaction_commit_unknown"
REASON_CONTEXT_MISSING = "equipment_trusted_context_missing"
REASON_CONTEXT_MISMATCH = "equipment_trusted_context_mismatch"
REASON_PROJECT_ANCHOR_MISSING = "equipment_project_anchor_missing"
REASON_PRINCIPAL_MISMATCH = "equipment_principal_scope_mismatch"
REASON_OWNER_MISMATCH = "equipment_owner_scope_mismatch"
REASON_STATEMENT_FORBIDDEN = "equipment_durable_statement_forbidden"

#: Unresolved-evidence codes reported by :meth:`load_snapshot`; each blocks the
#: projection instead of producing a guessed value.
UNRESOLVED_REQUEST_SOURCE = "request_source_provenance_unmapped"
UNRESOLVED_SOURCE_SCHEMA = "source_schema_version_unmapped"
UNRESOLVED_OFFER_SOURCE_CHAIN = "offer_source_chain_mismatch"
UNRESOLVED_OFFER_GROUP_AMBIGUOUS = "offer_candidate_group_ambiguous"
UNRESOLVED_REQUEST_CURRENCY = "request_currency_unavailable"
UNRESOLVED_REQUEST_CURRENCY_AMBIGUOUS = "request_currency_ambiguous"

#: States reported by :meth:`SqlAlchemyEquipmentRepository.selection_state`.
SELECTION_STATE_SELECTED = "selected"
SELECTION_STATE_UNSELECTED = "unselected"
SELECTION_STATE_DETACHED = "detached"
SELECTION_STATE_LINE_MISSING = "line_missing"


class EquipmentDurableBlocked(RuntimeError):
    """A durable equipment operation was refused before any positive write."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if not detail else f"{reason_code}: {detail}")


class _Principal(Protocol):
    """Server-resolved principal (``TrustedPrincipal`` shape), never request data."""

    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_id: str
    role: str
    resolution_source: str


@dataclass(frozen=True, slots=True)
class EquipmentDurableConfig:
    """Deployment facts the ``2026110305`` tables cannot express.

    ``owner_id`` and the source→schema mapping come from the composition root
    after the M04 owner registry accepted them.  The adapter refuses to start
    without them instead of inventing an owner or a schema version.
    """

    owner_id: str
    source_schema_versions: Mapping[str, str]
    writer_generation: int
    freshness_window: timedelta | None = None
    reason_max_length: int = 500

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("durable equipment config requires an explicit SoT owner_id")
        if (isinstance(self.writer_generation, bool)
                or not isinstance(self.writer_generation, int)
                or self.writer_generation < 0):
            raise ValueError("writer generation must be a nonnegative integer")
        if not isinstance(self.source_schema_versions, Mapping):
            raise ValueError("source schema versions must be an explicit mapping")
        for source_id, version in self.source_schema_versions.items():
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError("source schema mapping keys must be nonempty source ids")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("source schema mapping values must be nonempty versions")
        if self.freshness_window is not None and (
                not isinstance(self.freshness_window, timedelta)
                or self.freshness_window <= timedelta(0)):
            raise ValueError("freshness window must be a positive timedelta or None")
        if isinstance(self.reason_max_length, bool) or self.reason_max_length < 32:
            raise ValueError("reason max length must be at least 32 characters")

    def schema_version(self, source_id: str | None) -> str | None:
        if not isinstance(source_id, str) or not source_id:
            return None
        return self.source_schema_versions.get(source_id)

    def freshness(self, *, status: str, captured_at: datetime | None,
                  now: datetime) -> OfferFreshness:
        """Server policy: only an active, recently captured snapshot is fresh."""
        if status != "active":
            return OfferFreshness.STALE
        if self.freshness_window is None or captured_at is None:
            return OfferFreshness.UNKNOWN
        captured = captured_at if captured_at.tzinfo is not None else captured_at.replace(tzinfo=UTC)
        if captured > now:
            return OfferFreshness.UNKNOWN
        return (OfferFreshness.FRESH if now - captured <= self.freshness_window
                else OfferFreshness.STALE)


# ---------------------------------------------------------------------------
# SQL — module constants so acceptance checks can read the exact predicates.
# ---------------------------------------------------------------------------

_CONTEXT_SQL = (
    "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.project_id', true) AS project_id, "
    "current_setting('app.user_id', true) AS subject_id, "
    "current_setting('row_security', true) AS row_security "
)

_PROJECT_ANCHOR_SQL = (
    "SELECT project_id, tenant_id, cabinet_id, owner_state "
    "FROM scope_owner_project_anchors "
    "WHERE project_id = :project_id AND tenant_id = :tenant_id"
)

_BINDINGS_SQL = (
    "SELECT payload->>'request_id' AS request_id, "
    "payload->>'source_id' AS source_id, "
    "payload->>'source_schema_version' AS source_schema_version, "
    "payload->>'binding_digest' AS binding_digest "
    "FROM platform_events "
    "WHERE event_type = :event_type AND company_id = :tenant_id "
    "AND project_id = :project_id AND payload->>'_kind' = :payload_kind "
    "ORDER BY created_at, id"
)

_REQUESTS_SQL = (
    "SELECT id, tenant_id, title, status, created_at "
    "FROM equipment_v2_requests WHERE tenant_id = :tenant_id AND id = ANY(:request_ids) "
    "ORDER BY id"
)

_LINES_SQL = (
    "SELECT id, request_id, line_number, title, quantity, unit "
    "FROM equipment_v2_request_lines WHERE request_id = ANY(:request_ids) "
    "ORDER BY request_id, line_number, id"
)

_GROUPS_SQL = (
    "SELECT g.id, g.request_line_id, g.title, g.status, g.selected_offer_id, "
    "COALESCE(g.offer_ids, '[]'::jsonb) AS offer_ids "
    "FROM equipment_v2_candidate_groups AS g "
    "JOIN equipment_v2_request_lines AS l ON l.id = g.request_line_id "
    "WHERE l.request_id = ANY(:request_ids) "
    "ORDER BY g.request_line_id, g.id"
)

_OFFERS_SQL = (
    "SELECT o.id, o.snapshot_id, o.supplier_key, o.external_key, o.title, "
    "o.unit_price, o.currency, o.available_quantity, o.status, "
    "s.source_id, s.captured_at, s.ingestion_run_id, r.tenant_id AS run_tenant_id "
    "FROM equipment_v2_catalog_offers AS o "
    "JOIN equipment_v2_catalog_snapshots AS s ON s.id = o.snapshot_id "
    "JOIN equipment_v2_ingestion_runs AS r ON r.id = s.ingestion_run_id "
    "WHERE o.id = ANY(:offer_ids) ORDER BY o.id"
)

_BUDGETS_SQL = (
    "SELECT id, request_id, cost_center, amount, currency, status "
    "FROM equipment_v2_budget_allocations WHERE request_id = ANY(:request_ids) "
    "ORDER BY request_id, cost_center, currency, id"
)

_LINE_STATE_SQL = (
    "SELECT l.id AS line_id, l.request_id, l.line_number, l.quantity, l.unit, "
    "g.id AS group_id, g.selected_offer_id, COALESCE(g.offer_ids, '[]'::jsonb) AS offer_ids "
    "FROM equipment_v2_request_lines AS l "
    "LEFT JOIN equipment_v2_candidate_groups AS g ON g.request_line_id = l.id "
    "WHERE l.id = :line_id"
)

_LINE_LOCK_SQL = _LINE_STATE_SQL + " FOR UPDATE OF g"

_MEMBERSHIP_SQL = (
    "SELECT o.id AS offer_id, o.supplier_key, o.external_key, o.unit_price, o.currency, "
    "o.available_quantity, o.status, s.source_id, s.captured_at, r.tenant_id AS run_tenant_id "
    "FROM equipment_v2_catalog_offers AS o "
    "JOIN equipment_v2_catalog_snapshots AS s ON s.id = o.snapshot_id "
    "JOIN equipment_v2_ingestion_runs AS r ON r.id = s.ingestion_run_id "
    "JOIN equipment_v2_candidate_groups AS g ON g.id = :group_id "
    "WHERE o.id = :offer_id "
    "AND o.id = ANY(COALESCE(g.offer_ids, '[]'::jsonb)) "
    "AND r.tenant_id = :tenant_id"
)

_SELECTION_UPDATE_SQL = (
    "UPDATE equipment_v2_candidate_groups AS g SET selected_offer_id = :offer_id "
    "WHERE g.id = :group_id AND g.request_line_id = :line_id "
    "AND :offer_id = ANY(COALESCE(g.offer_ids, '[]'::jsonb)) "
    "AND g.selected_offer_id IS NOT DISTINCT FROM :expected_prior "
    "AND EXISTS (SELECT 1 FROM equipment_v2_catalog_offers AS o "
    "  JOIN equipment_v2_catalog_snapshots AS s ON s.id = o.snapshot_id "
    "  JOIN equipment_v2_ingestion_runs AS r ON r.id = s.ingestion_run_id "
    "  WHERE o.id = :offer_id AND r.tenant_id = :tenant_id) "
    "RETURNING g.id"
)

_EXPORT_FACTS_SQL = (
    "SELECT l.id AS line_id, l.quantity, g.id AS group_id, g.selected_offer_id, "
    "o.supplier_key, o.external_key, o.unit_price, o.currency "
    "FROM equipment_v2_request_lines AS l "
    "JOIN equipment_v2_candidate_groups AS g ON g.request_line_id = l.id "
    "LEFT JOIN equipment_v2_catalog_offers AS o ON o.id = g.selected_offer_id "
    "WHERE l.id = ANY(:line_ids)"
)

_APPEND_EVENT_SQL = (
    "INSERT INTO platform_events "
    "(id, event_type, company_id, project_id, cabinet_id, actor_sub, idempotency_key, payload) "
    "VALUES (:id, :event_type, :company_id, :project_id, :cabinet_id, :actor_sub, "
    ":idempotency_key, CAST(:payload AS jsonb)) RETURNING id"
)

_EVENT_BY_KEY_SQL = (
    "SELECT id, event_type, company_id, project_id, actor_sub, payload "
    "FROM platform_events WHERE idempotency_key = :idempotency_key"
)

_LEDGER_SQL = (
    "SELECT id, idempotency_key, actor_sub, payload, created_at "
    "FROM platform_events "
    "WHERE event_type = :event_type AND company_id = :tenant_id "
    "AND project_id = :project_id AND payload->>'_kind' = :payload_kind "
    "AND payload->>'owner_id' = :owner_id "
    "ORDER BY created_at, id"
)

#: The decision ledger is append-only and a decision is a transition, never a
#: delete.  Any statement containing these fragments is refused outright.
_FORBIDDEN_SQL_FRAGMENTS = ("DELETE", "TRUNCATE", "DROP ", "UPDATE PLATFORM_EVENTS")


def _is_sha256(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:]))


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class SqlAlchemyEquipmentRepository:
    """Durable equipment ports over ``equipment_v2_*`` and the platform event bus."""

    def __init__(self, session: AsyncSession, *, principal: _Principal,
                 config: EquipmentDurableConfig,
                 clock: Callable[[], datetime] | None = None) -> None:
        if not isinstance(config, EquipmentDurableConfig):
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISSING,
                                          "durable equipment config is required")
        self._session = session
        self._principal = principal
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------ read ports --

    async def load_snapshot(self, *, scope: DomainScope) -> EquipmentDomainSnapshot | None:
        await self._assert_context(scope)
        bindings = await self._request_bindings(scope)
        if not bindings:
            return self._empty_snapshot(scope)

        request_ids = sorted(bindings)
        requests = await self._rows(_REQUESTS_SQL, {"tenant_id": scope.tenant_id,
                                                    "request_ids": request_ids})
        lines = await self._rows(_LINES_SQL, {"request_ids": request_ids})
        groups = await self._rows(_GROUPS_SQL, {"request_ids": request_ids})
        budgets = await self._rows(_BUDGETS_SQL, {"request_ids": request_ids})
        offer_ids = sorted({str(offer_id) for row in groups
                            for offer_id in (row["offer_ids"] or [])})
        offers = ([{**row, "id": row["id"]}
                   for row in await self._rows(_OFFERS_SQL, {"offer_ids": offer_ids})]
                  if offer_ids else [])
        offers_by_id = {str(row["id"]): row for row in offers}

        # Raw identity maps are built before typed records so currency and
        # provenance lookups never depend on construction order.
        line_request = {str(row["id"]): str(row["request_id"]) for row in lines}
        group_line = {str(row["id"]): line_request.get(str(row["request_line_id"]), "")
                      for row in groups}
        group_members = {str(row["id"]): tuple(str(item) for item in (row["offer_ids"] or []))
                         for row in groups}
        offer_groups: dict[str, list[str]] = {}
        for group_id, members in group_members.items():
            for offer_id in members:
                offer_groups.setdefault(offer_id, []).append(group_id)
        request_currencies: dict[str, set[str]] = {}
        for offer_id, member_groups in offer_groups.items():
            offer = offers_by_id.get(offer_id)
            if offer is None:
                continue
            for group_id in member_groups:
                request_id = line_request.get(group_line.get(group_id, ""), "")
                if request_id and offer.get("currency"):
                    request_currencies.setdefault(request_id, set()).add(str(offer["currency"]))

        unresolved: list[tuple[str, str]] = []
        now = self._clock()

        typed_requests: list[TypedRequest] = []
        for row in sorted(requests, key=lambda item: str(item["id"])):
            request_id = str(row["id"])
            binding = bindings.get(request_id)
            if binding is None:
                unresolved.append((request_id, UNRESOLVED_REQUEST_SOURCE))
                continue
            source_id = binding.get("source_id") or ""
            schema_version = (binding.get("source_schema_version")
                              or self._config.schema_version(source_id)) or ""
            if not source_id or not schema_version:
                unresolved.append((request_id, UNRESOLVED_REQUEST_SOURCE))
                continue
            currencies = request_currencies.get(request_id, set())
            if not currencies:
                unresolved.append((request_id, UNRESOLVED_REQUEST_CURRENCY))
                continue
            if len(currencies) > 1:
                unresolved.append((request_id, UNRESOLVED_REQUEST_CURRENCY_AMBIGUOUS))
                continue
            typed_requests.append(TypedRequest(
                request_id=request_id,
                evidence=self._evidence(scope, source_id=source_id,
                                        schema_version=schema_version, supplier_id=None),
                title=str(row["title"]),
                currency=next(iter(currencies)),
                created_at=_as_aware(row["created_at"]).isoformat()  # type: ignore[union-attr]
                if row["created_at"] is not None else "1970-01-01T00:00:00+00:00",
            ))
        request_by_id = {row.request_id: row for row in typed_requests}

        typed_lines: list[RequestLine] = []
        line_by_id: dict[str, RequestLine] = {}
        for row in sorted(lines, key=lambda item: (str(item["request_id"]),
                                                   int(item["line_number"]), str(item["id"]))):
            request = request_by_id.get(str(row["request_id"]))
            if request is None:
                continue
            line = RequestLine(
                line_id=str(row["id"]), request_id=request.request_id,
                evidence=self._evidence(scope, source_id=request.evidence.source_id,
                                        schema_version=request.evidence.source_schema_version,
                                        supplier_id=None),
                ordinal=max(0, int(row["line_number"]) - 1),
                description=str(row["title"]), quantity=_as_decimal(row["quantity"]),
                unit=str(row["unit"]),
            )
            typed_lines.append(line)
            line_by_id[line.line_id] = line

        typed_groups: list[CandidateGroup] = []
        empty_groups: list[str] = []
        for row in sorted(groups, key=lambda item: (str(item["request_line_id"]), str(item["id"]))):
            line = line_by_id.get(str(row["request_line_id"]))
            if line is None:
                continue
            group_id = str(row["id"])
            if not group_members[group_id]:
                empty_groups.append(group_id)
            typed_groups.append(CandidateGroup(
                group_id=group_id, line_id=line.line_id,
                evidence=self._evidence(scope, source_id=line.evidence.source_id,
                                        schema_version=line.evidence.source_schema_version,
                                        supplier_id=None),
                ordinal=0, match_kind="exact", part_number=None,
            ))

        typed_tenders: list[EquipmentTender] = []
        for offer_id in sorted(offer_groups):
            offer = offers_by_id.get(offer_id)
            if offer is None:
                unresolved.append((offer_id, UNRESOLVED_OFFER_SOURCE_CHAIN))
                continue
            candidates = sorted(offer_groups[offer_id])
            distinct_lines = {group_line.get(group_id, "") for group_id in candidates}
            if len(candidates) != 1 or len(distinct_lines) != 1 or "" in distinct_lines:
                unresolved.append((offer_id, UNRESOLVED_OFFER_GROUP_AMBIGUOUS))
                continue
            group_id = candidates[0]
            line = line_by_id.get(group_line[group_id])
            if line is None:
                unresolved.append((offer_id, UNRESOLVED_OFFER_GROUP_AMBIGUOUS))
                continue
            if str(offer["run_tenant_id"]) != scope.tenant_id:
                unresolved.append((offer_id, UNRESOLVED_OFFER_SOURCE_CHAIN))
                continue
            schema_version = (self._config.schema_version(str(offer["source_id"]))
                              or line.evidence.source_schema_version or "")
            if not schema_version:
                unresolved.append((offer_id, UNRESOLVED_SOURCE_SCHEMA))
                continue
            typed_tenders.append(EquipmentTender(
                tender_id=offer_id, line_id=line.line_id, group_id=group_id,
                evidence=self._evidence(scope, source_id=str(offer["source_id"]),
                                        schema_version=schema_version,
                                        supplier_id=str(offer["supplier_key"])),
                ordinal=group_members[group_id].index(offer_id),
                supplier_id=str(offer["supplier_key"]),
                source_id=str(offer["source_id"]),
                source_record_id=str(offer["external_key"]),
                part_number=None, description=str(offer["title"]),
                currency=str(offer["currency"]),
                unit_price=_as_decimal(offer["unit_price"]),
                available_quantity=_as_decimal(offer["available_quantity"]),
                freshness=self._config.freshness(status=str(offer["status"]),
                                                 captured_at=_as_aware(offer["captured_at"]),
                                                 now=now),
                on_order=False,
            ))

        allocations = tuple(
            BudgetAllocation(
                request_id=str(row["request_id"]), cost_center=str(row["cost_center"]),
                amount=_as_decimal(row["amount"]), currency=str(row["currency"]),
                status=str(row["status"]),
            )
            for row in sorted(budgets, key=lambda item: (str(item["request_id"]),
                                                         str(item["cost_center"]),
                                                         str(item["currency"]), str(item["id"])))
        )
        return EquipmentDomainSnapshot(
            scope=scope,
            source_revision=self._revision(requests, lines, groups, offers, budgets),
            requests=tuple(typed_requests),
            lines=tuple(typed_lines),
            candidate_groups=tuple(typed_groups),
            tenders=tuple(typed_tenders),
            source_generation=self._config.writer_generation,
            budget_allocations=allocations,
            unresolved_subjects=tuple(sorted(set(unresolved))),
            empty_candidate_groups=tuple(sorted(set(empty_groups))),
        )

    async def load_allocations(self, *, scope: DomainScope) -> tuple[BudgetAllocation, ...]:
        snapshot = await self.load_snapshot(scope=scope)
        return () if snapshot is None else snapshot.budget_allocations

    async def resolve_actor(self, *, actor_id: str,
                            scope: DomainScope) -> DecisionActor | None:
        await self._assert_context(scope)
        if not isinstance(actor_id, str) or not actor_id.strip():
            return None
        if actor_id != self._principal.subject_id:
            # The only authenticated identity this adapter can vouch for is the
            # server-resolved principal; any other claimed actor is refused.
            return None
        return DecisionActor(
            actor_id=actor_id, kind=self._actor_kind(),
            tenant_id=scope.tenant_id, owner_id=scope.owner_id, project_id=scope.project_id,
        )

    async def load_selections(self, *, scope: DomainScope,
                              actor_id: str) -> tuple[HumanSelection, ...]:
        await self._assert_context(scope)
        selections: list[HumanSelection] = []
        for row in await self._ledger(scope, SELECTION_COMMITTED_EVENT):
            payload = self._payload(row)
            if payload.get("actor_id") != actor_id:
                continue
            selections.append(self._selection_from_event(row, payload))
        return tuple(sorted(selections, key=lambda item: (item.line_id, item.selection_id)))

    # ------------------------------------------------------- transaction ports --

    async def commit_selection(self, *,
                               transaction: SelectionTransaction) -> EquipmentTransactionResult:
        """Append one human selection under a single durable transaction."""
        scope = transaction.scope
        await self._assert_context(scope)
        replay = await self._replay(transaction.idempotency_key, transaction.payload_digest,
                                    SELECTION_COMMITTED_EVENT, scope)
        if replay is not None:
            return replay
        selection = transaction.selection
        if selection.actor_id != self._principal.subject_id:
            raise EquipmentDurableBlocked(REASON_PRINCIPAL_MISMATCH,
                                          "selection actor is not the resolved principal")
        if selection.owner_id != self._config.owner_id:
            raise EquipmentDurableBlocked(REASON_OWNER_MISMATCH,
                                          "selection owner is not the configured SoT owner")

        locked = await self._rows(_LINE_LOCK_SQL, {"line_id": selection.line_id})
        if not locked:
            return self._conflict(transaction, REASON_SELECTION_LINE_MISSING)
        if len(locked) > 1:
            return self._conflict(transaction, REASON_SELECTION_GROUP_AMBIGUOUS)
        row = locked[0]
        if row["group_id"] is None:
            return self._conflict(transaction, REASON_SELECTION_GROUP_AMBIGUOUS)
        group_id = str(row["group_id"])
        if (await self._request_bindings(scope)).get(str(row["request_id"])) is None:
            return self._conflict(transaction, REASON_REQUEST_BINDING_MISSING)

        current = row["selected_offer_id"]
        current_id = str(current) if current is not None else None
        if current_id is not None and current_id == selection.tender_id:
            # The same human decision again: no UPDATE and no second audit row.
            return self._result(transaction, EquipmentTransactionStatus.REPLAYED,
                                REASON_SELECTION_UNCHANGED, durable=True)
        if current_id != transaction.expected_prior_selection_id:
            return self._conflict(transaction, REASON_SELECTION_CAS_CONFLICT)

        membership = await self._rows(_MEMBERSHIP_SQL, {
            "group_id": group_id, "offer_id": selection.tender_id,
            "tenant_id": scope.tenant_id,
        })
        if len(membership) != 1:
            return self._conflict(transaction, REASON_SELECTION_MEMBERSHIP_UNPROVEN)

        updated = await self._scalars(_SELECTION_UPDATE_SQL, {
            "offer_id": selection.tender_id, "group_id": group_id,
            "line_id": selection.line_id,
            "expected_prior": transaction.expected_prior_selection_id,
            "tenant_id": scope.tenant_id,
        })
        if len(updated) != 1:
            # Lost the row between the lock and the update, or the predicate no
            # longer holds: this is a CAS conflict, never a silent success.
            return self._conflict(transaction, REASON_SELECTION_CAS_CONFLICT)

        return await self._append_event(
            transaction=transaction, event_type=SELECTION_COMMITTED_EVENT,
            conflict_reason=REASON_SELECTION_IDEMPOTENCY_CONFLICT,
            payload={
                "operation": "equipment.selection",
                "owner_id": scope.owner_id,
                "actor_id": selection.actor_id,
                "actor_kind": str(DecisionActorKind.HUMAN.value),
                "selection_id": selection.selection_id,
                "line_id": selection.line_id,
                "candidate_group_id": group_id,
                "offer_id": selection.tender_id,
                "supplier_id": selection.supplier_id,
                "source_id": selection.source_id,
                "source_record_id": selection.source_record_id,
                "source_schema_version": selection.source_schema_version,
                "manual_override": selection.manual_override,
                "supersedes_selection_id": selection.supersedes_selection_id,
                "previous_selection_id": transaction.expected_prior_selection_id,
                "projection_digest": transaction.projection_digest,
                "source_revision": transaction.source_revision,
                "source_generation": transaction.source_generation,
                "payload_digest": transaction.payload_digest,
                "reason": self._redacted_reason(selection.reason),
                "reason_digest": _stable_digest(selection.reason),
                "reason_code": "human_selected_offer",
            },
        )

    async def commit_export(self, *,
                            transaction: ExportTransaction) -> EquipmentTransactionResult:
        """Append one prepared export under a single durable transaction."""
        scope = transaction.scope
        await self._assert_context(scope)
        replay = await self._replay(transaction.idempotency_key, transaction.payload_digest,
                                    EXPORT_PREPARED_EVENT, scope)
        if replay is not None:
            return replay
        export = transaction.export
        if export.actor_id != self._principal.subject_id:
            raise EquipmentDurableBlocked(REASON_PRINCIPAL_MISMATCH,
                                          "export actor is not the resolved principal")

        active = await self.load_selections(scope=scope, actor_id=export.actor_id)
        if tuple(sorted(row.selection_id for row in active)) != tuple(
                sorted(transaction.expected_selection_ids)):
            return self._conflict(transaction, REASON_EXPORT_SELECTION_SET_CHANGED)

        facts = await self._durable_selection_facts(
            scope, [row.line_id for row in export.lines])
        for row in export.lines:
            fact = facts.get(row.line_id)
            if fact is None:
                return self._conflict(transaction, REASON_EXPORT_LINE_MISMATCH)
            if fact["detached"] or fact["selected_offer_id"] != row.tender_id:
                return self._conflict(transaction, REASON_SELECTED_OFFER_DETACHED)
            if (fact["currency"] != row.currency or fact["quantity"] != row.quantity
                    or fact["unit_price"] != row.unit_price
                    or fact["supplier_key"] != row.supplier_id
                    or fact["external_key"] != row.source_record_id):
                return self._conflict(transaction, REASON_EXPORT_LINE_MISMATCH)

        return await self._append_event(
            transaction=transaction, event_type=EXPORT_PREPARED_EVENT,
            conflict_reason=REASON_EXPORT_IDEMPOTENCY_CONFLICT,
            payload={
                "operation": "equipment.procurement_export",
                "owner_id": scope.owner_id,
                "actor_id": export.actor_id,
                "export_id": export.export_id,
                "selection_digest": transaction.selection_digest,
                "selection_ids": list(export.selection_ids),
                "tender_ids": list(export.tender_ids),
                "line_count": len(export.lines),
                "currency_totals": [[currency, str(total)]
                                    for currency, total in export.currency_totals],
                "projection_digest": transaction.projection_digest,
                "source_revision": transaction.source_revision,
                "source_generation": transaction.source_generation,
                "payload_digest": transaction.payload_digest,
                "status": export.status,
            },
        )

    # ------------------------------------------------------- request binding --

    async def bind_request(self, *, scope: DomainScope, request_id: str, source_id: str,
                           source_schema_version: str | None = None) -> bool:
        """Record request→project/source binding as append-only evidence.

        Idempotent by ``(scope, request_id)``: an identical replay returns
        ``True`` without a second row; a changed payload raises.
        """
        await self._assert_context(scope)
        if scope.owner_id != self._config.owner_id:
            raise EquipmentDurableBlocked(REASON_OWNER_MISMATCH,
                                          "owner is not the configured SoT owner")
        if not isinstance(request_id, str) or not request_id.strip() or not source_id.strip():
            raise EquipmentDurableBlocked(REASON_REQUEST_BINDING_MISSING,
                                          "request and source identity are required")
        schema_version = source_schema_version or self._config.schema_version(source_id)
        if not schema_version:
            raise EquipmentDurableBlocked(UNRESOLVED_SOURCE_SCHEMA,
                                          "source schema version must be mapped explicitly")
        digest = _stable_digest({
            "operation": REQUEST_BOUND_EVENT, "scope": scope, "request_id": request_id,
            "source_id": source_id, "source_schema_version": schema_version,
        })
        key = _stable_digest({
            "operation": REQUEST_BOUND_EVENT, "scope": scope, "request_id": request_id,
        })
        existing = await self._event_row_by_key(key)
        if existing is not None:
            if self._payload(existing).get("binding_digest") != digest:
                raise EquipmentDurableBlocked(REASON_SELECTION_IDEMPOTENCY_CONFLICT,
                                              "request binding replay changed its payload")
            return True
        await self._insert_event(
            event_type=REQUEST_BOUND_EVENT, scope=scope,
            actor_sub=self._principal.subject_id, idempotency_key=key,
            payload={
                "operation": REQUEST_BOUND_EVENT,
                "owner_id": scope.owner_id,
                "request_id": request_id,
                "source_id": source_id,
                "source_schema_version": schema_version,
                "binding_digest": digest,
            },
        )
        return True

    async def selection_state(self, *, scope: DomainScope, line_id: str) -> dict[str, Any]:
        """Report whether the durable selection for one line is still intact.

        A chosen offer removed under ``ON DELETE SET NULL`` leaves
        ``selected_offer_id = NULL`` while the audit row survives.  That state is
        reported as ``detached`` so callers fail closed rather than re-select.
        """
        await self._assert_context(scope)
        rows = await self._rows(_LINE_STATE_SQL, {"line_id": line_id})
        if not rows or rows[0]["group_id"] is None:
            return {"line_id": line_id, "state": SELECTION_STATE_LINE_MISSING}
        row = rows[0]
        selected = row["selected_offer_id"]
        audited = [self._payload(item) for item
                   in await self._ledger(scope, SELECTION_COMMITTED_EVENT)]
        audited_for_line = [item for item in audited if item.get("line_id") == line_id]
        base = {"line_id": line_id, "candidate_group_id": str(row["group_id"])}
        if selected is None and audited_for_line:
            return {**base, "state": SELECTION_STATE_DETACHED,
                    "reason_code": REASON_SELECTED_OFFER_DETACHED,
                    "last_selection_id": audited_for_line[-1].get("selection_id")}
        if selected is None:
            return {**base, "state": SELECTION_STATE_UNSELECTED}
        return {**base, "state": SELECTION_STATE_SELECTED, "offer_id": str(selected)}

    # ------------------------------------------------------------- internals --

    async def _assert_context(self, scope: DomainScope) -> None:
        if not isinstance(scope, DomainScope):
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISSING, "domain scope is required")
        if (scope.tenant_id != self._principal.tenant_id
                or scope.project_id != self._principal.project_id):
            raise EquipmentDurableBlocked(
                REASON_PRINCIPAL_MISMATCH,
                "requested scope is not the server-resolved tenant/project",
            )
        if scope.owner_id != self._config.owner_id:
            raise EquipmentDurableBlocked(REASON_OWNER_MISMATCH,
                                          "requested owner is not the configured SoT owner")
        context = await self._rows(_CONTEXT_SQL, {})
        row = context[0] if context else {}
        if str(row.get("tenant_id") or "") != scope.tenant_id:
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISSING,
                                          "transaction-local tenant context is not set")
        if str(row.get("project_id") or "") != scope.project_id:
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISSING,
                                          "transaction-local project context is not set")
        if str(row.get("subject_id") or "") != self._principal.subject_id:
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISMATCH,
                                          "transaction context subject differs from the principal")
        if str(row.get("row_security") or "").lower() != "on":
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISMATCH, "row_security is not on")
        anchor = await self._rows(_PROJECT_ANCHOR_SQL, {
            "project_id": scope.project_id, "tenant_id": scope.tenant_id})
        if len(anchor) != 1:
            raise EquipmentDurableBlocked(REASON_PROJECT_ANCHOR_MISSING,
                                          "no canonical project anchor for this tenant")
        if str(anchor[0]["owner_state"]) != "active":
            raise EquipmentDurableBlocked(REASON_PROJECT_ANCHOR_MISSING,
                                          "project anchor owner state is not active")

    def _actor_kind(self) -> DecisionActorKind:
        source = str(getattr(self._principal, "resolution_source", ""))
        return (DecisionActorKind.HUMAN
                if source in {"session_cookie", "bearer_jwt_verified"}
                else DecisionActorKind.SERVICE)

    async def _request_bindings(self, scope: DomainScope) -> dict[str, dict[str, str]]:
        rows = await self._rows(_BINDINGS_SQL, {
            "event_type": REQUEST_BOUND_EVENT, "tenant_id": scope.tenant_id,
            "project_id": scope.project_id, "payload_kind": EVENT_PAYLOAD_KIND,
        })
        bindings: dict[str, dict[str, str]] = {}
        for row in rows:
            request_id = row.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                # A malformed binding proves nothing about a request; the
                # request is simply not projected.
                continue
            bindings[request_id] = {
                "source_id": str(row.get("source_id") or ""),
                "source_schema_version": str(row.get("source_schema_version") or ""),
                "binding_digest": str(row.get("binding_digest") or ""),
            }
        return bindings

    def _evidence(self, scope: DomainScope, *, source_id: str | None,
                  schema_version: str | None, supplier_id: str | None) -> DomainEvidence:
        return DomainEvidence(
            tenant_id=scope.tenant_id, owner_id=scope.owner_id, source_id=source_id,
            supplier_id=supplier_id, source_schema_version=schema_version,
            project_id=scope.project_id,
        )

    def _revision(self, requests: Sequence[Mapping[str, Any]],
                  lines: Sequence[Mapping[str, Any]],
                  groups: Sequence[Mapping[str, Any]],
                  offers: Sequence[Mapping[str, Any]],
                  budgets: Sequence[Mapping[str, Any]]) -> str:
        """One digest over every durable row the projection was built from."""
        return _stable_digest({
            "generation": self._config.writer_generation,
            "requests": sorted((str(row["id"]), str(row["title"]), str(row["status"]))
                               for row in requests),
            "lines": sorted((str(row["id"]), str(row["request_id"]), int(row["line_number"]),
                             str(row["quantity"]), str(row["unit"])) for row in lines),
            "groups": sorted((str(row["id"]), str(row["request_line_id"]),
                              str(row["selected_offer_id"]),
                              tuple(str(item) for item in (row["offer_ids"] or [])))
                             for row in groups),
            "offers": sorted((str(row["id"]), str(row["snapshot_id"]), str(row["unit_price"]),
                              str(row["available_quantity"]), str(row["status"]))
                             for row in offers),
            "budgets": sorted((str(row["id"]), str(row["amount"]), str(row["status"]))
                              for row in budgets),
        })

    def _empty_snapshot(self, scope: DomainScope) -> EquipmentDomainSnapshot:
        return EquipmentDomainSnapshot(
            scope=scope,
            source_revision=_stable_digest({"generation": self._config.writer_generation,
                                            "requests": ()}),
            requests=(), lines=(), candidate_groups=(), tenders=(),
            source_generation=self._config.writer_generation,
        )

    async def _durable_selection_facts(
        self, scope: DomainScope, line_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        del scope
        if not line_ids:
            return {}
        rows = await self._rows(_EXPORT_FACTS_SQL, {"line_ids": sorted(set(line_ids))})
        facts: dict[str, dict[str, Any]] = {}
        for row in rows:
            line_id = str(row["line_id"])
            if line_id in facts:
                # Two candidate groups for one line: the selected offer is
                # ambiguous, so the export must not proceed for that line.
                facts[line_id] = {"selected_offer_id": None, "detached": True,
                                  "quantity": Decimal("0"), "unit_price": Decimal("0"),
                                  "currency": "", "supplier_key": "", "external_key": ""}
                continue
            selected = row["selected_offer_id"]
            facts[line_id] = {
                "selected_offer_id": str(selected) if selected is not None else None,
                "detached": selected is None or row["unit_price"] is None,
                "quantity": _as_decimal(row["quantity"]),
                "unit_price": (_as_decimal(row["unit_price"])
                               if row["unit_price"] is not None else Decimal("0")),
                "currency": str(row["currency"] or ""),
                "supplier_key": str(row["supplier_key"] or ""),
                "external_key": str(row["external_key"] or ""),
            }
        return facts

    def _result(self, transaction: SelectionTransaction | ExportTransaction,
                status: EquipmentTransactionStatus, reason_code: str,
                *, durable: bool) -> EquipmentTransactionResult:
        return EquipmentTransactionResult(
            status=status,
            operation_id=f"op-{transaction.idempotency_key[7:23]}",
            idempotency_key=transaction.idempotency_key,
            payload_digest=transaction.payload_digest,
            scope=transaction.scope,
            source_revision=transaction.source_revision,
            source_generation=transaction.source_generation,
            reason_code=reason_code,
            durable=durable,
        )

    def _conflict(self, transaction: SelectionTransaction | ExportTransaction,
                  reason_code: str) -> EquipmentTransactionResult:
        return self._result(transaction, EquipmentTransactionStatus.CONFLICT,
                            reason_code, durable=False)

    async def _replay(self, idempotency_key: str, payload_digest: str, event_type: str,
                      scope: DomainScope) -> EquipmentTransactionResult | None:
        row = await self._event_row_by_key(idempotency_key)
        if row is None:
            return None
        if (str(row["event_type"]) != event_type
                or str(row["company_id"] or "") != scope.tenant_id
                or str(row["project_id"] or "") != scope.project_id):
            raise EquipmentDurableBlocked(
                REASON_CONTEXT_MISMATCH,
                "idempotency key belongs to another operation or tenant",
            )
        payload = self._payload(row)
        replayed = payload.get("payload_digest") == payload_digest
        status = (EquipmentTransactionStatus.REPLAYED if replayed
                  else EquipmentTransactionStatus.CONFLICT)
        source_revision = payload.get("source_revision")
        generation = payload.get("source_generation")
        return EquipmentTransactionResult(
            status=status,
            operation_id=str(payload.get("selection_id") or payload.get("export_id")
                             or row["id"]),
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            scope=scope,
            source_revision=source_revision if isinstance(source_revision, str) and source_revision
            else "unchanged",
            source_generation=int(generation) if isinstance(generation, int) and generation >= 0 else 0,
            reason_code="" if replayed else (
                REASON_EXPORT_IDEMPOTENCY_CONFLICT
                if event_type == EXPORT_PREPARED_EVENT
                else REASON_SELECTION_IDEMPOTENCY_CONFLICT),
            durable=replayed,
        )

    async def _append_event(self, *, transaction: SelectionTransaction | ExportTransaction,
                            event_type: str, payload: dict[str, Any],
                            conflict_reason: str) -> EquipmentTransactionResult:
        try:
            await self._insert_event(
                event_type=event_type, scope=transaction.scope,
                actor_sub=str(payload.get("actor_id") or ""),
                idempotency_key=transaction.idempotency_key, payload=payload,
            )
        except IntegrityError as error:
            if IDEMPOTENCY_CONSTRAINT not in str(getattr(error, "orig", error)):
                raise
            # A concurrent writer won the key: reconcile by reading the winner.
            winner = await self._event_row_by_key(transaction.idempotency_key)
            if winner is None:
                return self._result(transaction, EquipmentTransactionStatus.COMMIT_UNKNOWN,
                                    REASON_COMMIT_UNKNOWN, durable=False)
            if self._payload(winner).get("payload_digest") == transaction.payload_digest:
                return self._result(transaction, EquipmentTransactionStatus.REPLAYED,
                                    "", durable=True)
            return self._conflict(transaction, conflict_reason)
        return self._result(transaction, EquipmentTransactionStatus.COMMITTED, "", durable=True)

    async def _insert_event(self, *, event_type: str, scope: DomainScope, actor_sub: str,
                            idempotency_key: str, payload: dict[str, Any]) -> None:
        body = dict(payload)
        body["_kind"] = EVENT_PAYLOAD_KIND
        body.setdefault("tenant_id", scope.tenant_id)
        body.setdefault("owner_id", scope.owner_id)
        body.setdefault("project_id", scope.project_id)
        await self._scalars(_APPEND_EVENT_SQL, {
            "id": "pev_" + hashlib.sha256(
                (event_type + ":" + idempotency_key).encode("utf-8")).hexdigest()[:16],
            "event_type": event_type,
            "company_id": scope.tenant_id,
            "project_id": scope.project_id,
            "cabinet_id": self._principal.cabinet_id,
            "actor_sub": actor_sub or None,
            "idempotency_key": idempotency_key,
            "payload": json.dumps(_jsonable(body), sort_keys=True, separators=(",", ":")),
        })

    async def _event_row_by_key(self, idempotency_key: str) -> Mapping[str, Any] | None:
        rows = await self._rows(_EVENT_BY_KEY_SQL, {"idempotency_key": idempotency_key})
        return rows[0] if rows else None

    async def _ledger(self, scope: DomainScope,
                      event_type: str) -> list[Mapping[str, Any]]:
        return await self._rows(_LEDGER_SQL, {
            "event_type": event_type, "tenant_id": scope.tenant_id,
            "project_id": scope.project_id, "payload_kind": EVENT_PAYLOAD_KIND,
            "owner_id": scope.owner_id,
        })

    @staticmethod
    def _payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = row.get("payload")
        return payload if isinstance(payload, Mapping) else {}

    def _selection_from_event(self, row: Mapping[str, Any],
                              payload: Mapping[str, Any]) -> HumanSelection:
        selection_id = payload.get("selection_id")
        actor_id = payload.get("actor_id")
        line_id = payload.get("line_id")
        offer_id = payload.get("offer_id") or payload.get("tender_id")
        reason = payload.get("reason")
        projection_digest = payload.get("projection_digest")
        if not all(isinstance(value, str) and value
                   for value in (selection_id, actor_id, line_id, offer_id, reason,
                                 projection_digest)):
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISMATCH,
                                          "durable selection evidence is incomplete")
        if not _is_sha256(projection_digest):
            raise EquipmentDurableBlocked(REASON_CONTEXT_MISMATCH,
                                          "durable selection projection digest is invalid")
        supersedes = payload.get("supersedes_selection_id")
        return HumanSelection(
            actor_id=actor_id,
            line_id=line_id,
            tender_id=offer_id,
            reason=str(reason),
            selection_id=selection_id,
            projection_digest=projection_digest,
            tenant_id=str(payload.get("tenant_id") or row.get("company_id") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            project_id=str(payload.get("project_id") or row.get("project_id") or ""),
            supersedes_selection_id=(supersedes
                                     if isinstance(supersedes, str) and supersedes else None),
            manual_override=bool(payload.get("manual_override", True)),
            supplier_id=str(payload.get("supplier_id") or ""),
            source_id=str(payload.get("source_id") or ""),
            source_record_id=str(payload.get("source_record_id") or ""),
            source_schema_version=str(payload.get("source_schema_version") or ""),
        )

    def _redacted_reason(self, reason: str) -> str:
        return " ".join(reason.split())[: self._config.reason_max_length]

    async def _rows(self, statement: str,
                    params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        self._reject_forbidden(statement)
        result = await self._session.execute(text(statement), dict(params))
        return list(result.mappings().all())

    async def _scalars(self, statement: str, params: Mapping[str, Any]) -> list[Any]:
        self._reject_forbidden(statement)
        result = await self._session.execute(text(statement), dict(params))
        return [row[0] for row in result.all()]

    @staticmethod
    def _reject_forbidden(statement: str) -> None:
        upper = statement.upper()
        for fragment in _FORBIDDEN_SQL_FRAGMENTS:
            if fragment in upper:
                raise EquipmentDurableBlocked(
                    REASON_STATEMENT_FORBIDDEN,
                    "statement would mutate the append-only decision ledger",
                )


def selection_digest_of(selections: Sequence[HumanSelection]) -> str:
    """Expose the shared selection digest for callers composing export fences."""
    return selection_evidence_digest(selections)


__all__ = [
    "EVENT_PAYLOAD_KIND",
    "EXPORT_PREPARED_EVENT",
    "EquipmentDurableBlocked",
    "EquipmentDurableConfig",
    "IDEMPOTENCY_CONSTRAINT",
    "REQUEST_BOUND_EVENT",
    "SELECTION_COMMITTED_EVENT",
    "SELECTION_STATE_DETACHED",
    "SELECTION_STATE_LINE_MISSING",
    "SELECTION_STATE_SELECTED",
    "SELECTION_STATE_UNSELECTED",
    "SqlAlchemyEquipmentRepository",
    "selection_digest_of",
]
