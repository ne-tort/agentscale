"""Static transaction/repository fixtures for MIG-M04-ADAPTERS-01."""

from contextlib import asynccontextmanager
from dataclasses import replace

from prodavan.application.migration.m04_scope.scope_migration import (
    MigrationEvidenceContext,
    ScopeActorMembershipEvidence,
    ScopeMigrationCoordinator,
    ScopeParentEvidence,
    ScopeRlsEvidence,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    RlsContext,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from prodavan.application.migration.scope_registry_adapter.ports import (
    ScopeRegistryEvidenceIds,
    ScopeRegistryGateEvidence,
    ScopeRegistryRead,
    TransactionLocalRlsClearReceipt,
    TransactionLocalRlsReceipt,
)

TENANT = "tenant-1"
CABINET = "cabinet-1"
PROJECT = "project-1"
TX_ID = "tx-m04-adapter-1"
CORRELATION = "corr-m04-adapter-1"
DIGEST = "sha256:" + "a" * 64


def tenant_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.TENANT, scope_id=TENANT, tenant_id=TENANT)


def cabinet_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.CABINET,
        scope_id=CABINET,
        tenant_id=TENANT,
        cabinet_id=CABINET,
    )


def project_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PROJECT,
        scope_id=PROJECT,
        tenant_id=TENANT,
        cabinet_id=CABINET,
        project_id=PROJECT,
    )


def plugin_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE,
        scope_id="plugin-1",
        tenant_id=TENANT,
        cabinet_id=CABINET,
        # ScopeAnchors requires a plugin scope to name its installation anchor:
        # scope_id must equal plugin_instance_id or the typed identity is invalid.
        plugin_instance_id="plugin-1",
    )


def trusted_context(anchors: ScopeAnchors | None = None) -> MigrationEvidenceContext:
    anchors = anchors or cabinet_anchors()
    rls = RlsContext(
        tenant_id=anchors.tenant_id,
        cabinet_id=anchors.cabinet_id,
        project_id=anchors.project_id,
        user_id="user-1",
        role="migration",
    )
    return MigrationEvidenceContext(
        tenant_id=anchors.tenant_id,
        authenticated=True,
        transaction_id=TX_ID,
        correlation_id=CORRELATION,
        user_id=rls.user_id,
        role=rls.role,
        rls_context=rls,
        # MigrationEvidenceContext.usable treats a server-derived context without
        # a positive writer generation as unusable: a writer that cannot name its
        # fencing generation cannot prove it is the current owner. Omitting it made
        # every fixture-backed acceptance case block with
        # trusted_transaction_context_missing before any adapter logic ran.
        writer_generation=1,
    )


def accepted_gate(**changes) -> ScopeRegistryGateEvidence:
    values = {
        "tenant_id": TENANT,
        "m04_01_status": "accepted",
        "m04_01_evidence_id": "m04-01-evidence-1",
        "m04_02_status": "accepted",
        "m04_02_evidence_id": "m04-02-evidence-1",
        "source_snapshot_digest": DIGEST,
        "canonical_scope_digest": "sha256:" + "b" * 64,
        "checkpoint_id": "checkpoint-1",
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "scope-source-v1",
        "canonical_schema_version": "scope-registry.v1",
        "rls_review_evidence_id": "rls-review-1",
        "audit_review_evidence_id": "audit-review-1",
        "decision_record_id": "decision-1",
        "canonical_positive_writes": False,
    }
    values.update(changes)
    return ScopeRegistryGateEvidence(**values)



def accepted_entry(anchors: ScopeAnchors, *, state: OwnerState = OwnerState.ACTIVE):
    return ScopeRegistryEntry(
        anchors=anchors,
        owner_state=state,
        provenance=ScopeProvenance(
            source_kind="legacy_scope",
            source_id="source-1",
            schema_version="legacy.v1",
            payload_digest=DIGEST,
        ),
    )


def evidence_ids(anchors: ScopeAnchors) -> ScopeRegistryEvidenceIds:
    return ScopeRegistryEvidenceIds(
        registry_id="scope-registry-1",
        mapping_id="mapping-1",
        audit_id="scope-audit-1",
        tenant_anchor_id="tenant-anchor-1",
        cabinet_anchor_id="cabinet-anchor-1" if anchors.cabinet_id else None,
        project_anchor_id="project-anchor-1" if anchors.project_id else None,
        cabinet_membership_ids=("membership-1",) if anchors.cabinet_id else (),
    )


def parent_proof(anchors: ScopeAnchors) -> ParentCompatibilityProof:
    return ParentCompatibilityProof(
        tenant_exists=True,
        tenant_retained=True,
        cabinet_exists=bool(anchors.cabinet_id),
        cabinet_tenant_compatible=bool(anchors.cabinet_id),
        cabinet_membership_active=bool(anchors.cabinet_id),
        project_exists=bool(anchors.project_id),
        project_tenant_compatible=bool(anchors.project_id),
        project_cabinet_compatible=bool(anchors.project_id),
        project_policy_eligible=bool(anchors.project_id),
    )


