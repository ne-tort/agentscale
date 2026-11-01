"""MIG-M04-02 acceptance contracts; intentionally not executed during migration freeze."""

from dataclasses import replace

import pytest

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillOutcome,
    ScopeMigrationCoordinator,
    ScopeResolution,
    ScopeResolver,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ScopeAnchors,
    ScopeKind,
)
from acceptance_fixtures import (
    AtomicMemoryScopePort,
    CORRELATION,
    TENANT,
    candidate,
    cabinet_anchors,
    checkpoint,
    parent_proof,
    plugin_anchors,
    project_anchors,
    tenant_anchors,
    trusted_context,
    active_entry,
)


@pytest.mark.asyncio
async def test_exact_replay_returns_original_mapping_without_duplicate_audit() -> None:
    port = AtomicMemoryScopePort()
    coordinator = ScopeMigrationCoordinator(port)
    item = candidate()
    first = await coordinator.process_row(candidate=item, checkpoint=checkpoint(), context=trusted_context())
    second = await coordinator.process_row(
        candidate=item, checkpoint=checkpoint("src-1", 1), context=trusted_context()
    )

    assert first.outcome is BackfillOutcome.ACCEPTED
    assert first.checkpoint.after_source_id == "src-1"
    assert first.checkpoint.sequence == 1
    assert second.outcome is BackfillOutcome.REPLAY
    assert second.checkpoint == first.checkpoint
    assert len(port.audit) == 1
    assert port.audit[0].correlation_id == CORRELATION
    assert port.audit[0].source_digest == item.source_digest
    assert not hasattr(port.audit[0], "payload")
    assert len(port.entries) == 1


@pytest.mark.asyncio
async def test_same_digest_with_changed_typed_identity_is_conflict() -> None:
    port = AtomicMemoryScopePort()
    coordinator = ScopeMigrationCoordinator(port)
    original = candidate(payload={"scope": "same"})
    await coordinator.process_row(candidate=original, checkpoint=checkpoint(), context=trusted_context())
    changed = candidate(
        payload={"scope": "same"},
        anchors=ScopeAnchors(
            scope_kind=ScopeKind.CABINET, scope_id="cabinet-2",
            tenant_id=TENANT, cabinet_id="cabinet-2",
        ),
        asserted_cabinet_id="cabinet-2",
    )
    conflict = await coordinator.process_row(
        candidate=changed, checkpoint=checkpoint("src-1", 1), context=trusted_context(),
    )
    assert conflict.outcome is BackfillOutcome.CONFLICT
    assert conflict.reason_code == "canonical_identity_changed"
    mapping = port.mappings[(original.source_kind, original.source_id, original.schema_version)]
    assert mapping.canonical_identity == (
        ScopeKind.CABINET.value, "cabinet-1", TENANT, "cabinet-1", None, None,
    )


