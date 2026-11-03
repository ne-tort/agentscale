"""MIG-EQ-02 port-driven application-flow acceptance; not run under freeze."""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.equipment_domain.domain import (
    EquipmentDomainStatus,
    active_human_selections,
)
from prodavan.application.migration.equipment_domain.service import (
    DecisionActor,
    DecisionActorKind,
    EquipmentDomainService,
)
from prodavan.application.migration.equipment_domain.transactions import (
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
    ExportTransaction,
    SelectionTransaction,
)
from .acceptance_fixtures import (
    AGENT,
    ASSOCIATION_BATCH,
    HUMAN,
    OTHER_PROJECT,
    SCOPE,
    SNAPSHOT,
)


class ReadPort:
    def __init__(self, snapshot=SNAPSHOT):
        self.snapshot = snapshot
        self.calls = []

    async def load_snapshot(self, *, scope):
        self.calls.append(scope)
        return self.snapshot


class ActorPort:
    def __init__(self, actor=HUMAN):
        self.actor = actor

    async def resolve_actor(self, *, actor_id, scope):
        return self.actor if self.actor.actor_id == actor_id else None


class DecisionPort:
    def __init__(self):
        self.selections = []
        self.exports = []
        self.accept = True
        self._selection_digests = {}
        self._export_digests = {}

    async def load_selections(self, *, scope, actor_id):
        return tuple(item for item in self.selections if item.actor_id == actor_id)

    async def commit_selection(self, *, transaction: SelectionTransaction):
        prior_digest = self._selection_digests.get(transaction.idempotency_key)
        if prior_digest is not None:
            status = (EquipmentTransactionStatus.REPLAYED
                      if prior_digest == transaction.payload_digest
                      else EquipmentTransactionStatus.CONFLICT)
            return EquipmentTransactionResult(
                status, "selection-replay", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
                durable=status is EquipmentTransactionStatus.REPLAYED,
            )
        if not self.accept:
            status = EquipmentTransactionStatus.CONFLICT
        else:
            status = EquipmentTransactionStatus.COMMITTED
            active = active_human_selections(self.selections)
            for row in active:
                if row.line_id == transaction.selection.line_id:
                    if transaction.expected_prior_selection_id != row.selection_id:
                        status = EquipmentTransactionStatus.CONFLICT
                        break
            if status is EquipmentTransactionStatus.COMMITTED:
                self._selection_digests[transaction.idempotency_key] = transaction.payload_digest
                self.selections.append(transaction.selection)
        return EquipmentTransactionResult(
            status, "selection-op", transaction.idempotency_key,
            transaction.payload_digest, transaction.scope,
            transaction.source_revision, transaction.source_generation,
            durable=status is EquipmentTransactionStatus.COMMITTED,
        )

    async def commit_export(self, *, transaction: ExportTransaction):
        prior_digest = self._export_digests.get(transaction.idempotency_key)
        if prior_digest is not None:
            status = (EquipmentTransactionStatus.REPLAYED
                      if prior_digest == transaction.payload_digest
                      else EquipmentTransactionStatus.CONFLICT)
            return EquipmentTransactionResult(
                status, "export-replay", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
                durable=status is EquipmentTransactionStatus.REPLAYED,
            )
        active = active_human_selections(self.selections)
        active_ids = tuple(sorted(row.selection_id for row in active))
        if (not self.accept or active_ids != tuple(sorted(transaction.expected_selection_ids))):
            status = EquipmentTransactionStatus.CONFLICT
        else:
            status = EquipmentTransactionStatus.COMMITTED
            self._export_digests[transaction.idempotency_key] = transaction.payload_digest
            self.exports.append(transaction)
        return EquipmentTransactionResult(
            status, "export-op", transaction.idempotency_key,
            transaction.payload_digest, transaction.scope,
            transaction.source_revision, transaction.source_generation,
            durable=status is EquipmentTransactionStatus.COMMITTED,
        )


