"""MIG-M04-02 acceptance scenarios; intentionally not executed."""

from dataclasses import replace

import pytest

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillOutcome,
    ExistingScopeMapping,
    MappingReplayDecision,
    ScopeMigrationCoordinator,
    ScopeCandidate,
    ScopeMappingInput,
    ScopeParentEvidence,
    ScopeResolution,
    ScopeResolutionEvidence,
    ScopeRlsEvidence,
    ScopeResolver,
    ScopeSourceQuarantineEvidence,
    ScopeStreamIdentity,
    SourceRowState,
    assess_mapping_replay,
    evaluate_active_scope_evidence,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    QuarantineRecord,
    ScopeAnchors,
    ScopeKind,
)
from .acceptance_fixtures import (
    CABINET,
    CORRELATION,
    TENANT,
    actor_membership_evidence,
    candidate,
    cabinet_anchors,
    checkpoint,
    mapping_input,
    parent_evidence,
    plugin_anchors,
    project_anchors,
    rls_evidence,
    scope_stream,
    tenant_anchors,
    trusted_context,
)


@pytest.mark.asyncio
async def test_missing_persistence_port_blocks_and_has_no_positive_digest() -> None:
    item = candidate()
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=item, checkpoint=checkpoint(), context=trusted_context()
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"
    assert result.source_digest == item.source_digest
    assert result.canonical_digest is None
    assert result.canonical_positive_writes is False
    assert result.correlation_id == CORRELATION


@pytest.mark.asyncio
async def test_missing_trusted_transaction_context_blocks() -> None:
    context = replace(trusted_context(), rls_context=None)
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=candidate(), checkpoint=checkpoint(), context=context
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_missing_scope_mapping_actor_context_blocks_before_persistence() -> None:
    context = trusted_context()
    assert context.rls_context is not None
    # ``RlsContext`` is a frozen pydantic contract, so the altered context must be
    # derived with ``model_copy``; ``dataclasses.replace`` cannot copy it at all.
    mismatched = replace(
        context,
        rls_context=context.rls_context.model_copy(update={"tenant_id": "tenant-other"}),
    )
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=candidate(), checkpoint=checkpoint(), context=mismatched
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"


@pytest.mark.asyncio
async def test_resume_checkpoint_denies_without_persistence_or_wrong_tenant() -> None:
    coordinator = ScopeMigrationCoordinator(None)
    no_port = await coordinator.resume_checkpoint(stream=scope_stream(), context=trusted_context())
    assert no_port.checkpoint is None
    assert no_port.reason_code == "missing_trusted_transaction_context_or_port"
    wrong_tenant = await coordinator.resume_checkpoint(
        stream=scope_stream(tenant_id="tenant-other"), context=trusted_context()
    )
    assert wrong_tenant.checkpoint is None
    assert wrong_tenant.reason_code == "checkpoint_tenant_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor_change", ["tenant", "cabinet", "project"])
async def test_mapping_blocks_when_server_context_does_not_match_anchors(anchor_change) -> None:
    item = candidate(anchors=project_anchors())
    context = trusted_context(anchors=project_anchors())
    assert context.rls_context is not None
    # Frozen pydantic RLS context: derive the altered copy with ``model_copy``.
    if anchor_change == "tenant":
        altered_rls = context.rls_context.model_copy(update={"tenant_id": "tenant-other"})
    elif anchor_change == "cabinet":
        altered_rls = context.rls_context.model_copy(update={"cabinet_id": "cabinet-other"})
    else:
        altered_rls = context.rls_context.model_copy(update={"project_id": "project-other"})
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=item,
        checkpoint=checkpoint(),
        context=replace(context, rls_context=altered_rls),
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"
    assert result.checkpoint == checkpoint()


