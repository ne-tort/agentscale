"""MIG-EQ-02 transaction acceptance scenarios; intentionally not run.

The fakes below model adapter receipts only.  They do not stand in for a
persistence implementation: the production service must still receive a
trusted adapter with transaction-local scope, CAS and durable idempotency.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.equipment_domain.domain import (
    EquipmentDomainStatus,
    active_human_selections,
    build_projection,
    select_tender,
)
from prodavan.application.migration.equipment_domain.service import EquipmentDomainService
from prodavan.application.migration.equipment_domain.transactions import (
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
    ExportTransaction,
    SelectionTransaction,
    WIRING_BLOCKER,
)
from .acceptance_fixtures import (
    AGENT,
    HUMAN,
    LINE,
    GROUP,
    EVIDENCE,
    REQUEST,
    SCOPE,
    SNAPSHOT,
    STALE_TENDER,
    TENDER_SAME_PN_A,
    TENDER_SAME_PN_B,
)


class _Reads:
    def __init__(self, snapshot=SNAPSHOT):
        self.snapshot = snapshot

    async def load_snapshot(self, *, scope):
        return self.snapshot


class _Actors:
    def __init__(self, actor=HUMAN):
        self.actor = actor

    async def resolve_actor(self, *, actor_id, scope):
        if self.actor.actor_id != actor_id:
            return None
        return self.actor


class _Decisions:
    def __init__(self):
        self.selections = []

    async def load_selections(self, *, scope, actor_id):
        return tuple(item for item in self.selections if item.actor_id == actor_id)

    async def append_selection_if_current(self, **kwargs):
        self.selections.append(kwargs["selection"])
        return True

    async def append_export_if_current(self, **kwargs):
        return True


class _AtomicPort:
    def __init__(self, *, status=EquipmentTransactionStatus.COMMITTED,
                 durable=True):
        self.status = status
        self.durable = durable
        self.selections = []
        self.exports = []
        self._seen: dict[tuple[str, str], str] = {}
        self.fail_after_commit = False

    async def load_selections(self, *, scope, actor_id):
        return tuple(transaction.selection for transaction in self.selections
                     if transaction.selection.actor_id == actor_id
                     and (transaction.selection.tenant_id, transaction.selection.owner_id,
                          transaction.selection.project_id)
                     == (scope.tenant_id, scope.owner_id, scope.project_id))

    async def commit_selection(self, *, transaction: SelectionTransaction):
        key = ("selection", transaction.idempotency_key)
        previous_digest = self._seen.get(key)
        if previous_digest is not None:
            status = (EquipmentTransactionStatus.REPLAYED
                      if previous_digest == transaction.payload_digest
                      else EquipmentTransactionStatus.CONFLICT)
            return EquipmentTransactionResult(
                status, "selection-replay", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
                durable=status is EquipmentTransactionStatus.REPLAYED,
            )
        previous_selection = next((row for row in active_human_selections(tuple(
            selection_transaction.selection for selection_transaction in self.selections
        )) if row.line_id == transaction.selection.line_id), None)
        expected_prior = transaction.expected_prior_selection_id
        if (previous_selection is not None and previous_selection.selection_id != expected_prior
                or previous_selection is None and expected_prior is not None):
            return EquipmentTransactionResult(
                EquipmentTransactionStatus.CONFLICT, "selection-cas-conflict",
                transaction.idempotency_key, transaction.payload_digest,
                transaction.scope, transaction.source_revision,
                transaction.source_generation, reason_code="active_selection_cas_conflict",
                durable=False,
            )
        self._seen[key] = transaction.payload_digest
        self.selections.append(transaction)
        if self.fail_after_commit:
            return EquipmentTransactionResult(
                EquipmentTransactionStatus.COMMIT_UNKNOWN,
                "selection-unknown", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
            )
        return EquipmentTransactionResult(
            self.status, "selection-op", transaction.idempotency_key,
            transaction.payload_digest, transaction.scope,
            transaction.source_revision, transaction.source_generation,
            durable=self.durable,
        )

    async def commit_export(self, *, transaction: ExportTransaction):
        key = ("export", transaction.idempotency_key)
        previous_digest = self._seen.get(key)
        if previous_digest is not None:
            status = (EquipmentTransactionStatus.REPLAYED
                      if previous_digest == transaction.payload_digest
                      else EquipmentTransactionStatus.CONFLICT)
            return EquipmentTransactionResult(
                status, "export-replay", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
                durable=status is EquipmentTransactionStatus.REPLAYED,
            )
        self._seen[key] = transaction.payload_digest
        self.exports.append(transaction)
        if self.fail_after_commit:
            return EquipmentTransactionResult(
                EquipmentTransactionStatus.COMMIT_UNKNOWN,
                "export-unknown", transaction.idempotency_key,
                transaction.payload_digest, transaction.scope,
                transaction.source_revision, transaction.source_generation,
            )
        return EquipmentTransactionResult(
            self.status, "export-op", transaction.idempotency_key,
            transaction.payload_digest, transaction.scope,
            transaction.source_revision, transaction.source_generation,
            durable=self.durable,
        )


def _service(*, snapshot=SNAPSHOT, actor=HUMAN, transaction=None):
    decisions = transaction if transaction is not None else _Decisions()
    return EquipmentDomainService(
        source=_Reads(snapshot), actors=_Actors(actor), decisions=decisions,
        transaction=transaction,
    ), decisions


@pytest.mark.asyncio
async def test_transactionless_selection_and_export_are_fail_closed_blocked_outcomes():
    service, decisions = _service()
    outcome = await service.select(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Human checked quote",
    )
    assert outcome.status is EquipmentTransactionStatus.BLOCKED
    assert outcome.reason_code == WIRING_BLOCKER
    assert outcome.durable is False
    assert decisions.selections == []


@pytest.mark.asyncio
async def test_selection_transaction_carries_manual_override_and_exact_generation_fence():
    port = _AtomicPort()
    service, _ = _service(transaction=port)
    outcome = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Manager override",
        manual_override=True,
    )
    assert outcome.status is EquipmentTransactionStatus.COMMITTED
    assert outcome.durable
    transaction = port.selections[0]
    assert transaction.selection.manual_override is True
    assert transaction.source_revision == SNAPSHOT.source_revision
    assert transaction.source_generation == SNAPSHOT.source_generation
    assert transaction.idempotency_key.startswith("sha256:")
    assert transaction.payload_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_replay_is_safe_but_changed_payload_and_duplicate_line_are_not_success():
    port = _AtomicPort()
    service, _ = _service(transaction=port)
    first = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Checked",
    )
    replay = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Checked",
    )
    assert first.status is EquipmentTransactionStatus.COMMITTED
    assert replay.status is EquipmentTransactionStatus.REPLAYED
    assert len(port.selections) == 1
    assert port.selections[0].idempotency_key.startswith("sha256:")
    assert port.selections[0].payload_digest.startswith("sha256:")
    changed = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Changed evidence",
    )
    assert changed.status is EquipmentTransactionStatus.CONFLICT
    assert changed.durable is False


@pytest.mark.asyncio
async def test_atomic_port_rejects_racing_second_active_selection_for_same_line():
    port = _AtomicPort()
    service, _ = _service(transaction=port)
    first = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="First concurrent choice",
    )
    assert first.status is EquipmentTransactionStatus.COMMITTED
    projection = await service.project(scope=SCOPE)
    second_selection = select_tender(
        projection=projection, actor_id="employee-a", selection_id="selection-race",
        line_id="line-a", tender_id="tender-b", reason="Racing concurrent choice",
    )
    transaction = SelectionTransaction(
        scope=SCOPE,
        selection=second_selection,
        projection_digest=projection.contract.input_digest,
        source_revision=projection.contract.source_revision,
        source_generation=projection.contract.source_generation,
        expected_prior_selection_id=None,
    )
    result = await port.commit_selection(transaction=transaction)
    assert result.status is EquipmentTransactionStatus.CONFLICT
    assert result.reason_code == "active_selection_cas_conflict"
    assert len(port.selections) == 1


@pytest.mark.asyncio
async def test_stale_generation_and_commit_unknown_fail_closed():
    stale_snapshot = replace(SNAPSHOT, source_generation=2)
    port = _AtomicPort()
    service, _ = _service(snapshot=stale_snapshot, transaction=port)
    outcome = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Checked at generation two",
    )
    assert outcome.source_generation == 2
    port.fail_after_commit = True
    with pytest.raises(RuntimeError, match="unknown"):
        await service.commit_selection(
            scope=SCOPE, actor_id="employee-a", selection_id="selection-b",
            line_id="line-a", tender_id="tender-a", reason="Uncertain commit",
            supersedes_selection_id="selection-a",
        )
    port.fail_after_commit = False
    replay = await service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-b",
        line_id="line-a", tender_id="tender-a", reason="Uncertain commit",
        supersedes_selection_id="selection-a",
    )
    assert replay.status is EquipmentTransactionStatus.REPLAYED


@pytest.mark.asyncio
async def test_export_transaction_preserves_selected_membership_and_reconciled_total():
    port = _AtomicPort()
    service, decisions = _service(transaction=port)
    await service.select(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Selected by human",
    )
    outcome = await service.commit_export(
        scope=SCOPE, actor_id="employee-a", export_id="export-a",
    )
    assert outcome.status is EquipmentTransactionStatus.COMMITTED
    transaction = port.exports[0]
    assert transaction.expected_selection_ids == ("selection-a",)
    assert transaction.export.selection_ids == ("selection-a",)
    assert transaction.export.lines[0].total == LINE.quantity * TENDER_SAME_PN_A.unit_price
    assert transaction.export.lines[0].source_revision == SNAPSHOT.source_revision
    assert transaction.export.lines[0].source_generation == SNAPSHOT.source_generation
    assert transaction.export.lines[0].manual_override is True
    assert transaction.export.currency_totals == (("RUB", transaction.export.lines[0].total),)
    assert transaction.export.idempotency_key
    # ``SelectionTransaction`` is the fence around the decision, not the decision
    # itself, so the override marker lives on the wrapped ``HumanSelection`` (see
    # ``test_selection_transaction_carries_manual_override_and_exact_generation_fence``).
    # Reading it off the transaction reached for a field the fence never mirrors.
    assert decisions.selections[0].selection.manual_override is True


@pytest.mark.asyncio
async def test_export_transaction_blocks_when_any_projected_line_is_unselected():
    line_b = replace(LINE, line_id="line-b", ordinal=1, description="Second pump")
    group_b = replace(GROUP, group_id="group-b", line_id="line-b", ordinal=1)
    tender_b = replace(
        TENDER_SAME_PN_B, line_id="line-b", group_id="group-b", ordinal=0,
        source_record_id="record-b",
    )
    snapshot = replace(
        SNAPSHOT, lines=(LINE, line_b), candidate_groups=(GROUP, group_b),
        tenders=(TENDER_SAME_PN_A, tender_b),
    )
    port = _AtomicPort()
    service, _ = _service(snapshot=snapshot, transaction=port)
    await service.select(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Only first line reviewed",
    )
    outcome = await service.commit_export(
        scope=SCOPE, actor_id="employee-a", export_id="partial-export",
    )
    assert outcome.status is EquipmentTransactionStatus.BLOCKED
    assert outcome.reason_code == "equipment_selection_set_incomplete"
    assert port.exports == []


@pytest.mark.asyncio
async def test_duplicate_export_replays_and_changed_selection_is_conflict():
    port = _AtomicPort()
    service, _ = _service(transaction=port)
    await service.select(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-a",
        line_id="line-a", tender_id="tender-a", reason="Supplier A reviewed",
    )
    port.fail_after_commit = True
    with pytest.raises(RuntimeError, match="unknown"):
        await service.commit_export(
            scope=SCOPE, actor_id="employee-a", export_id="export-a",
        )
    port.fail_after_commit = False
    replay = await service.commit_export(
        scope=SCOPE, actor_id="employee-a", export_id="export-a",
    )
    assert replay.status is EquipmentTransactionStatus.REPLAYED
    assert len(port.exports) == 1

    await service.select(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-b",
        line_id="line-a", tender_id="tender-b", reason="Supplier B override",
        supersedes_selection_id="selection-a",
    )
    changed = await service.commit_export(
        scope=SCOPE, actor_id="employee-a", export_id="export-a",
    )
    assert changed.status is EquipmentTransactionStatus.CONFLICT
    assert changed.durable is False
    assert len(port.exports) == 1


def test_duplicate_line_group_vendor_source_record_blocks_projection():
    duplicate = replace(TENDER_SAME_PN_A, tender_id="tender-duplicate")
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(TENDER_SAME_PN_A, duplicate), expected_scope=SCOPE,
        source_revision="revision-duplicate",
    )
    assert projection.status is EquipmentDomainStatus.BLOCKED
    assert "duplicate_tender_source_identity" in projection.blocked_reasons
    assert projection.tenders == ()


def test_cross_tenant_tender_rows_are_blocked_before_positive_projection():
    cross_tenant = replace(
        TENDER_SAME_PN_A,
        tender_id="tender-cross-tenant",
        evidence=replace(EVIDENCE, tenant_id="tenant-b"),
    )
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(cross_tenant,), expected_scope=SCOPE,
        source_revision="revision-cross-tenant",
    )
    assert projection.status is EquipmentDomainStatus.BLOCKED
    assert "tender_tenant_owner_project_mismatch" in projection.blocked_reasons
    assert projection.tenders == ()


def test_stale_offer_is_visible_but_never_a_selection_member():
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(STALE_TENDER,), expected_scope=SCOPE,
        source_revision="revision-stale",
    )
    assert projection.status is EquipmentDomainStatus.READY
    with pytest.raises(ValueError, match="stale"):
        select_tender(
            projection=projection, actor_id="employee-a", line_id="line-a",
            tender_id=STALE_TENDER.tender_id, reason="Rejected stale offer",
            selection_id="selection-stale",
        )


def test_fresh_offer_without_availability_is_visible_but_not_selectable():
    unknown = replace(
        TENDER_SAME_PN_A,
        tender_id="tender-unknown-availability",
        available_quantity=None,
    )
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(unknown,), expected_scope=SCOPE,
        source_revision="revision-availability",
    )
    assert projection.status is EquipmentDomainStatus.READY
    with pytest.raises(ValueError, match="unavailable"):
        select_tender(
            projection=projection, actor_id="employee-a", line_id="line-a",
            tender_id=unknown.tender_id, reason="Availability unconfirmed",
            selection_id="selection-unknown-availability",
        )


@pytest.mark.asyncio
async def test_non_human_transaction_path_is_denied_before_port_call():
    port = _AtomicPort()
    service, _ = _service(actor=AGENT, transaction=port)
    with pytest.raises(PermissionError, match="authenticated human"):
        await service.commit_selection(
            scope=SCOPE, actor_id="agent-a", selection_id="selection-agent",
            line_id="line-a", tender_id="tender-a", reason="Agent choice",
        )
    assert port.selections == []


@pytest.mark.asyncio
async def test_generation_and_revision_are_in_digest_and_missing_generation_blocks_writes():
    first_service, _ = _service(snapshot=replace(SNAPSHOT, source_generation=1))
    next_service, _ = _service(snapshot=replace(SNAPSHOT, source_generation=2))
    first_projection = await first_service.project(scope=SCOPE)
    next_projection = await next_service.project(scope=SCOPE)
    assert first_projection.contract.input_digest != next_projection.contract.input_digest

    port = _AtomicPort()
    missing_service, _ = _service(
        snapshot=replace(SNAPSHOT, source_generation=0), transaction=port,
    )
    outcome = await missing_service.commit_selection(
        scope=SCOPE, actor_id="employee-a", selection_id="selection-no-generation",
        line_id="line-a", tender_id="tender-a", reason="Generation absent",
    )
    assert outcome.status is EquipmentTransactionStatus.BLOCKED
    assert outcome.reason_code == "equipment_source_generation_unavailable"
    assert port.selections == []