class AssociationPort:
    def __init__(self, batch=ASSOCIATION_BATCH, result=True):
        self.batch, self.result, self.calls = batch, result, []

    async def load_batch(self, *, scope, owner_kind, association_field):
        return self.batch

    async def verify_related_ids(self, *, scope, owner_kind, related_kind, related_ids):
        self.verified_related_ids = (scope, owner_kind, related_kind, related_ids)
        return True

    async def append_ordered_edges(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _service(*, snapshot=SNAPSHOT, actor=HUMAN, decisions=None, associations=None):
    reads = ReadPort(snapshot)
    decisions = decisions or DecisionPort()
    service = EquipmentDomainService(source=reads, actors=ActorPort(actor),
                                     decisions=decisions, associations=associations,
                                     transaction=decisions)
    return service, reads, decisions


@pytest.mark.asyncio
async def test_projection_select_export_application_journey_uses_ports() -> None:
    service, reads, decisions = _service()
    projection = await service.project(scope=SCOPE)
    assert projection.status is EquipmentDomainStatus.READY
    selected = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote")
    export = await service.prepare_export(scope=SCOPE, actor_id="employee-a", export_id="export-a")
    assert selected.status is EquipmentTransactionStatus.COMMITTED
    assert decisions.selections[0].selection_id == "sel-a"
    assert export.selection_ids == ("sel-a",)
    assert export.lines[0].supplier_id == "supplier-a"
    assert export.lines[0].total == export.currency_totals[0][1]
    assert reads.calls == [SCOPE, SCOPE, SCOPE]


@pytest.mark.asyncio
async def test_agent_actor_is_rejected_before_selection_or_export_writes() -> None:
    service, _, decisions = _service(actor=AGENT)
    with pytest.raises(PermissionError, match="authenticated human"):
        await service.select(scope=SCOPE, actor_id="agent-a", selection_id="sel-a",
            line_id="line-a", tender_id="tender-a", reason="automated best offer")
    assert decisions.selections == [] and decisions.exports == []


@pytest.mark.asyncio
async def test_wrong_actor_project_and_wrong_snapshot_scope_fail_closed() -> None:
    wrong_actor = DecisionActor("employee-a", DecisionActorKind.HUMAN,
                                "tenant-a", "owner-a", OTHER_PROJECT.project_id)
    service, _, _ = _service(actor=wrong_actor)
    with pytest.raises(PermissionError, match="scope"):
        await service.prepare_export(scope=SCOPE, actor_id="employee-a", export_id="x")

    service, _, _ = _service(snapshot=replace(SNAPSHOT, scope=OTHER_PROJECT))
    with pytest.raises(ValueError, match="another tenant/owner/project"):
        await service.project(scope=SCOPE)


@pytest.mark.asyncio
async def test_selection_transaction_conflict_does_not_claim_a_persisted_decision() -> None:
    decisions = DecisionPort()
    decisions.accept = False
    service, _, _ = _service(decisions=decisions)
    outcome = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote")
    assert outcome.status is EquipmentTransactionStatus.CONFLICT
    assert outcome.durable is False
    assert decisions.selections == []


@pytest.mark.asyncio
async def test_export_preview_is_read_only_and_transaction_conflict_is_not_success() -> None:
    decisions = DecisionPort()
    service, _, _ = _service(decisions=decisions)
    await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote")
    preview = await service.prepare_export(scope=SCOPE, actor_id="employee-a", export_id="x")
    assert preview.status == "prepared"
    assert decisions.exports == []
    decisions.accept = False
    outcome = await service.commit_export(scope=SCOPE, actor_id="employee-a", export_id="x")
    assert outcome.status is EquipmentTransactionStatus.CONFLICT
    assert outcome.durable is False


@pytest.mark.asyncio
async def test_replacing_selection_requires_current_explicit_supersession() -> None:
    service, _, decisions = _service()
    first = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human reviewed supplier A")
    first_selection = decisions.selections[0]
    assert first.status is EquipmentTransactionStatus.COMMITTED
    with pytest.raises(ValueError, match="explicitly superseded"):
        await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-b",
            line_id="line-a", tender_id="tender-b", reason="Changed decision")
    second = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-b",
        line_id="line-a", tender_id="tender-b", reason="Changed decision",
        supersedes_selection_id=first_selection.selection_id)
    second_selection = decisions.selections[-1]
    export = await service.prepare_export(scope=SCOPE, actor_id="employee-a", export_id="export-b")
    assert second.status is EquipmentTransactionStatus.COMMITTED
    assert second_selection.supersedes_selection_id == "sel-a"
    assert export.tender_ids == ("tender-b",)
    assert [row.selection_id for row in decisions.selections] == ["sel-a", "sel-b"]


@pytest.mark.asyncio
async def test_selection_id_replay_is_idempotent_and_changed_payload_conflicts() -> None:
    service, _, decisions = _service()
    first = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote")
    replay = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote")
    assert first.status is EquipmentTransactionStatus.COMMITTED
    assert replay.status is EquipmentTransactionStatus.REPLAYED
    assert len(decisions.selections) == 1
    conflict = await service.select(scope=SCOPE, actor_id="employee-a", selection_id="sel-a",
        line_id="line-a", tender_id="tender-a", reason="Changed immutable reason")
    assert conflict.status is EquipmentTransactionStatus.CONFLICT


@pytest.mark.asyncio
async def test_ordered_jsonb_backfill_writes_exact_order_under_revision_fence() -> None:
    port = AssociationPort()
    service, _, _ = _service(associations=port)
    result = await service.backfill_associations(scope=SCOPE, owner_kind="request_line",
        association_field="candidate_group_ids", related_kind="candidate_group")
    assert result.edges == (
        ("line-a", "candidate_group", 0, "group-z"),
        ("line-a", "candidate_group", 1, "group-a"),
        ("line-a", "candidate_group", 2, "group-z"),
    )
    assert port.calls[0]["source_revision"] == "revision-7"
    assert port.calls[0]["source_digest"] == result.source_digest
    assert port.calls[0]["edges"] == result.edges
    assert port.verified_related_ids == (SCOPE, "request_line", "candidate_group", ("group-z", "group-a", "group-z"))


@pytest.mark.asyncio
async def test_association_compare_and_set_failure_and_cross_scope_batch_are_not_success() -> None:
    port = AssociationPort(result=False)
    service, _, _ = _service(associations=port)
    with pytest.raises(RuntimeError, match="source revision changed"):
        await service.backfill_associations(scope=SCOPE, owner_kind="request_line",
            association_field="candidate_group_ids", related_kind="candidate_group")

    port = AssociationPort(batch=replace(ASSOCIATION_BATCH, scope=OTHER_PROJECT))
    service, _, _ = _service(associations=port)
    with pytest.raises(ValueError, match="crosses tenant"):
        await service.backfill_associations(scope=SCOPE, owner_kind="request_line",
            association_field="candidate_group_ids", related_kind="candidate_group")
