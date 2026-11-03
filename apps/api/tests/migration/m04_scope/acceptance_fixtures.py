"""Static M04-02 acceptance inputs; no persistence adapter is supplied here."""

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    BackfillOutcome,
    ExistingScopeMapping,
    MigrationEvidenceContext,
    ScopeCandidate,
    ScopeActorMembershipEvidence,
    ScopeMappingInput,
    ScopeParentEvidence,
    ScopeRlsEvidence,
    ScopeStreamIdentity,
    SourceRowState,
)
from prodavan.application.scope_registry.contracts import RlsContext, ScopeAnchors, ScopeKind

TENANT = "tenant-1"
CABINET = "cabinet-1"
PROJECT = "project-1"
CORRELATION = "corr-m04-02-1"
SNAPSHOT_DIGEST = "sha256:" + "a" * 64


def tenant_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.TENANT, scope_id=TENANT, tenant_id=TENANT)


def cabinet_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.CABINET, scope_id=CABINET, tenant_id=TENANT, cabinet_id=CABINET)


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
    )


def trusted_context(*, anchors: ScopeAnchors | None = None) -> MigrationEvidenceContext:
    rls = RlsContext(
        tenant_id=anchors.tenant_id if anchors else TENANT,
        cabinet_id=anchors.cabinet_id if anchors else CABINET,
        project_id=anchors.project_id if anchors else None,
        user_id="user-1",
        role="migration",
    )
    return MigrationEvidenceContext(
        tenant_id=rls.tenant_id,
        authenticated=True,
        transaction_id="tx-1",
        correlation_id=CORRELATION,
        user_id="user-1",
        role="migration",
        rls_context=rls,
        writer_generation=1,
    )


def checkpoint(
    after_source_id: str | None = None,
    sequence: int = 0,
    *,
    tenant_id: str = TENANT,
    snapshot_digest: str = SNAPSHOT_DIGEST,
) -> BackfillCheckpoint:
    return BackfillCheckpoint(
        source_snapshot_id="snapshot-1",
        source_schema_version="schema-1",
        after_source_id=after_source_id,
        sequence=sequence,
        source_snapshot_digest=snapshot_digest,
        tenant_id=tenant_id,
        writer_generation=1,
    )


def scope_stream(*, tenant_id: str = TENANT, snapshot_digest: str = SNAPSHOT_DIGEST) -> ScopeStreamIdentity:
    return ScopeStreamIdentity(
        source_snapshot_id="snapshot-1",
        source_schema_version="schema-1",
        source_snapshot_digest=snapshot_digest,
        tenant_id=tenant_id,
        writer_generation=1,
    )


def parent_evidence(
    anchors: ScopeAnchors,
    *,
    transaction_id: str | None = "tx-1",
    tenant_matches: int = 1,
    cabinet_matches: int | None = None,
    active_cabinet_memberships: int | None = None,
    project_matches: int | None = None,
    active_registry_matches: int = 1,
    tenant_deleted: bool = False,
    cabinet_deleted: bool = False,
    project_deleted: bool = False,
    tenant_archived: bool = False,
    cabinet_archived: bool = False,
    project_archived: bool = False,
    tenant_id: str | None = None,
    cabinet_id: str | None = None,
    cabinet_membership_kind: str | None = "cabinet_company_grant",
    membership_tenant_id: str | None = None,
    membership_cabinet_id: str | None = None,
    project_id: str | None = None,
    project_tenant_id: str | None = None,
    project_cabinet_id: str | None = None,
    active_registry_anchors: ScopeAnchors | None = None,
) -> ScopeParentEvidence:
    has_cabinet = anchors.scope_kind in {ScopeKind.CABINET, ScopeKind.PROJECT}
    has_project = anchors.scope_kind is ScopeKind.PROJECT
    return ScopeParentEvidence(
        tenant_matches=tenant_matches,
        cabinet_matches=(int(has_cabinet) if cabinet_matches is None else cabinet_matches),
        active_cabinet_memberships=(
            int(has_cabinet) if active_cabinet_memberships is None else active_cabinet_memberships
        ),
        project_matches=(int(has_project) if project_matches is None else project_matches),
        active_registry_matches=active_registry_matches,
        transaction_id=transaction_id,
        tenant_deleted=tenant_deleted,
        cabinet_deleted=cabinet_deleted,
        project_deleted=project_deleted,
        tenant_archived=tenant_archived,
        cabinet_archived=cabinet_archived,
        project_archived=project_archived,
        tenant_id=anchors.tenant_id if tenant_id is None else tenant_id,
        cabinet_id=anchors.cabinet_id if cabinet_id is None else cabinet_id,
        cabinet_membership_kind=cabinet_membership_kind,
        membership_tenant_id=anchors.tenant_id if membership_tenant_id is None else membership_tenant_id,
        membership_cabinet_id=anchors.cabinet_id if membership_cabinet_id is None else membership_cabinet_id,
        project_id=anchors.project_id if project_id is None else project_id,
        project_tenant_id=anchors.tenant_id if project_tenant_id is None else project_tenant_id,
        project_cabinet_id=anchors.cabinet_id if project_cabinet_id is None else project_cabinet_id,
        active_registry_anchors=anchors if active_registry_anchors is None else active_registry_anchors,
    )


