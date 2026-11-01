"""MIG-M04-02 deterministic acceptance fixtures; intentionally not executed."""

from copy import deepcopy
from dataclasses import dataclass, field

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    BackfillOutcome,
    MigrationEvidenceContext,
    ScopeCandidate,
    ScopeMigrationResult,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    RlsContext,
    ScopeAnchors,
    ScopeKind,
    ScopeRegistryEntry,
    ScopeProvenance,
)

TENANT = "tenant-1"
CABINET = "cabinet-1"
PROJECT = "project-1"
CORRELATION = "corr-m04-02-1"
DIGEST = "sha256:" + "a" * 64


def tenant_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.TENANT, scope_id=TENANT, tenant_id=TENANT)


def cabinet_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.CABINET, scope_id=CABINET, tenant_id=TENANT, cabinet_id=CABINET)


def project_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PROJECT, scope_id=PROJECT, tenant_id=TENANT,
        cabinet_id=CABINET, project_id=PROJECT,
    )


def plugin_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="plugin-1", tenant_id=TENANT,
        cabinet_id=CABINET,
    )


def trusted_context() -> MigrationEvidenceContext:
    rls = RlsContext(tenant_id=TENANT, cabinet_id=CABINET, user_id="user-1", role="migration")
    return MigrationEvidenceContext(
        tenant_id=TENANT, authenticated=True, transaction_id="tx-1", correlation_id=CORRELATION,
        user_id="user-1", role="migration", rls_context=rls,
    )


def checkpoint(after_source_id: str | None = None, sequence: int = 0) -> BackfillCheckpoint:
    return BackfillCheckpoint(
        source_snapshot_id="snapshot-1", source_schema_version="schema-1",
        after_source_id=after_source_id, sequence=sequence, source_snapshot_digest=DIGEST,
    )


def candidate(
    *, anchors: ScopeAnchors | None = None, payload: dict[str, object] | None = None,
    source_id: str = "src-1", asserted_tenant_id: str | None = TENANT,
    asserted_cabinet_id: str | None = CABINET,
    asserted_project_id: str | None = None,
) -> ScopeCandidate:
    return ScopeCandidate(
        source_kind="legacy_scope", source_id=source_id, schema_version="scope-v1",
        payload=payload if payload is not None else {"scope": source_id, "state": "active"},
        anchors=anchors or cabinet_anchors(), asserted_tenant_id=asserted_tenant_id,
        asserted_cabinet_id=asserted_cabinet_id, asserted_project_id=asserted_project_id,
    )


def active_entry(anchors: ScopeAnchors, source: ScopeCandidate) -> ScopeRegistryEntry:
    return ScopeRegistryEntry(
        anchors=anchors, owner_state=OwnerState.ACTIVE,
        provenance=ScopeProvenance(
            source_kind=source.source_kind, source_id=source.source_id,
            schema_version=source.schema_version, payload_digest=source.source_digest,
        ),
    )


def parent_proof(*, membership: bool = True, project_compatible: bool = True) -> ParentCompatibilityProof:
    return ParentCompatibilityProof(
        tenant_exists=True, tenant_retained=True,
        cabinet_exists=True, cabinet_tenant_compatible=True,
        cabinet_membership_active=membership,
        project_exists=True, project_tenant_compatible=project_compatible,
        project_cabinet_compatible=project_compatible,
        project_policy_eligible=project_compatible,
    )


@dataclass(frozen=True)
class RedactedAuditEvent:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    outcome: str
    reason_code: str | None
    correlation_id: str


