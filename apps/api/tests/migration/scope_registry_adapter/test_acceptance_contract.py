"""Acceptance contract for MIG-M04-ADAPTERS-01; intentionally not executed."""

from dataclasses import replace

import pytest

from prodavan.application.migration.scope_registry_adapter import (
    ScopeAdapterOutcome,
    ScopeRegistryApplicationAdapter,
)
from prodavan.application.scope_registry.contracts import OwnerState, ScopeKind
from prodavan.application.migration.scope_registry_adapter.ports import ScopeRegistryEvidenceIds
from .acceptance_fixtures import (
    accepted_entry,
    accepted_gate,
    cabinet_anchors,
    DIGEST,
    evidence_ids,
    parent_proof,
    parent_evidence,
    plugin_anchors,
    project_anchors,
    registry_read,
    tenant_anchors,
    trusted_context,
    FakeTransaction,
    FakeTransactionManager,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("anchors_factory", [tenant_anchors, cabinet_anchors, project_anchors])
async def test_active_typed_scope_returns_exact_evidence_and_uses_one_transaction(anchors_factory):
    anchors = anchors_factory()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    manager = FakeTransactionManager(transaction)

    result = await ScopeRegistryApplicationAdapter(manager).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.ACTIVE
    assert result.entry == accepted_entry(anchors)
    assert result.correlation_id == "corr-m04-adapter-1"
    assert result.transaction_id == "tx-m04-adapter-1"
    assert result.evidence_ids == evidence_ids(anchors)
    assert result.source_snapshot_digest == DIGEST
    assert result.partition_canonical_digest == "sha256:" + "b" * 64
    assert result.checkpoint_id == "checkpoint-1"
    assert result.source_snapshot_id == "snapshot-1"
    assert result.source_schema_version == "scope-source-v1"
    assert result.canonical_schema_version == "scope-registry.v1"
    assert result.source_payload_digest == DIGEST
    assert result.resolved_scope_digest == registry_read(anchors).mapping_canonical_digest
    assert {name for name, _ in result.gate_evidence_ids} == {
        "MIG-M04-01", "MIG-M04-02", "checkpoint", "rls_review", "audit_review", "decision_record"
    }
    assert manager.expected_transaction_ids == ["tx-m04-adapter-1"]
    assert transaction.rls_contexts == [trusted_context(anchors).rls_context]
    assert transaction.clear_count == 1
    assert transaction.owner_registry.calls == 1
    assert transaction.migration_gates.calls == 1
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_missing_transaction_port_is_an_explicit_block():
    anchors = cabinet_anchors()
    result = await ScopeRegistryApplicationAdapter(None).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )
    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "scope_registry_transaction_port_missing"
    assert result.entry is None
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "reason"),
    [
        (None, "m04_scope_gate_evidence_missing"),
        (accepted_gate(m04_01_status="blocked"), "m04_01_or_m04_02_gate_not_accepted"),
        (accepted_gate(m04_02_evidence_id=None), "m04_scope_gate_evidence_ids_incomplete"),
        (accepted_gate(source_snapshot_digest="not-a-digest"), "m04_scope_gate_digest_invalid"),
        (accepted_gate(source_snapshot_id=None), "m04_scope_gate_evidence_ids_incomplete"),
        (accepted_gate(canonical_positive_writes=True), "upstream_canonical_positive_writer_not_disabled"),
    ],
)
async def test_missing_or_unaccepted_upstream_gates_block_before_owner_read(gate, reason):
    anchors = cabinet_anchors()
    transaction = FakeTransaction(registry_read(anchors), gate)

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == reason
    assert transaction.owner_registry.calls == 0
    assert transaction.clear_count == 1
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_missing_trusted_context_blocks_without_opening_transaction():
    anchors = cabinet_anchors()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    context = replace(trusted_context(anchors), rls_context=None)
    manager = FakeTransactionManager(transaction)

    result = await ScopeRegistryApplicationAdapter(manager).resolve_active(
        context=context, anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "trusted_transaction_context_missing"
    assert manager.expected_transaction_ids == []
    assert transaction.rls_contexts == []


@pytest.mark.asyncio
async def test_cross_tenant_or_narrower_context_assertions_block():
    anchors = cabinet_anchors()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    context = trusted_context(anchors)
    # RlsContext is a frozen pydantic model, not a dataclass: `dataclasses.replace`
    # raises TypeError and never produced the cross-tenant context this case needs.
    context = replace(
        context,
        tenant_id="tenant-other",
        rls_context=context.rls_context.model_copy(update={"tenant_id": "tenant-other"}),
    )
    manager = FakeTransactionManager(transaction)

    result = await ScopeRegistryApplicationAdapter(manager).resolve_active(
        context=context, anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "tenant_context_mismatch"
    assert manager.expected_transaction_ids == []


@pytest.mark.asyncio
async def test_plugin_scope_never_opens_transaction_or_calls_owner_repository():
    anchors = plugin_anchors()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    manager = FakeTransactionManager(transaction)

    result = await ScopeRegistryApplicationAdapter(manager).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.DEFERRED
    assert result.reason_code == "plugin_instance_deferred_m05"
    assert result.entry is None
    assert manager.expected_transaction_ids == []
    assert transaction.owner_registry.calls == 0
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_ambiguous_scope_rows_block_without_selecting_first_match():
    anchors = cabinet_anchors()
    transaction = FakeTransaction(registry_read(anchors, match_count=2), accepted_gate())

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "duplicate_or_ambiguous_scope_registry_rows"
    assert result.entry is None
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_project_tenant_cabinet_mismatch_is_quarantined_not_resolved():
    anchors = project_anchors()
    invalid_proof = parent_proof(anchors).model_copy(
        update={"project_cabinet_compatible": False}
    )
    transaction = FakeTransaction(
        registry_read(anchors, proof=invalid_proof), accepted_gate()
    )

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.QUARANTINED
    assert result.reason_code == "parent_compatibility_unproven"
    assert result.entry is None


@pytest.mark.asyncio
async def test_multiple_active_cabinet_memberships_do_not_resolve_to_one():
    anchors = cabinet_anchors()
    invalid_parents = replace(parent_evidence(anchors), active_cabinet_memberships=2)
    transaction = FakeTransaction(
        registry_read(anchors, parents=invalid_parents), accepted_gate()
    )

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.QUARANTINED
    # M04-02 reports the exact incompatibility it proved; a generic
    # `parent_compatibility_unproven` is reserved for a bundle whose specific parent
    # cardinalities are all sound but whose proof does not cover the scope.
    assert result.reason_code == "cabinet_membership_ambiguous"
    assert result.entry is None


@pytest.mark.asyncio
async def test_archived_scope_is_denied_and_never_returned_as_active():
    anchors = tenant_anchors()
    transaction = FakeTransaction(
        registry_read(anchors, entry=accepted_entry(anchors, state=OwnerState.ARCHIVED)),
        accepted_gate(),
    )

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.DENY
    assert result.entry is None


@pytest.mark.asyncio
async def test_changed_mapping_digest_is_blocked_without_returning_scope():
    anchors = cabinet_anchors()
    read = replace(registry_read(anchors), mapping_source_digest="sha256:" + "c" * 64)
    transaction = FakeTransaction(read, accepted_gate())

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "scope_mapping_source_identity_mismatch"
    assert result.entry is None


@pytest.mark.asyncio
async def test_mapping_from_another_snapshot_or_checkpoint_is_blocked():
    anchors = cabinet_anchors()
    read = replace(registry_read(anchors), mapping_checkpoint_id="checkpoint-old")
    transaction = FakeTransaction(read, accepted_gate())

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "scope_mapping_checkpoint_mismatch"
    assert result.entry is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_changes", "reason"),
    [
        ({"source_snapshot_digest": "sha256:" + "c" * 64}, "scope_snapshot_digest_mismatch"),
        ({"partition_canonical_digest": "sha256:" + "d" * 64}, "scope_partition_canonical_digest_mismatch"),
    ],
)
async def test_snapshot_or_partition_digest_mismatch_is_blocked(read_changes, reason):
    anchors = cabinet_anchors()
    read = replace(registry_read(anchors), **read_changes)
    transaction = FakeTransaction(read, accepted_gate())

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == reason
    assert result.entry is None


@pytest.mark.asyncio
async def test_missing_evidence_ids_block_active_result():
    anchors = cabinet_anchors()
    ids = replace(evidence_ids(anchors), mapping_id=None)
    transaction = FakeTransaction(registry_read(anchors, ids=ids), accepted_gate())

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "scope_evidence_ids_incomplete"
    assert result.entry is None



@pytest.mark.asyncio
@pytest.mark.parametrize("bad_scope", ["session", "connection", "" ])
async def test_rls_receipt_must_prove_transaction_local_scope(bad_scope):
    anchors = tenant_anchors()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    transaction.receipt_scope = bad_scope

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.BLOCKED
    assert result.reason_code == "transaction_local_rls_context_unproven"
    assert transaction.clear_count == 1
    assert transaction.owner_registry.calls == 0


@pytest.mark.asyncio
async def test_failure_to_clear_transaction_local_context_is_system_failure():
    anchors = tenant_anchors()
    transaction = FakeTransaction(registry_read(anchors), accepted_gate())
    transaction.clear_succeeds = False

    result = await ScopeRegistryApplicationAdapter(FakeTransactionManager(transaction)).resolve_active(
        context=trusted_context(anchors), anchors=anchors
    )

    assert result.outcome is ScopeAdapterOutcome.SYSTEM_FAILURE
    assert result.canonical_positive_writes is False
    assert transaction.clear_count == 1


def test_evidence_dto_has_no_raw_payload_or_secret_fields():
    field_names = set(ScopeRegistryEvidenceIds.__dataclass_fields__)
    assert not {"payload", "raw_payload", "secret", "token", "credential"}.intersection(field_names)
    assert ScopeKind.PLUGIN_INSTANCE.value == "plugin_instance"