def rls_evidence(
    anchors: ScopeAnchors,
    *,
    transaction_id: str = "tx-1",
    enabled: bool = True,
    forced: bool = True,
    role: str | None = "scope_migration_reader",
    review_id: str | None = "review-m04-scope-1",
    policy_names: tuple[str, ...] = ("scope_registry_tenant_policy",),
    tenant_policy_enforced: bool = True,
    using_enforced: bool = True,
    with_check_enforced: bool = True,
    missing_context_denies: bool = True,
    role_not_bypass_rls: bool = True,
    typed_parent_policy_enforced: bool = True,
) -> ScopeRlsEvidence:
    return ScopeRlsEvidence(
        transaction_id=transaction_id,
        tenant_id=anchors.tenant_id,
        cabinet_id=anchors.cabinet_id,
        project_id=anchors.project_id,
        row_security_enabled=enabled,
        row_security_forced=forced,
        database_role=role,
        security_review_id=review_id,
        policy_names=policy_names,
        tenant_policy_enforced=tenant_policy_enforced,
        using_policy_enforced=using_enforced,
        with_check_policy_enforced=with_check_enforced,
        missing_context_denies=missing_context_denies,
        role_not_bypass_rls=role_not_bypass_rls,
        typed_parent_policy_enforced=typed_parent_policy_enforced,
    )


def actor_membership_evidence(
    anchors: ScopeAnchors,
    *,
    transaction_id: str = "tx-1",
    actor_id: str | None = "user-1",
    tenant_id: str | None = None,
    relationship_kind: str | None = "employee_membership",
    relationship_matches: int = 1,
    active: bool = True,
) -> ScopeActorMembershipEvidence:
    return ScopeActorMembershipEvidence(
        transaction_id=transaction_id,
        actor_id=actor_id,
        tenant_id=anchors.tenant_id if tenant_id is None else tenant_id,
        relationship_kind=relationship_kind,
        relationship_matches=relationship_matches,
        active=active,
    )


def mapping_input(kind: ScopeKind) -> ScopeMappingInput:
    return {
        ScopeKind.TENANT: ScopeMappingInput(kind, TENANT),
        ScopeKind.CABINET: ScopeMappingInput(kind, TENANT, cabinet_id=CABINET),
        ScopeKind.PROJECT: ScopeMappingInput(kind, TENANT, cabinet_id=CABINET, project_id=PROJECT),
        ScopeKind.PLUGIN_INSTANCE: ScopeMappingInput(
            kind, TENANT, cabinet_id=CABINET, plugin_instance_id="plugin-1"
        ),
    }[kind]


def candidate(
    *,
    anchors: ScopeAnchors | None = None,
    payload: dict[str, object] | None = None,
    source_id: str = "src-1",
    asserted_tenant_id: str | None = None,
    asserted_cabinet_id: str | None = None,
    asserted_project_id: str | None = None,
    source_state: SourceRowState = SourceRowState.ACTIVE,
    source_schema_version: str = "schema-1",
    canonical_schema_version: str = "scope-registry.v1",
) -> ScopeCandidate:
    typed_anchors = anchors or cabinet_anchors()
    return ScopeCandidate(
        source_kind="legacy_scope",
        source_id=source_id,
        schema_version=source_schema_version,
        payload=payload if payload is not None else {"scope": source_id, "state": source_state.value},
        anchors=typed_anchors,
        source_state=source_state,
        canonical_schema_version=canonical_schema_version,
        asserted_tenant_id=(typed_anchors.tenant_id if asserted_tenant_id is None else asserted_tenant_id),
        asserted_cabinet_id=(typed_anchors.cabinet_id if asserted_cabinet_id is None else asserted_cabinet_id),
        asserted_project_id=(typed_anchors.project_id if asserted_project_id is None else asserted_project_id),
    )