@pytest.mark.asyncio
async def test_mapping_does_not_treat_source_tenant_as_trusted_actor_context() -> None:
    result = await ScopeMigrationCoordinator(None).process_row(
        candidate=candidate(anchors=project_anchors()),
        checkpoint=checkpoint(),
        context=trusted_context(),
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"


def test_source_id_too_wide_is_rejected_before_quarantine_dto_construction() -> None:
    with pytest.raises(ValueError, match="exceeds owner mapping evidence width"):
        ScopeSourceQuarantineEvidence(
            source_kind="legacy_scope",
            source_id="x" * 129,
            schema_version="schema-1",
            canonical_schema_version="scope-registry.v1",
            source_digest="sha256:" + "e" * 64,
        )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (SourceRowState.DELETED, "source_row_deleted"),
        (SourceRowState.ARCHIVED, "source_row_archived"),
        (SourceRowState.QUARANTINED, "source_row_quarantined"),
        (SourceRowState.UNKNOWN, "source_row_lifecycle_unknown"),
    ],
)
def test_deleted_archived_and_unknown_source_rows_are_quarantined(state, reason) -> None:
    item = candidate(source_state=state)
    actual_reason, entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert actual_reason == reason
    assert entry is None
    assert quarantine is not None and quarantine.reason_code == reason
    if isinstance(quarantine, QuarantineRecord):
        assert quarantine.payload_digest == item.source_digest
        assert quarantine.details == {}
    else:
        assert isinstance(quarantine, ScopeSourceQuarantineEvidence)
        assert quarantine.source_id == item.source_id
        assert quarantine.source_digest == item.source_digest
        assert not hasattr(quarantine, "payload")


@pytest.mark.parametrize(
    ("assertions", "reason"),
    [
        ({"asserted_tenant_id": "tenant-other"}, "cross_tenant_source_assertion"),
        ({"asserted_cabinet_id": "cabinet-other"}, "cabinet_source_assertion_mismatch"),
        ({"asserted_project_id": "project-other"}, "project_source_assertion_mismatch"),
        ({"source_id": "x" * 41}, "source_id_exceeds_typed_provenance"),
    ],
)
def test_cross_scope_assertions_are_quarantined(assertions, reason) -> None:
    item = candidate(**assertions)
    actual_reason, entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert actual_reason == reason
    assert entry is None
    assert quarantine is not None and quarantine.reason_code == reason


def test_plugin_scope_is_quarantined_for_later_m05_without_positive_entry() -> None:
    item = candidate(anchors=plugin_anchors())
    reason, entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert reason == "plugin_instance_deferred_m05"
    assert entry is None and quarantine is not None