def parent_evidence(anchors: ScopeAnchors) -> ScopeParentEvidence:
    return ScopeParentEvidence(
        tenant_matches=1,
        cabinet_matches=1 if anchors.cabinet_id else 0,
        active_cabinet_memberships=1 if anchors.cabinet_id else 0,
        project_matches=1 if anchors.project_id else 0,
        active_registry_matches=1,
        # Parent evidence is transaction-bound evidence: M04-02 quarantines a bundle
        # whose cardinality proof does not name the transaction it was read in.
        transaction_id=TX_ID,
        tenant_id=TENANT,
        cabinet_id=CABINET if anchors.cabinet_id else None,
        # Only a company grant proves cabined membership; a bare membership row is
        # not compatible evidence for a cabinet/project scope.
        cabinet_membership_kind="cabinet_company_grant" if anchors.cabinet_id else None,
        membership_tenant_id=TENANT if anchors.cabinet_id else None,
        membership_cabinet_id=CABINET if anchors.cabinet_id else None,
        project_id=PROJECT if anchors.project_id else None,
        project_tenant_id=TENANT if anchors.project_id else None,
        project_cabinet_id=CABINET if anchors.project_id else None,
        active_registry_anchors=anchors,
    )


def rls_evidence(anchors: ScopeAnchors) -> ScopeRlsEvidence:
    return ScopeRlsEvidence(
        transaction_id=TX_ID,
        tenant_id=anchors.tenant_id,
        cabinet_id=anchors.cabinet_id,
        project_id=anchors.project_id,
        row_security_enabled=True,
        row_security_forced=True,
        database_role="prodavan_app",
        security_review_id="rls-review-1",
        policy_names=("tenant_scope_policy",),
        tenant_policy_enforced=True,
        using_policy_enforced=True,
        with_check_policy_enforced=True,
        missing_context_denies=True,
        role_not_bypass_rls=True,
        typed_parent_policy_enforced=True,
    )


def actor_membership_evidence(anchors: ScopeAnchors) -> ScopeActorMembershipEvidence:
    return ScopeActorMembershipEvidence(
        transaction_id=TX_ID,
        actor_id="user-1",
        tenant_id=anchors.tenant_id,
        relationship_kind="employee_membership",
        relationship_matches=1,
        active=True,
    )


def registry_read(
    anchors: ScopeAnchors,
    *,
    match_count: int = 1,
    entry: ScopeRegistryEntry | None = None,
    proof: ParentCompatibilityProof | None = None,
    parents: ScopeParentEvidence | None = None,
    ids: ScopeRegistryEvidenceIds | None = None,
    rls: ScopeRlsEvidence | None = None,
    actor: ScopeActorMembershipEvidence | None = None,
) -> ScopeRegistryRead:
    return ScopeRegistryRead(
        transaction_id=TX_ID,
        match_count=match_count,
        entry=entry if entry is not None else accepted_entry(anchors),
        parent_proof=proof if proof is not None else parent_proof(anchors),
        parent_evidence=parents if parents is not None else parent_evidence(anchors),
        evidence_ids=ids if ids is not None else evidence_ids(anchors),
        mapping_source_kind="legacy_scope",
        mapping_source_id="source-1",
        mapping_schema_version="legacy.v1",
        mapping_source_digest=DIGEST,
        mapping_canonical_digest=ScopeMigrationCoordinator._canonical_digest(
            entry if entry is not None else accepted_entry(anchors), "scope-registry.v1"
        ),
        mapping_checkpoint_id="checkpoint-1",
        mapping_canonical_schema_version="scope-registry.v1",
        source_snapshot_id="snapshot-1",
        source_snapshot_schema_version="scope-source-v1",
        source_snapshot_digest=DIGEST,
        partition_canonical_digest="sha256:" + "b" * 64,
        rls_evidence=rls if rls is not None else rls_evidence(anchors),
        actor_membership_evidence=(
            actor if actor is not None else actor_membership_evidence(anchors)
        ),
    )


class FakeOwnerRegistryRepository:
    def __init__(self, read: ScopeRegistryRead) -> None:
        self.read = read
        self.calls = 0

    async def read_scope(self, *, anchors, transaction_id):
        self.calls += 1
        assert transaction_id == TX_ID
        assert anchors.tenant_id == TENANT
        return self.read


class FakeGateRepository:
    def __init__(self, gate: ScopeRegistryGateEvidence | None) -> None:
        self.gate = gate
        self.calls = 0

    async def read_gate(self, *, tenant_id):
        self.calls += 1
        assert tenant_id == TENANT
        return self.gate


class FakeTransaction:
    def __init__(self, read: ScopeRegistryRead, gate: ScopeRegistryGateEvidence | None) -> None:
        self.transaction_id = TX_ID
        self.owner_registry = FakeOwnerRegistryRepository(read)
        self.migration_gates = FakeGateRepository(gate)
        self.rls_contexts: list[RlsContext] = []
        self.clear_count = 0
        self.receipt_transaction_id = TX_ID
        self.receipt_scope = "transaction_local"
        self.clear_succeeds = True

    async def set_transaction_local_rls_context(self, context: RlsContext):
        self.rls_contexts.append(context)
        return TransactionLocalRlsReceipt(
            transaction_id=self.receipt_transaction_id,
            setting_scope=self.receipt_scope,
            tenant_id=context.tenant_id,
            cabinet_id=context.cabinet_id,
            project_id=context.project_id,
            user_id=context.user_id,
            role=context.role,
        )

    async def clear_transaction_local_rls_context(self):
        self.clear_count += 1
        return TransactionLocalRlsClearReceipt(
            transaction_id=TX_ID,
            setting_scope="transaction_local",
            cleared=self.clear_succeeds,
        )


class FakeTransactionManager:
    def __init__(self, transaction: FakeTransaction) -> None:
        self._transaction = transaction
        self.expected_transaction_ids: list[str] = []

    @asynccontextmanager
    async def transaction(self, *, expected_transaction_id: str):
        self.expected_transaction_ids.append(expected_transaction_id)
        yield self._transaction