@dataclass
class AtomicMemoryScopePort:
    """Test double for one atomic transaction; not an application adapter."""

    proof: ParentCompatibilityProof = field(default_factory=parent_proof)
    mappings: dict[tuple[str, str, str], ScopeMigrationResult] = field(default_factory=dict)
    entries: dict[tuple[str, str], ScopeRegistryEntry] = field(default_factory=dict)
    quarantine: list[ScopeMigrationResult] = field(default_factory=list)
    audit: list[RedactedAuditEvent] = field(default_factory=list)
    checkpoint_sequence: int = 0

    async def classify_and_append(
        self, *, context, candidate, checkpoint, source_digest,
        proposed_entry, proposed_quarantine, reason_code,
    ) -> ScopeMigrationResult:
        key = (candidate.source_kind, candidate.source_id, candidate.schema_version)
        prior = self.mappings.get(key)
        proposed_identity = self._canonical_identity(candidate.anchors)
        if prior is not None and prior.source_digest == source_digest and prior.canonical_identity == proposed_identity:
            return ScopeMigrationResult(
                outcome=BackfillOutcome.REPLAY, source_kind=candidate.source_kind,
                source_id=candidate.source_id, schema_version=candidate.schema_version,
                source_digest=source_digest, canonical_digest=prior.canonical_digest,
                checkpoint=prior.checkpoint, reason_code=None,
                scope_kind=candidate.anchors.scope_kind if candidate.anchors else None,
                scope_id=candidate.anchors.scope_id if candidate.anchors else None,
                correlation_id=context.correlation_id,
                canonical_identity=proposed_identity,
            )

        staged_mappings = deepcopy(self.mappings)
        staged_entries = deepcopy(self.entries)
        staged_quarantine = deepcopy(self.quarantine)
        staged_audit = deepcopy(self.audit)
        staged_sequence = self.checkpoint_sequence
        prior = staged_mappings.get(key)
        if checkpoint.sequence != staged_sequence + 1:
            outcome, reason = BackfillOutcome.BLOCKED, "checkpoint_cas_mismatch"
        elif (
            not context.authenticated
            or context.rls_context is None
            or context.rls_context.tenant_id != context.tenant_id
            or context.rls_context.user_id != context.user_id
            or context.rls_context.role != context.role
        ):
            outcome, reason = BackfillOutcome.BLOCKED, "invalid_transaction_context"
        elif prior is not None:
            reason_code = (
                "source_digest_changed"
                if prior.source_digest != source_digest
                else "canonical_identity_changed"
            )
            outcome, reason = BackfillOutcome.CONFLICT, reason_code
        elif reason_code is not None:
            outcome, reason = BackfillOutcome.QUARANTINED, reason_code
        elif candidate.anchors is None or not self.proof.valid_for_scope(candidate.anchors.scope_kind):
            outcome, reason = BackfillOutcome.QUARANTINED, "parent_compatibility_unproven"
        elif self._source_assertions_mismatch(candidate):
            outcome, reason = BackfillOutcome.QUARANTINED, "source_anchor_assertion_mismatch"
        elif candidate.anchors.scope_kind is ScopeKind.CABINET and any(
            entry.anchors.scope_id == candidate.anchors.scope_id
            and entry.anchors.tenant_id != candidate.anchors.tenant_id
            for entry in staged_entries.values()
        ):
            outcome, reason = BackfillOutcome.QUARANTINED, "duplicate_or_ambiguous_scope"
        else:
            outcome, reason = BackfillOutcome.ACCEPTED, None

        canonical_digest = None
        if outcome is BackfillOutcome.ACCEPTED and proposed_entry is not None:
            anchor = proposed_entry.anchors
            key_anchor = (anchor.scope_kind.value, anchor.scope_id)
            existing = staged_entries.get(key_anchor)
            if existing is not None and existing.anchors != proposed_entry.anchors:
                outcome, reason = BackfillOutcome.QUARANTINED, "duplicate_or_ambiguous_scope"
            elif existing is None:
                staged_entries[key_anchor] = proposed_entry
                canonical_digest = "sha256:" + "b" * 64
            else:
                canonical_digest = "sha256:" + "b" * 64

        result = ScopeMigrationResult(
            outcome=outcome, source_kind=candidate.source_kind, source_id=candidate.source_id,
            schema_version=candidate.schema_version, source_digest=source_digest,
            canonical_digest=canonical_digest, checkpoint=checkpoint, reason_code=reason,
            scope_kind=candidate.anchors.scope_kind if candidate.anchors else None,
            scope_id=candidate.anchors.scope_id if candidate.anchors else None,
            correlation_id=context.correlation_id,
            parent_compatibility_proof=self.proof if outcome is BackfillOutcome.ACCEPTED else None,
            canonical_identity=self._canonical_identity(candidate.anchors),
        )
        if outcome is not BackfillOutcome.CONFLICT:
            staged_mappings[key] = result
        if outcome in {BackfillOutcome.QUARANTINED, BackfillOutcome.CONFLICT}:
            staged_quarantine.append(result)
        if outcome is not BackfillOutcome.BLOCKED:
            staged_audit.append(RedactedAuditEvent(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                source_digest=source_digest,
                outcome=outcome.value,
                reason_code=reason,
                correlation_id=context.correlation_id,
            ))
            staged_sequence = checkpoint.sequence

        self.mappings = staged_mappings
        self.entries = staged_entries
        self.quarantine = staged_quarantine
        self.audit = staged_audit
        self.checkpoint_sequence = staged_sequence
        return result

    async def resolve_active(self, *, context, anchors):
        if (
            not context.authenticated
            or context.rls_context is None
            or context.rls_context.tenant_id != anchors.tenant_id
            or context.rls_context.cabinet_id != anchors.cabinet_id
            or context.rls_context.project_id != anchors.project_id
        ):
            return None
        matches = [
            entry for entry in self.entries.values()
            if entry.owner_state is OwnerState.ACTIVE and entry.anchors == anchors
        ]
        if len(matches) != 1:
            return None
        return matches[0], self.proof

    @staticmethod
    def _canonical_identity(anchors: ScopeAnchors | None) -> tuple[str, str, str, str | None, str | None, str | None] | None:
        if anchors is None:
            return None
        return (
            anchors.scope_kind.value, anchors.scope_id, anchors.tenant_id,
            anchors.cabinet_id, anchors.project_id, anchors.plugin_instance_id,
        )

    @staticmethod
    def _source_assertions_mismatch(candidate: ScopeCandidate) -> bool:
        anchors = candidate.anchors
        return bool(
            anchors is None
            or (candidate.asserted_tenant_id is not None and candidate.asserted_tenant_id != anchors.tenant_id)
            or (candidate.asserted_cabinet_id is not None and candidate.asserted_cabinet_id != anchors.cabinet_id)
            or (candidate.asserted_project_id is not None and candidate.asserted_project_id != anchors.project_id)
        )