@pytest.mark.parametrize(
    ("kind", "evidence", "anchors", "expected"),
    [
        (ScopeKind.TENANT, ScopeParentEvidence(1, 0, 0, 0, 1, tenant_id="tenant-1", active_registry_anchors=tenant_anchors()), tenant_anchors(), True),
        (ScopeKind.TENANT, ScopeParentEvidence(2, 0, 0, 0, 1, tenant_id="tenant-1", active_registry_anchors=tenant_anchors()), tenant_anchors(), False),
        (ScopeKind.TENANT, ScopeParentEvidence(1, 0, 0, 0, 0, tenant_id="tenant-1", active_registry_anchors=tenant_anchors()), tenant_anchors(), False),
        (ScopeKind.TENANT, ScopeParentEvidence(1, 0, 0, 0, 2, tenant_id="tenant-1", active_registry_anchors=tenant_anchors()), tenant_anchors(), False),
        (ScopeKind.CABINET, ScopeParentEvidence(1, 1, 1, 0, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", active_registry_anchors=cabinet_anchors()), cabinet_anchors(), True),
        # Distinct invalid case: the single active membership must belong to the
        # resolved cabinet, not merely to the same tenant.
        (ScopeKind.CABINET, ScopeParentEvidence(1, 1, 1, 0, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-other", active_registry_anchors=cabinet_anchors()), cabinet_anchors(), False),
        (ScopeKind.CABINET, ScopeParentEvidence(1, 1, 2, 0, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", active_registry_anchors=cabinet_anchors()), cabinet_anchors(), False),
        (ScopeKind.PROJECT, ScopeParentEvidence(1, 1, 1, 1, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", project_id="project-1", project_tenant_id="tenant-1", project_cabinet_id="cabinet-1", active_registry_anchors=project_anchors()), project_anchors(), True),
        # Distinct invalid case: exactly one project row must resolve to the
        # requested project identity, not merely to the same tenant/cabinet.
        (ScopeKind.PROJECT, ScopeParentEvidence(1, 1, 1, 1, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", project_id="project-other", project_tenant_id="tenant-1", project_cabinet_id="cabinet-1", active_registry_anchors=project_anchors()), project_anchors(), False),
        (ScopeKind.PROJECT, ScopeParentEvidence(1, 1, 1, 1, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", project_id="project-1", project_tenant_id="tenant-other", project_cabinet_id="cabinet-1", active_registry_anchors=project_anchors()), project_anchors(), False),
        (ScopeKind.PROJECT, ScopeParentEvidence(1, 1, 1, 1, 1, tenant_id="tenant-1", cabinet_id="cabinet-1", cabinet_membership_kind="cabinet_company_grant", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", project_id="project-1", project_tenant_id="tenant-1", project_cabinet_id="cabinet-other", active_registry_anchors=project_anchors()), project_anchors(), False),
        (ScopeKind.CABINET, ScopeParentEvidence(1, 1, 1, 0, 1, cabinet_deleted=True, tenant_id="tenant-1", cabinet_id="cabinet-1", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", active_registry_anchors=cabinet_anchors()), cabinet_anchors(), False),
        (ScopeKind.CABINET, ScopeParentEvidence(1, 1, 1, 0, 2, tenant_id="tenant-1", cabinet_id="cabinet-1", membership_tenant_id="tenant-1", membership_cabinet_id="cabinet-1", active_registry_anchors=cabinet_anchors()), cabinet_anchors(), False),
    ],
)
def test_parent_proof_requires_exact_cardinality_and_active_parents(kind, evidence, anchors, expected) -> None:
    assert evidence.valid_parent_for(anchors) is expected


def test_parent_evidence_rejects_negative_cardinality() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        ScopeParentEvidence(-1, 0, 0, 0, 0)


def test_checkpoint_binds_tenant_snapshot_and_canonical_schema() -> None:
    original = checkpoint()
    changed_snapshot = checkpoint(snapshot_digest="sha256:" + "b" * 64)
    changed_tenant = checkpoint(tenant_id="tenant-other")
    changed_schema = replace(original, canonical_schema_version="scope-registry.v2")
    assert original.stream_key != changed_tenant.stream_key
    assert original.stream_key != changed_schema.stream_key
    assert original.lineage_key != changed_snapshot.lineage_key


def test_checkpoint_rejects_malformed_snapshot_digest() -> None:
    with pytest.raises(ValueError, match="source snapshot digest"):
        checkpoint(snapshot_digest="sha256:not-a-full-digest")


def test_candidate_canonical_schema_must_match_checkpoint_schema() -> None:
    candidate_v2 = candidate(canonical_schema_version="scope-registry.v2")
    assert candidate_v2.canonical_schema_version != checkpoint().canonical_schema_version


def test_source_id_over_typed_provenance_is_quarantined_without_truncation() -> None:
    item = candidate(source_id="x" * 41)
    actual_reason, entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert actual_reason == "source_id_exceeds_typed_provenance"
    assert entry is None
    assert isinstance(quarantine, ScopeSourceQuarantineEvidence)
    assert quarantine.source_id == "x" * 41
    assert quarantine.source_digest == item.source_digest
    archived = candidate(source_id="y" * 41, source_state=SourceRowState.ARCHIVED)
    archived_reason, _, archived_quarantine = ScopeMigrationCoordinator._proposed_outcome(
        archived, trusted_context(), archived.source_digest
    )
    assert archived_reason == "source_row_archived"
    assert isinstance(archived_quarantine, ScopeSourceQuarantineEvidence)
    assert archived_quarantine.reason_code == archived_reason


def test_source_digest_is_deterministic_and_includes_identity_lifecycle_and_assertions() -> None:
    first = candidate(payload={"scope": "same", "status": "active"})
    replay = candidate(payload={"status": "active", "scope": "same"})
    deleted = candidate(payload={"scope": "same", "status": "active"}, source_state=SourceRowState.DELETED)
    different_source = candidate(source_id="src-2", payload={"scope": "same", "status": "active"})
    changed_anchor = candidate(anchors=project_anchors(), payload={"scope": "same", "status": "active"})
    assert first.source_digest == replay.source_digest
    assert first.mapping_identity == replay.mapping_identity
    assert first.source_digest != deleted.source_digest
    assert first.source_digest != different_source.source_digest
    assert first.mapping_identity != different_source.mapping_identity
    assert first.source_digest != changed_anchor.source_digest
    assert first.mapping_identity == changed_anchor.mapping_identity


def test_source_digest_is_snapshotted_and_rejects_nonfinite_or_nonjson_payload() -> None:
    payload: dict[str, object] = {"state": "active"}
    item = candidate(payload=payload)
    before = item.source_digest
    payload["state"] = "mutated-after-construction"
    assert item.source_digest == before
    with pytest.raises(ValueError, match="deterministic finite JSON"):
        candidate(payload={"not_json": object()})
    with pytest.raises(ValueError, match="deterministic finite JSON"):
        candidate(payload={"not_finite": float("nan")})


@pytest.mark.asyncio
async def test_active_resolver_fails_closed_without_port_and_defers_plugin() -> None:
    resolver = ScopeResolver(None)
    tenant = await resolver.resolve(
        context=trusted_context(anchors=tenant_anchors()), anchors=tenant_anchors()
    )
    wrong_context = await resolver.resolve(context=trusted_context(), anchors=project_anchors())
    plugin = await resolver.resolve(
        context=trusted_context(anchors=plugin_anchors()), anchors=plugin_anchors()
    )
    assert tenant.outcome is ScopeResolution.DENY and tenant.entry is None
    assert wrong_context.outcome is ScopeResolution.DENY and wrong_context.entry is None
    assert plugin.outcome is ScopeResolution.DENY and plugin.entry is None


def test_source_id_at_owner_mapping_width_is_retained_for_quarantine() -> None:
    item = candidate(source_id="x" * 128)
    reason, entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert reason == "source_id_exceeds_typed_provenance"
    assert entry is None
    assert isinstance(quarantine, ScopeSourceQuarantineEvidence)
    assert quarantine.source_id == item.source_id
    assert len(quarantine.source_id) == 128


def test_source_id_over_128_is_rejected_without_truncation() -> None:
    with pytest.raises(ValueError, match="exceeds owner mapping evidence width"):
        ScopeCandidate(
            source_kind="legacy_scope",
            source_id="x" * 129,
            schema_version="schema-1",
            payload={},
            anchors=cabinet_anchors(),
        )


def test_typed_scope_fixtures_use_exact_company_cabinet_project_anchors() -> None:
    tenant = tenant_anchors()
    cabinet = cabinet_anchors()
    project = project_anchors()
    assert tenant.scope_id == TENANT == tenant.tenant_id
    assert cabinet.tenant_id == TENANT and cabinet.scope_id == cabinet.cabinet_id
    assert project.tenant_id == TENANT and project.cabinet_id == cabinet.cabinet_id
    assert project.scope_id == project.project_id


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ScopeKind.TENANT, tenant_anchors()),
        (ScopeKind.CABINET, cabinet_anchors()),
        (ScopeKind.PROJECT, project_anchors()),
        (ScopeKind.PLUGIN_INSTANCE, plugin_anchors()),
    ],
)
def test_typed_scope_mapping_is_deterministic(kind, expected) -> None:
    assert mapping_input(kind).to_anchors() == expected


def test_typed_scope_mapping_rejects_missing_hierarchy_parent() -> None:
    with pytest.raises(ValueError, match="project scope ID is required"):
        ScopeMappingInput(ScopeKind.PROJECT, TENANT, cabinet_id=CABINET).to_anchors()
    with pytest.raises(ValueError, match="malformed for the owner registry"):
        candidate(anchors=ScopeAnchors(scope_kind=ScopeKind.TENANT, scope_id="../co", tenant_id="../co"))


def test_source_mapping_key_matches_m04_01_unique_key_and_digest_conflicts() -> None:
    first = candidate(payload={"value": "before"})
    exact = candidate(payload={"value": "before"})
    changed_digest = candidate(payload={"value": "after"})
    changed_source_schema = candidate(source_schema_version="schema-2")
    changed_canonical_schema = candidate(canonical_schema_version="scope-registry.v2")
    accepted_identity = (
        ScopeKind.CABINET.value,
        CABINET,
        TENANT,
        CABINET,
        None,
        None,
    )
    stored = ExistingScopeMapping(
        mapping_identity=("legacy_scope", "src-1", "schema-1"),
        source_digest=first.source_digest,
        canonical_schema_version="scope-registry.v1",
        canonical_identity=accepted_identity,
        outcome=BackfillOutcome.ACCEPTED,
        reason_code=None,
    )
    assert first.mapping_identity == stored.mapping_identity
    assert assess_mapping_replay(exact, stored).decision is MappingReplayDecision.REPLAY
    assert assess_mapping_replay(changed_digest, stored).reason_code == "source_digest_changed"
    assert assess_mapping_replay(changed_source_schema, None).decision is MappingReplayDecision.NEW
    assert assess_mapping_replay(changed_source_schema, stored).reason_code == "source_identity_mismatch"
    canonical_version_conflict = assess_mapping_replay(changed_canonical_schema, stored)
    assert canonical_version_conflict.decision is MappingReplayDecision.CONFLICT
    assert canonical_version_conflict.reason_code == "canonical_schema_version_changed"
    changed_anchor = candidate(anchors=project_anchors(), payload={"value": "before"})
    assert assess_mapping_replay(changed_anchor, stored).reason_code == "source_digest_changed"


def test_quarantined_mapping_replay_keeps_null_canonical_identity() -> None:
    item = candidate(source_state=SourceRowState.ARCHIVED)
    stored = ExistingScopeMapping(
        mapping_identity=item.mapping_identity,
        source_digest=item.source_digest,
        canonical_schema_version=item.canonical_schema_version,
        canonical_identity=None,
        outcome=BackfillOutcome.QUARANTINED,
        reason_code="source_row_archived",
    )
    assert assess_mapping_replay(item, stored).decision is MappingReplayDecision.REPLAY


def test_active_mapping_result_contract_requires_reviewed_parent_rls_and_actor_proof() -> None:
    item = candidate()
    reason, quarantined_entry, quarantine = ScopeMigrationCoordinator._proposed_outcome(
        item, trusted_context(), item.source_digest
    )
    assert reason is None and quarantined_entry is not None and quarantine is None
    assert quarantined_entry.owner_state is OwnerState.QUARANTINED

    # Positive active mapping remains impossible in this zone without trusted parent,
    # RLS and actor-membership evidence supplied by a real transaction adapter.


def test_scope_stream_resumes_only_exact_snapshot_lineage() -> None:
    stream = scope_stream()
    initial = stream.initial_checkpoint()
    saved = replace(initial, after_source_id="src-17", sequence=17)
    assert saved.stream_key == stream.stream_key
    assert saved.source_snapshot_digest == stream.source_snapshot_digest
    assert scope_stream(snapshot_digest="sha256:" + "b" * 64).lineage_key != saved.lineage_key
    assert ScopeStreamIdentity(
        source_snapshot_id="snapshot-1",
        source_schema_version="schema-2",
        source_snapshot_digest=stream.source_snapshot_digest,
        tenant_id=TENANT,
        writer_generation=stream.writer_generation,
    ).stream_key != stream.stream_key


@pytest.mark.parametrize(
    ("evidence_overrides", "reason"),
    [
        ({"tenant_matches": 0}, "tenant_parent_missing"),
        ({"tenant_matches": 2}, "tenant_parent_ambiguous"),
        ({"tenant_deleted": True}, "tenant_parent_deleted"),
        ({"tenant_archived": True}, "tenant_parent_archived"),

    ],
)
def test_tenant_parent_evidence_rejects_missing_ambiguous_and_deleted(evidence_overrides, reason) -> None:
    evidence = parent_evidence(tenant_anchors(), **evidence_overrides)
    assert evidence.invalid_reason_for(tenant_anchors()) == reason


@pytest.mark.parametrize(
    ("evidence_overrides", "reason"),
    [
        ({"cabinet_matches": 0}, "cabinet_parent_missing"),
        ({"cabinet_matches": 2}, "cabinet_parent_ambiguous"),
        ({"cabinet_deleted": True}, "cabinet_parent_deleted"),
        ({"cabinet_archived": True}, "cabinet_parent_archived"),

        ({"active_cabinet_memberships": 0}, "cabinet_membership_missing"),
        ({"active_cabinet_memberships": 2}, "cabinet_membership_ambiguous"),
        ({"cabinet_membership_kind": "employee_assignment"}, "cabinet_membership_kind_unproven"),
        ({"cabinet_membership_kind": "all_grant_scope"}, "cabinet_membership_kind_unproven"),
        ({"membership_tenant_id": "tenant-other"}, "cross_tenant_cabinet_membership"),
    ],
)
def test_cabinet_parent_evidence_requires_one_live_same_tenant_membership(evidence_overrides, reason) -> None:
    evidence = parent_evidence(cabinet_anchors(), **evidence_overrides)
    assert evidence.invalid_reason_for(cabinet_anchors()) == reason


@pytest.mark.parametrize(
    ("evidence_overrides", "reason"),
    [
        ({"project_matches": 0}, "project_parent_missing"),
        ({"project_matches": 2}, "project_parent_ambiguous"),
        ({"project_deleted": True}, "project_parent_deleted"),
        ({"project_archived": True}, "project_parent_archived"),
        ({"project_tenant_id": "tenant-other"}, "cross_tenant_project_parent"),
        ({"project_cabinet_id": "cabinet-other"}, "project_cabinet_mismatch"),
    ],
)
def test_project_parent_evidence_requires_exact_company_cabinet_tuple(evidence_overrides, reason) -> None:
    evidence = parent_evidence(project_anchors(), **evidence_overrides)
    assert evidence.invalid_reason_for(project_anchors()) == reason


def test_active_scope_evaluator_requires_exact_parent_membership_and_rls_evidence() -> None:
    from prodavan.application.scope_registry.contracts import (
        OwnerState,
        ParentCompatibilityProof,
        ScopeProvenance,
        ScopeRegistryEntry,
    )

    anchors = project_anchors()
    context = trusted_context(anchors=anchors)
    proof = ParentCompatibilityProof(
        tenant_exists=True,
        tenant_retained=True,
        cabinet_exists=True,
        cabinet_tenant_compatible=True,
        cabinet_membership_active=True,
        project_exists=True,
        project_tenant_compatible=True,
        project_cabinet_compatible=True,
        project_policy_eligible=True,
    )
    entry = ScopeRegistryEntry(
        anchors=anchors,
        owner_state=OwnerState.ACTIVE,
        provenance=ScopeProvenance(
            source_kind="legacy_scope",
            source_id="src-project",
            schema_version="schema-1",
            payload_digest="sha256:" + "c" * 64,
        ),
    )

    def evidence(*, parent=None, rls=None, registry_entry=entry, proof_transaction_id="tx-1", actor=None):
        return ScopeResolutionEvidence(
            entry=registry_entry,
            parent_proof=proof,
            parent_compatibility_transaction_id=proof_transaction_id,
            parent_evidence=parent or parent_evidence(anchors),
            rls_evidence=rls or rls_evidence(anchors),
            actor_membership_evidence=actor or actor_membership_evidence(anchors),
        )

    accepted = evaluate_active_scope_evidence(
        context=context, anchors=anchors, evidence=evidence()
    )
    assert accepted.outcome is ScopeResolution.ACTIVE

    cases = [
        (parent_evidence(anchors, active_registry_matches=0), None, ScopeResolution.QUARANTINED, "active_scope_missing"),
        (parent_evidence(anchors, active_registry_matches=2), None, ScopeResolution.QUARANTINED, "active_scope_ambiguous"),
        (parent_evidence(anchors, project_deleted=True), None, ScopeResolution.QUARANTINED, "project_parent_deleted"),
        (parent_evidence(anchors, active_cabinet_memberships=2), None, ScopeResolution.QUARANTINED, "cabinet_membership_ambiguous"),
        (parent_evidence(anchors, project_tenant_id="tenant-other"), None, ScopeResolution.QUARANTINED, "cross_tenant_project_parent"),
        (None, rls_evidence(anchors, forced=False), ScopeResolution.DENY, "rls_evidence_missing_or_invalid"),
        (None, rls_evidence(anchors, review_id=None), ScopeResolution.DENY, "rls_evidence_missing_or_invalid"),
    ]
    for parent, rls, outcome, reason in cases:
        result = evaluate_active_scope_evidence(
            context=context, anchors=anchors, evidence=evidence(parent=parent, rls=rls)
        )
        assert result.outcome is outcome
        assert result.reason_code == reason
        assert result.entry is None

    unproven_actor = ScopeResolutionEvidence(
        entry=entry,
        parent_proof=proof,
        parent_compatibility_transaction_id="tx-1",
        parent_evidence=parent_evidence(anchors),
        rls_evidence=rls_evidence(anchors),
        actor_membership_evidence=actor_membership_evidence(anchors, relationship_matches=2),
    )
    actor_denied = evaluate_active_scope_evidence(
        context=context, anchors=anchors, evidence=unproven_actor
    )
    assert actor_denied.outcome is ScopeResolution.DENY
    assert actor_denied.reason_code == "active_tenant_membership_unproven"
    assert actor_denied.entry is None

    mismatched_parent = ScopeResolutionEvidence(
        entry=entry,
        parent_proof=proof,
        parent_compatibility_transaction_id="tx-1",
        parent_evidence=parent_evidence(anchors, transaction_id="other-tx"),
        rls_evidence=rls_evidence(anchors),
        actor_membership_evidence=actor_membership_evidence(anchors),
    )
    parent_denied = evaluate_active_scope_evidence(
        context=context, anchors=anchors, evidence=mismatched_parent
    )
    assert parent_denied.outcome is ScopeResolution.QUARANTINED
    assert parent_denied.reason_code == "parent_evidence_transaction_mismatch"
    assert parent_denied.entry is None
    mismatched_proof = evaluate_active_scope_evidence(
        context=context,
        anchors=anchors,
        evidence=evidence(proof_transaction_id="other-tx"),
    )
    assert mismatched_proof.outcome is ScopeResolution.QUARANTINED
    assert mismatched_proof.reason_code == "parent_compatibility_transaction_mismatch"
    assert mismatched_proof.entry is None

    assert context.rls_context is not None
    changed_context = replace(
        context,
        rls_context=context.rls_context.model_copy(update={"project_id": "project-other"}),
    )
    mismatch = evaluate_active_scope_evidence(
        context=changed_context, anchors=anchors, evidence=evidence()
    )
    assert mismatch.outcome is ScopeResolution.DENY
    assert mismatch.reason_code == "scope_context_mismatch"
    assert mismatch.entry is None


def test_active_scope_evaluator_denies_nonactive_and_plugin_rows() -> None:
    from prodavan.application.scope_registry.contracts import (
        OwnerState,
        ParentCompatibilityProof,
        ScopeProvenance,
        ScopeRegistryEntry,
    )

    anchors = cabinet_anchors()
    context = trusted_context(anchors=anchors)
    proof = ParentCompatibilityProof(
        tenant_exists=True,
        tenant_retained=True,
        cabinet_exists=True,
        cabinet_tenant_compatible=True,
        cabinet_membership_active=True,
    )
    for state in (OwnerState.ARCHIVED, OwnerState.QUARANTINED):
        entry = ScopeRegistryEntry(
            anchors=anchors,
            owner_state=state,
            provenance=ScopeProvenance(
                source_kind="legacy_scope",
                source_id="src-cabinet",
                schema_version="schema-1",
                payload_digest="sha256:" + "d" * 64,
            ),
        )
        result = evaluate_active_scope_evidence(
            context=context,
            anchors=anchors,
            evidence=ScopeResolutionEvidence(
                entry=entry,
                parent_proof=proof,
                parent_compatibility_transaction_id="tx-1",
                parent_evidence=parent_evidence(anchors),
                rls_evidence=rls_evidence(anchors),
                actor_membership_evidence=actor_membership_evidence(anchors),
            ),
        )
        assert result.outcome is ScopeResolution.DENY and result.entry is None

    plugin = plugin_anchors()
    denied = evaluate_active_scope_evidence(
        context=trusted_context(anchors=plugin), anchors=plugin, evidence=None
    )
    assert denied.outcome is ScopeResolution.DENY
    assert denied.reason_code == "plugin_instance_denied_m05"


def test_rls_evidence_is_bound_to_transaction_and_reviewed_policy_claims() -> None:
    anchors = project_anchors()
    context = trusted_context(anchors=anchors)
    assert rls_evidence(anchors).valid_for(context=context, anchors=anchors)
    assert not rls_evidence(anchors, transaction_id="other-tx").valid_for(context=context, anchors=anchors)
    assert not rls_evidence(anchors, enabled=False).valid_for(context=context, anchors=anchors)
    assert not rls_evidence(anchors, review_id=None).valid_for(context=context, anchors=anchors)
    assert not rls_evidence(anchors, policy_names=()).valid_for(context=context, anchors=anchors)
