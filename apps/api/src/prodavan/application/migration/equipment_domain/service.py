"""MIG-EQ-02 application workflow implemented only against explicit ports.

These ports are contracts, not live persistence implementations. Selection and
export preparation are durable only when an embedding application supplies its
own audited repository; this package intentionally provides no adapter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from .domain import (
    AssociationBackfill,
    CandidateGroup,
    DomainScope,
    EquipmentProjection,
    EquipmentTender,
    HumanSelection,
    ProcurementExport,
    RequestLine,
    TypedRequest,
    active_human_selections,
    build_projection,
    prepare_procurement_export,
    select_tender,
    _stable_digest,
)
from .transactions import (
    EquipmentDomainTransactionPort,
    EquipmentTransactionResult,
    ExportTransaction,
    SelectionTransaction,
    blocked_transaction_result,
    validate_transaction_result,
)


class DecisionActorKind(StrEnum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class DecisionActor:
    actor_id: str
    kind: DecisionActorKind
    tenant_id: str
    owner_id: str
    project_id: str

    def __post_init__(self) -> None:
        if not all((self.actor_id, self.tenant_id, self.owner_id, self.project_id)):
            raise ValueError("authenticated actor and tenant/owner/project scope are required")

    @property
    def scope(self) -> DomainScope:
        return DomainScope(self.tenant_id, self.owner_id, self.project_id)


@dataclass(frozen=True, slots=True)
class EquipmentDomainSnapshot:
    scope: DomainScope
    source_revision: str
    requests: tuple[TypedRequest, ...]
    lines: tuple[RequestLine, ...]
    candidate_groups: tuple[CandidateGroup, ...]
    tenders: tuple[EquipmentTender, ...]
    source_generation: int = 0

    def __post_init__(self) -> None:
        if not self.source_revision.strip():
            raise ValueError("source revision is required for deterministic projection")
        if self.source_generation < 0:
            raise ValueError("source generation cannot be negative")


@dataclass(frozen=True, slots=True)
class AssociationBatch:
    scope: DomainScope
    source_revision: str
    rows: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if not self.source_revision.strip():
            raise ValueError("source revision is required for ordered association backfill")


class EquipmentDomainReadPort(Protocol):
    async def load_snapshot(self, *, scope: DomainScope) -> EquipmentDomainSnapshot | None:
        """Return a consistent, tenant/project-bounded source snapshot."""
        ...


class EquipmentActorPort(Protocol):
    async def resolve_actor(self, *, actor_id: str, scope: DomainScope) -> DecisionActor | None:
        """Resolve authenticated identity; actor kind must come from trusted auth."""
        ...


class HumanDecisionPort(Protocol):
    async def load_selections(self, *, scope: DomainScope, actor_id: str) -> tuple[HumanSelection, ...]:
        """Read immutable explicit selections for actor and exact project scope."""
        ...


class OrderedAssociationPort(Protocol):
    async def load_batch(self, *, scope: DomainScope, owner_kind: str,
                         association_field: str) -> AssociationBatch | None:
        """Read original JSONB rows and a revision from one consistent snapshot."""
        ...

    async def verify_related_ids(self, *, scope: DomainScope, owner_kind: str,
                                 related_kind: str, related_ids: tuple[str, ...]) -> bool:
        """Verify every target exists and belongs to this exact tenant/owner/project."""
        ...

    async def append_ordered_edges(self, *, scope: DomainScope, owner_kind: str,
                                   related_kind: str, association_field: str, source_revision: str,
                                   source_digest: str,
                                   edges: tuple[tuple[str, str, int, str], ...]) -> bool:
        """Idempotent append guarded atomically by the source revision and scope."""
        ...


@dataclass(frozen=True, slots=True)
class AssociationBackfillResult:
    scope: DomainScope
    owner_kind: str
    related_kind: str
    association_field: str
    source_revision: str
    source_digest: str
    association_count: int
    edge_count: int
    edges: tuple[tuple[str, str, int, str], ...]


class EquipmentDomainService:
    """Useful read/decision/export workflow with no persistence coupling."""

    def __init__(self, *, source: EquipmentDomainReadPort,
                 actors: EquipmentActorPort, decisions: HumanDecisionPort,
                 associations: OrderedAssociationPort | None = None,
                 transaction: EquipmentDomainTransactionPort | None = None) -> None:
        self._source = source
        self._actors = actors
        self._decisions = decisions
        self._associations = associations
        self._transaction = transaction

    async def project(self, *, scope: DomainScope) -> EquipmentProjection:
        snapshot = await self._load_snapshot(scope)
        return build_projection(
            requests=snapshot.requests, lines=snapshot.lines,
            candidate_groups=snapshot.candidate_groups, tenders=snapshot.tenders,
            expected_scope=scope, source_revision=snapshot.source_revision,
            source_generation=snapshot.source_generation,
        )

    async def commit_selection(self, *, scope: DomainScope, actor_id: str,
                               selection_id: str, line_id: str, tender_id: str,
                               reason: str,
                               supersedes_selection_id: str | None = None,
                               manual_override: bool = True) -> EquipmentTransactionResult:
        """Attempt one durable human-selection transaction, or return blocked.

        This path intentionally does not call the legacy row-selection port.  A
        missing transaction adapter produces a durable-looking *blocked*
        outcome with no write, so callers cannot mistake a prepared decision
        for a committed selection.
        """
        actor = await self._human_actor(actor_id=actor_id, scope=scope)
        projection = await self.project(scope=scope)
        history = await self._decisions.load_selections(scope=scope, actor_id=actor.actor_id)
        self._assert_selection_history_scope(history=history, actor_id=actor.actor_id, scope=scope)
        same_id = next((item for item in history if item.selection_id == selection_id), None)
        selection = select_tender(
            projection=projection, actor_id=actor.actor_id, line_id=line_id,
            tender_id=tender_id, reason=reason, selection_id=selection_id,
            supersedes_selection_id=supersedes_selection_id,
            manual_override=manual_override,
        )
        if same_id is None:
            self._assert_selection_replacement(
                history=history, line_id=line_id,
                supersedes_selection_id=supersedes_selection_id,
            )
        if projection.contract.source_generation < 1:
            idempotency_key = _stable_digest({
                "operation": "equipment.selection", "scope": scope,
                "selection_id": selection.selection_id,
            })
            payload_digest = _stable_digest({
                "selection": selection,
                "projection_digest": projection.contract.input_digest,
                "source_revision": projection.contract.source_revision,
                "source_generation": projection.contract.source_generation,
            })
            return blocked_transaction_result(
                scope=scope, operation="selection",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                source_revision=projection.contract.source_revision,
                source_generation=projection.contract.source_generation,
                reason_code="equipment_source_generation_unavailable",
            )
        transaction = SelectionTransaction(
            scope=scope,
            selection=selection,
            projection_digest=projection.contract.input_digest,
            source_revision=projection.contract.source_revision,
            source_generation=projection.contract.source_generation,
            expected_prior_selection_id=supersedes_selection_id,
        )
        if self._transaction is None:
            return blocked_transaction_result(
                scope=scope, operation="selection",
                idempotency_key=transaction.idempotency_key,
                payload_digest=transaction.payload_digest,
                source_revision=transaction.source_revision,
                source_generation=transaction.source_generation,
            )
        result = await self._transaction.commit_selection(transaction=transaction)
        return validate_transaction_result(
            result=result, scope=scope,
            idempotency_key=transaction.idempotency_key,
            payload_digest=transaction.payload_digest,
            source_revision=transaction.source_revision,
            source_generation=transaction.source_generation,
        )

    async def select(self, *, scope: DomainScope, actor_id: str, selection_id: str,
                     line_id: str, tender_id: str, reason: str,
                     supersedes_selection_id: str | None = None,
                     manual_override: bool = True) -> EquipmentTransactionResult:
        """Compatibility-named entry to the atomic selection transaction only."""
        return await self.commit_selection(
            scope=scope, actor_id=actor_id, selection_id=selection_id,
            line_id=line_id, tender_id=tender_id, reason=reason,
            supersedes_selection_id=supersedes_selection_id,
            manual_override=manual_override,
        )

    async def prepare_export(self, *, scope: DomainScope, actor_id: str,
                             export_id: str) -> ProcurementExport:
        """Build a current export preview only; this method performs no write."""
        actor = await self._human_actor(actor_id=actor_id, scope=scope)
        projection = await self.project(scope=scope)
        selections = await self._decisions.load_selections(scope=scope, actor_id=actor.actor_id)
        if any(selection.actor_id != actor.actor_id for selection in selections):
            raise RuntimeError("decision repository returned another actor's selection")
        if any((selection.tenant_id, selection.owner_id, selection.project_id) != (
                scope.tenant_id, scope.owner_id, scope.project_id) for selection in selections):
            raise RuntimeError("decision repository returned a cross-scope selection")
        export = prepare_procurement_export(
            projection=projection, selections=selections, actor_id=actor.actor_id,
            export_id=export_id,
        )
        return export

    async def commit_export(self, *, scope: DomainScope, actor_id: str,
                            export_id: str) -> EquipmentTransactionResult:
        """Attempt one durable prepared-export transaction, or return blocked.

        Export preparation remains a pure snapshot.  Only the explicit
        transaction adapter may append the export/audit/idempotency record;
        this method never submits to a supplier or fabricates an artifact.
        """
        actor = await self._human_actor(actor_id=actor_id, scope=scope)
        projection = await self.project(scope=scope)
        selections = await self._decisions.load_selections(scope=scope, actor_id=actor.actor_id)
        self._assert_selection_history_scope(history=selections, actor_id=actor.actor_id, scope=scope)
        export = prepare_procurement_export(
            projection=projection, selections=selections,
            actor_id=actor.actor_id, export_id=export_id,
        )
        if projection.contract.source_generation < 1:
            return blocked_transaction_result(
                scope=scope, operation="procurement_export",
                idempotency_key=export.idempotency_key,
                payload_digest=_stable_digest(export),
                source_revision=projection.contract.source_revision,
                source_generation=projection.contract.source_generation,
                reason_code="equipment_source_generation_unavailable",
            )
        active_selections = active_human_selections(selections)
        selected_line_ids = {item.line_id for item in active_selections}
        projected_line_ids = {line.line_id for line in projection.lines}
        if selected_line_ids != projected_line_ids:
            return blocked_transaction_result(
                scope=scope, operation="procurement_export",
                idempotency_key=export.idempotency_key,
                payload_digest=_stable_digest(export),
                source_revision=projection.contract.source_revision,
                source_generation=projection.contract.source_generation,
                reason_code="equipment_selection_set_incomplete",
            )
        transaction = ExportTransaction(
            scope=scope,
            export=export,
            projection_digest=projection.contract.input_digest,
            source_revision=projection.contract.source_revision,
            source_generation=projection.contract.source_generation,
            selection_digest=export.selection_digest,
            expected_selection_ids=tuple(sorted(item.selection_id for item in active_selections)),
        )
        if self._transaction is None:
            return blocked_transaction_result(
                scope=scope, operation="procurement_export",
                idempotency_key=transaction.idempotency_key,
                payload_digest=transaction.payload_digest,
                source_revision=transaction.source_revision,
                source_generation=transaction.source_generation,
            )
        result = await self._transaction.commit_export(transaction=transaction)
        return validate_transaction_result(
            result=result, scope=scope,
            idempotency_key=transaction.idempotency_key,
            payload_digest=transaction.payload_digest,
            source_revision=transaction.source_revision,
            source_generation=transaction.source_generation,
        )

    async def select_durable(self, **kwargs: Any) -> EquipmentTransactionResult:
        """Named alias for API/application callers choosing the durable path."""
        return await self.commit_selection(**kwargs)

    async def prepare_export_durable(self, **kwargs: Any) -> EquipmentTransactionResult:
        """Named alias for API/application callers choosing the durable path."""
        return await self.commit_export(**kwargs)

    async def backfill_associations(self, *, scope: DomainScope, owner_kind: str,
                                    association_field: str, related_kind: str,
                                    id_field: str = "id") -> AssociationBackfillResult:
        if self._associations is None:
            raise RuntimeError("ordered association port is not configured")
        if not all((owner_kind.strip(), association_field.strip(), related_kind.strip(), id_field.strip())):
            raise ValueError("owner, related and association field kinds are required")
        batch = await self._associations.load_batch(
            scope=scope, owner_kind=owner_kind, association_field=association_field,
        )
        if batch is None:
            raise LookupError("association source batch is unavailable")
        if batch.scope != scope:
            raise ValueError("association batch crosses tenant, owner or project scope")
        backfill = AssociationBackfill.from_jsonb(
            batch.rows, id_field=id_field, association_field=association_field,
            kind=related_kind, expected_scope=scope,
        )
        edges = backfill.ordered_edges()
        related_ids = tuple(edge[3] for edge in edges)
        if not await self._associations.verify_related_ids(
                scope=scope, owner_kind=owner_kind, related_kind=related_kind,
                related_ids=related_ids):
            raise ValueError("association targets are missing or cross tenant/owner/project scope")
        source_digest = _digest_rows(batch.rows)
        appended = await self._associations.append_ordered_edges(
            scope=scope, owner_kind=owner_kind, related_kind=related_kind,
            association_field=association_field,
            source_revision=batch.source_revision, source_digest=source_digest, edges=edges,
        )
        if not appended:
            raise RuntimeError("association source revision changed or backfill replay conflicted")
        return AssociationBackfillResult(
            scope, owner_kind, related_kind, association_field, batch.source_revision, source_digest,
            len(backfill.associations), len(edges), edges,
        )

    async def _load_snapshot(self, scope: DomainScope) -> EquipmentDomainSnapshot:
        snapshot = await self._source.load_snapshot(scope=scope)
        if snapshot is None:
            raise LookupError("equipment domain snapshot is unavailable")
        if snapshot.scope != scope:
            raise ValueError("equipment source returned another tenant/owner/project")
        return snapshot

    async def _human_actor(self, *, actor_id: str, scope: DomainScope) -> DecisionActor:
        if not actor_id.strip():
            raise ValueError("authenticated human actor is required")
        actor = await self._actors.resolve_actor(actor_id=actor_id, scope=scope)
        if actor is None or actor.kind is not DecisionActorKind.HUMAN:
            raise PermissionError("selection and procurement export require an authenticated human actor")
        if actor.actor_id != actor_id or actor.scope != scope:
            raise PermissionError("authenticated actor scope does not match the requested project")
        return actor

    @staticmethod
    def _assert_selection_history_scope(*, history: Sequence[HumanSelection],
                                        actor_id: str, scope: DomainScope) -> None:
        if any(item.actor_id != actor_id or (item.tenant_id, item.owner_id, item.project_id) != (
                scope.tenant_id, scope.owner_id, scope.project_id) for item in history):
            raise RuntimeError("decision repository returned another actor's or another scope's selection")

    @staticmethod
    def _assert_selection_replacement(*, history: Sequence[HumanSelection], line_id: str,
                                      supersedes_selection_id: str | None) -> None:
        active_history = active_human_selections(history)
        active_for_line = [item for item in active_history if item.line_id == line_id]
        if supersedes_selection_id is None and active_for_line:
            raise ValueError("an existing line selection must be explicitly superseded")
        if supersedes_selection_id is not None and (
                len(active_for_line) != 1 or active_for_line[0].selection_id != supersedes_selection_id):
            raise ValueError("superseded selection must be the current active decision for this line")


def _digest_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "AssociationBackfillResult", "AssociationBatch", "DecisionActor", "DecisionActorKind",
    "EquipmentActorPort", "EquipmentDomainReadPort", "EquipmentDomainService",
    "EquipmentDomainSnapshot", "HumanDecisionPort", "OrderedAssociationPort",
    "EquipmentDomainTransactionPort", "EquipmentTransactionResult",
]