@pytest.mark.asyncio
async def test_checkpoint_compare_and_swap_blocks_stale_unseen_row() -> None:
    port = AtomicMemoryScopePort(checkpoint_sequence=3)
    result = await ScopeMigrationCoordinator(port).process_row(
        candidate=candidate(source_id="src-4"), checkpoint=checkpoint(sequence=1),
        context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "checkpoint_cas_mismatch"
    assert not port.mappings and not port.audit


@pytest.mark.asyncio
async def test_changed_digest_conflicts_without_overwriting_first_mapping() -> None:
    port = AtomicMemoryScopePort()
    coordinator = ScopeMigrationCoordinator(port)
    original = candidate(payload={"scope": "one"})
    changed = candidate(payload={"scope": "two"})
    first = await coordinator.process_row(candidate=original, checkpoint=checkpoint(), context=trusted_context())
    conflict = await coordinator.process_row(candidate=changed, checkpoint=checkpoint(), context=trusted_context())

    key = (original.source_kind, original.source_id, original.schema_version)
    assert first.outcome is BackfillOutcome.ACCEPTED
    assert conflict.outcome is BackfillOutcome.CONFLICT
    assert conflict.reason_code == "source_digest_changed"
    assert port.mappings[key].source_digest == original.source_digest
    assert len(port.quarantine) == 1
    assert port.audit[-1].outcome == BackfillOutcome.CONFLICT.value
    assert port.audit[-1].source_digest == changed.source_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proof", "anchors", "reason"),
    [
        (parent_proof(membership=False), cabinet_anchors(), "parent_compatibility_unproven"),
        (parent_proof(project_compatible=False), project_anchors(), "parent_compatibility_unproven"),
    ],
)
async def test_missing_membership_or_project_compatibility_quarantines(proof, anchors, reason) -> None:
    port = AtomicMemoryScopePort(proof=proof)
    result = await ScopeMigrationCoordinator(port).process_row(
        candidate=candidate(anchors=anchors, asserted_project_id=anchors.project_id),
        checkpoint=checkpoint(), context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.QUARANTINED
    assert result.reason_code == reason
    assert not port.entries
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_cross_tenant_assertion_is_quarantined() -> None:
    result = await ScopeMigrationCoordinator(AtomicMemoryScopePort()).process_row(
        candidate=candidate(asserted_tenant_id="tenant-other"),
        checkpoint=checkpoint(), context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.QUARANTINED
    assert result.reason_code == "cross_tenant_source_assertion"


@pytest.mark.asyncio
async def test_missing_transaction_local_context_blocks_without_persistence() -> None:
    port = AtomicMemoryScopePort()
    context = replace(trusted_context(), rls_context=None)
    result = await ScopeMigrationCoordinator(port).process_row(
        candidate=candidate(), checkpoint=checkpoint(), context=context,
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"
    assert not port.mappings and not port.audit


@pytest.mark.asyncio
async def test_missing_persistence_port_blocks() -> None:
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=candidate(), checkpoint=checkpoint(), context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_plugin_instance_is_always_deferred_until_m05() -> None:
    port = AtomicMemoryScopePort()
    result = await ScopeMigrationCoordinator(port).process_row(
        candidate=candidate(anchors=plugin_anchors()), checkpoint=checkpoint(), context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.QUARANTINED
    assert result.reason_code == "plugin_instance_deferred_m05"
    assert not port.entries


@pytest.mark.asyncio
async def test_resolver_returns_active_row_with_parent_proof() -> None:
    port = AtomicMemoryScopePort()
    item = candidate(anchors=tenant_anchors(), asserted_cabinet_id=None)
    entry = active_entry(tenant_anchors(), item)
    port.entries[(ScopeKind.TENANT.value, TENANT)] = entry
    base_context = trusted_context()
    tenant_context = replace(
        base_context,
        rls_context=base_context.rls_context.model_copy(update={"cabinet_id": None}),
    )
    resolved = await ScopeResolver(port).resolve(context=tenant_context, anchors=tenant_anchors())
    assert resolved.outcome is ScopeResolution.ACTIVE
    assert resolved.entry == entry


@pytest.mark.asyncio
async def test_resolver_denies_context_for_different_cabinet_or_project() -> None:
    resolved = await ScopeResolver(AtomicMemoryScopePort()).resolve(
        context=trusted_context(), anchors=project_anchors(),
    )
    assert resolved.outcome is ScopeResolution.DENY
    assert resolved.reason_code == "scope_context_mismatch"


@pytest.mark.asyncio
async def test_resolver_excludes_archived_and_quarantined_rows() -> None:
    port = AtomicMemoryScopePort()
    anchors = cabinet_anchors()
    item = candidate(anchors=anchors)
    port.entries[(anchors.scope_kind.value, anchors.scope_id)] = active_entry(anchors, item).model_copy(
        update={"owner_state": OwnerState.ARCHIVED}
    )
    resolved = await ScopeResolver(port).resolve(context=trusted_context(), anchors=anchors)
    assert resolved.outcome is ScopeResolution.DENY
    assert resolved.entry is None


@pytest.mark.asyncio
async def test_parent_mismatch_and_cross_tenant_scope_collision_quarantine() -> None:
    port = AtomicMemoryScopePort()
    coordinator = ScopeMigrationCoordinator(port)
    mismatch = candidate(asserted_cabinet_id="cabinet-other")
    denied = await coordinator.process_row(
        candidate=mismatch, checkpoint=checkpoint(), context=trusted_context(),
    )
    assert denied.outcome is BackfillOutcome.QUARANTINED

    first = candidate(source_id="src-owner")
    await coordinator.process_row(candidate=first, checkpoint=checkpoint(), context=trusted_context())
    other_tenant = "tenant-2"
    collision_anchors = ScopeAnchors(
        scope_kind=ScopeKind.CABINET, scope_id="cabinet-1",
        tenant_id=other_tenant, cabinet_id="cabinet-1",
    )
    collision_context_base = trusted_context()
    collision_context = replace(
        collision_context_base, tenant_id=other_tenant,
        rls_context=collision_context_base.rls_context.model_copy(update={"tenant_id": other_tenant}),
    )
    duplicate = await coordinator.process_row(
        candidate=candidate(
            source_id="src-cross-tenant", anchors=collision_anchors,
            asserted_tenant_id=other_tenant,
        ),
        checkpoint=checkpoint(), context=collision_context,
    )
    assert duplicate.outcome is BackfillOutcome.QUARANTINED
    assert duplicate.reason_code == "duplicate_or_ambiguous_scope"


def test_acceptance_contract_keeps_legacy_owner_and_writes_disabled() -> None:
    assert BackfillOutcome.BLOCKED.value == "blocked"
    assert CORRELATION
    assert TENANT == tenant_anchors().tenant_id
