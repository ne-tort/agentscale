"""Bounded M04 scope evidence migration with mandatory trusted persistence ports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)


class BackfillOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    DEFERRED = "deferred"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class ScopeResolution(StrEnum):
    ACTIVE = "active"
    DENY = "deny"
    DEFERRED = "deferred"
    QUARANTINED = "quarantined"


class SourceRowState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"
    DELETED = "deleted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MigrationEvidenceContext:
    """Server-derived transaction context; request assertions are never authority."""

    tenant_id: str | None
    authenticated: bool
    transaction_id: str | None
    correlation_id: str | None
    user_id: str | None
    role: str | None
    rls_context: RlsContext | None
    writer_generation: int | None = None

    @property
    def usable(self) -> bool:
        return bool(
            self.authenticated
            and self.tenant_id
            and self.transaction_id
            and self.correlation_id
            and self.user_id
            and self.role
            and self.writer_generation is not None
            and self.writer_generation > 0
            and self.rls_context
            and self.rls_context.tenant_id == self.tenant_id
            and self.rls_context.user_id == self.user_id
            and self.rls_context.role == self.role
        )

    def matches_scope(self, anchors: ScopeAnchors) -> bool:
        rls = self.rls_context
        return bool(
            self.usable
            and self.tenant_id == anchors.tenant_id
            and rls is not None
            and rls.tenant_id == anchors.tenant_id
            and rls.cabinet_id == anchors.cabinet_id
            and rls.project_id == anchors.project_id
        )


@dataclass(frozen=True, slots=True)
class ScopeCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: dict[str, object]
    anchors: ScopeAnchors | None
    source_state: SourceRowState = SourceRowState.UNKNOWN
    canonical_schema_version: str = "scope-registry.v1"
    # Source fields are assertions to compare with trusted parent resolution.
    asserted_tenant_id: str | None = None
    asserted_cabinet_id: str | None = None
    asserted_project_id: str | None = None
    canonical_payload_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.source_kind.strip() or not self.source_id.strip() or not self.schema_version.strip():
            raise ValueError("source kind, ID and schema version are required")
        if len(self.source_id) > 128:
            raise ValueError("source ID exceeds owner mapping evidence width")
        if len(self.source_kind) > 64 or len(self.schema_version) > 32:
            raise ValueError("source identity exceeds the owner mapping evidence contract")
        if not self.canonical_schema_version.strip() or len(self.canonical_schema_version) > 32:
            raise ValueError("canonical scope schema version is required")
        for field_name in ("asserted_tenant_id", "asserted_cabinet_id", "asserted_project_id"):
            asserted_id = getattr(self, field_name)
            if asserted_id is not None and (not asserted_id.strip() or len(asserted_id) > 128):
                raise ValueError(f"{field_name} exceeds source assertion evidence bounds")
        if self.anchors is not None:
            for field_name in (
                "scope_id", "tenant_id", "cabinet_id", "project_id", "plugin_instance_id"
            ):
                anchor_id = getattr(self.anchors, field_name)
                if anchor_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", anchor_id):
                    raise ValueError(f"typed {field_name} is malformed for the owner registry")
        try:
            payload_snapshot = json.dumps(
                self.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("scope source payload must have deterministic finite JSON encoding") from exc
        object.__setattr__(self, "canonical_payload_json", payload_snapshot)

    @property
    def mapping_identity(self) -> tuple[str, str, str]:
        # M04-01 uniquely keys source mappings by source schema_version.
        return (self.source_kind, self.source_id, self.schema_version)

    @property
    def source_digest(self) -> str:
        encoded = json.dumps(
            {
                "source_kind": self.source_kind,
                "source_id": self.source_id,
                "source_schema_version": self.schema_version,
                "canonical_schema_version": self.canonical_schema_version,
                "anchors": self.anchors.model_dump(mode="json") if self.anchors is not None else None,
                "payload": json.loads(self.canonical_payload_json),
                "source_state": self.source_state.value,
                "asserted_tenant_id": self.asserted_tenant_id,
                "asserted_cabinet_id": self.asserted_cabinet_id,
                "asserted_project_id": self.asserted_project_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScopeMappingInput:
    """Untrusted legacy scope tuple; mapping is deterministic but not authoritative."""

    scope_kind: ScopeKind
    tenant_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_instance_id: str | None = None

    def to_anchors(self) -> ScopeAnchors:
        scope_id = {
            ScopeKind.TENANT: self.tenant_id,
            ScopeKind.CABINET: self.cabinet_id,
            ScopeKind.PROJECT: self.project_id,
            ScopeKind.PLUGIN_INSTANCE: self.plugin_instance_id,
        }[self.scope_kind]
        if scope_id is None:
            raise ValueError(f"{self.scope_kind.value} scope ID is required")
        return ScopeAnchors(
            scope_kind=self.scope_kind,
            scope_id=scope_id,
            tenant_id=self.tenant_id,
            cabinet_id=self.cabinet_id,
            project_id=self.project_id,
            plugin_instance_id=self.plugin_instance_id,
        )


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    source_snapshot_id: str
    source_schema_version: str
    after_source_id: str | None
    sequence: int
    source_snapshot_digest: str
    tenant_id: str
    writer_generation: int = 0
    stream_kind: str = "scope_registry"
    canonical_schema_version: str = "scope-registry.v1"

    def __post_init__(self) -> None:
        if not self.source_snapshot_id.strip() or len(self.source_snapshot_id) > 128:
            raise ValueError("snapshot identity is required and must fit the checkpoint contract")
        if not self.source_schema_version.strip() or len(self.source_schema_version) > 32:
            raise ValueError("source schema version is required and must fit the checkpoint contract")
        if not self.tenant_id.strip() or len(self.tenant_id) > 40:
            raise ValueError("checkpoint requires a canonical companies.id tenant")
        if self.writer_generation <= 0:
            raise ValueError("checkpoint writer generation must be positive")
        if not self.stream_kind.strip() or len(self.stream_kind) > 64:
            raise ValueError("stream identity is required and must fit the checkpoint contract")
        if not self.canonical_schema_version.strip() or len(self.canonical_schema_version) > 32:
            raise ValueError("canonical schema identity is required and must fit the checkpoint contract")
        if (
            self.sequence < 0
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_snapshot_digest)
        ):
            raise ValueError("invalid checkpoint sequence or source snapshot digest")

    @property
    def stream_key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.tenant_id,
            self.stream_kind,
            self.source_snapshot_id,
            self.source_schema_version,
            self.canonical_schema_version,
            self.writer_generation,
        )

    @property
    def lineage_key(self) -> tuple[str, str, str, str, str, int, str]:
        return (*self.stream_key, self.source_snapshot_digest)


@dataclass(frozen=True, slots=True)
class ScopeStreamIdentity:
    """Stable source snapshot identity used to load a persisted resume cursor."""

    source_snapshot_id: str
    source_schema_version: str
    source_snapshot_digest: str
    tenant_id: str
    writer_generation: int
    stream_kind: str = "scope_registry"
    canonical_schema_version: str = "scope-registry.v1"

    def __post_init__(self) -> None:
        if not self.source_snapshot_id.strip() or len(self.source_snapshot_id) > 128:
            raise ValueError("snapshot identity and schema version are required")
        if not self.source_schema_version.strip() or len(self.source_schema_version) > 32:
            raise ValueError("snapshot identity and schema version are required")
        if not self.tenant_id.strip() or len(self.tenant_id) > 40:
            raise ValueError("tenant identity must fit canonical companies.id")
        if self.writer_generation <= 0:
            raise ValueError("writer generation must be positive")
        if len(self.stream_kind) > 64 or len(self.canonical_schema_version) > 32:
            raise ValueError("stream identity exceeds its persistence contract")
        if not self.stream_kind.strip() or not self.canonical_schema_version.strip():
            raise ValueError("stream and canonical schema identity are required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_snapshot_digest):
            raise ValueError("invalid source snapshot digest")

    @property
    def stream_key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.tenant_id,
            self.stream_kind,
            self.source_snapshot_id,
            self.source_schema_version,
            self.canonical_schema_version,
            self.writer_generation,
        )

    @property
    def lineage_key(self) -> tuple[str, str, str, str, str, int, str]:
        return (*self.stream_key, self.source_snapshot_digest)

    def initial_checkpoint(self) -> BackfillCheckpoint:
        return BackfillCheckpoint(
            source_snapshot_id=self.source_snapshot_id,
            source_schema_version=self.source_schema_version,
            after_source_id=None,
            sequence=0,
            source_snapshot_digest=self.source_snapshot_digest,
            tenant_id=self.tenant_id,
            writer_generation=self.writer_generation,
            stream_kind=self.stream_kind,
            canonical_schema_version=self.canonical_schema_version,
        )


@dataclass(frozen=True, slots=True)
class ScopeCheckpointResume:
    checkpoint: BackfillCheckpoint | None
    reason_code: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ScopeParentEvidence:
    """Cardinality and lifecycle evidence returned by the same trusted transaction."""

    tenant_matches: int
    cabinet_matches: int
    active_cabinet_memberships: int
    project_matches: int
    active_registry_matches: int
    transaction_id: str | None = None
    tenant_deleted: bool = False
    cabinet_deleted: bool = False
    project_deleted: bool = False
    tenant_archived: bool = False
    cabinet_archived: bool = False
    project_archived: bool = False
    tenant_id: str | None = None
    cabinet_id: str | None = None
    cabinet_membership_kind: str | None = None
    membership_tenant_id: str | None = None
    membership_cabinet_id: str | None = None
    project_id: str | None = None
    project_tenant_id: str | None = None
    project_cabinet_id: str | None = None
    active_registry_anchors: ScopeAnchors | None = None

    def __post_init__(self) -> None:
        if min(
            self.tenant_matches,
            self.cabinet_matches,
            self.active_cabinet_memberships,
            self.project_matches,
            self.active_registry_matches,
        ) < 0:
            raise ValueError("parent evidence cardinalities cannot be negative")

    def valid_in_transaction(
        self, *, context: MigrationEvidenceContext, anchors: ScopeAnchors
    ) -> bool:
        return bool(
            self.transaction_id
            and self.transaction_id == context.transaction_id
            and self.valid_parent_for(anchors)
        )

    def valid_parent_for(self, anchors: ScopeAnchors) -> bool:
        return self.invalid_reason_for(anchors) is None

    def invalid_reason_for(self, anchors: ScopeAnchors) -> str | None:
        kind = anchors.scope_kind
        if self.tenant_matches != 1:
            return "tenant_parent_missing" if self.tenant_matches == 0 else "tenant_parent_ambiguous"
        if self.tenant_deleted:
            return "tenant_parent_deleted"
        if self.tenant_archived:
            return "tenant_parent_archived"
        if self.tenant_id != anchors.tenant_id:
            return "cross_tenant_parent"
        if kind is ScopeKind.TENANT:
            return self._active_registry_reason(anchors)
        if self.cabinet_matches != 1:
            return "cabinet_parent_missing" if self.cabinet_matches == 0 else "cabinet_parent_ambiguous"
        if self.cabinet_deleted:
            return "cabinet_parent_deleted"
        if self.cabinet_archived:
            return "cabinet_parent_archived"
        if self.cabinet_id != anchors.cabinet_id:
            return "cabinet_parent_mismatch"
        if self.active_cabinet_memberships != 1:
            return (
                "cabinet_membership_missing"
                if self.active_cabinet_memberships == 0
                else "cabinet_membership_ambiguous"
            )
        if self.cabinet_membership_kind != "cabinet_company_grant":
            return "cabinet_membership_kind_unproven"
        if self.membership_tenant_id != anchors.tenant_id:
            return "cross_tenant_cabinet_membership"
        if self.membership_cabinet_id != anchors.cabinet_id:
            return "cabinet_membership_mismatch"
        if kind is ScopeKind.CABINET:
            return self._active_registry_reason(anchors)
        if kind is ScopeKind.PROJECT:
            if self.project_matches != 1:
                return "project_parent_missing" if self.project_matches == 0 else "project_parent_ambiguous"
            if self.project_deleted:
                return "project_parent_deleted"
            if self.project_archived:
                return "project_parent_archived"
            if self.project_id != anchors.project_id:
                return "project_parent_mismatch"
            if self.project_tenant_id != anchors.tenant_id:
                return "cross_tenant_project_parent"
            if self.project_cabinet_id != anchors.cabinet_id:
                return "project_cabinet_mismatch"
            return self._active_registry_reason(anchors)
        return "plugin_instance_scope_denied"

    def _active_registry_reason(self, anchors: ScopeAnchors) -> str | None:
        if self.active_registry_matches != 1:
            return (
                "active_scope_missing"
                if self.active_registry_matches == 0
                else "active_scope_ambiguous"
            )
        if self.active_registry_anchors != anchors:
            return "active_scope_anchor_mismatch"
        return None


@dataclass(frozen=True, slots=True)
class ScopeRlsEvidence:
    """Same-transaction evidence that tenant RLS is enabled, forced and reviewed."""

    transaction_id: str
    tenant_id: str | None
    cabinet_id: str | None
    project_id: str | None
    row_security_enabled: bool
    row_security_forced: bool
    database_role: str | None
    security_review_id: str | None
    policy_names: tuple[str, ...]
    tenant_policy_enforced: bool = False
    using_policy_enforced: bool = False
    with_check_policy_enforced: bool = False
    missing_context_denies: bool = False
    role_not_bypass_rls: bool = False
    typed_parent_policy_enforced: bool = False

    def valid_for(
        self, *, context: MigrationEvidenceContext, anchors: ScopeAnchors
    ) -> bool:
        return bool(
            context.transaction_id
            and self.transaction_id == context.transaction_id
            and self.tenant_id == anchors.tenant_id
            and self.cabinet_id == anchors.cabinet_id
            and self.project_id == anchors.project_id
            and self.row_security_enabled
            and self.row_security_forced
            and self.tenant_policy_enforced
            and self.using_policy_enforced
            and self.with_check_policy_enforced
            and self.missing_context_denies
            and self.role_not_bypass_rls
            and self.typed_parent_policy_enforced
            and self.database_role
            and self.security_review_id
            and self.policy_names
            and all(name.strip() for name in self.policy_names)
        )



@dataclass(frozen=True, slots=True)
class ScopeActorMembershipEvidence:
    """Transaction-bound proof of actor membership in the resolved tenant."""

    transaction_id: str
    actor_id: str | None
    tenant_id: str | None
    relationship_kind: str | None
    relationship_matches: int
    active: bool

    def valid_for(
        self, *, context: MigrationEvidenceContext, anchors: ScopeAnchors
    ) -> bool:
        return bool(
            context.transaction_id
            and self.transaction_id == context.transaction_id
            and self.actor_id == context.user_id
            and self.tenant_id == anchors.tenant_id
            and self.relationship_kind in {"employee_membership", "company_principal_binding"}
            and self.relationship_matches == 1
            and self.active
        )


@dataclass(frozen=True, slots=True)
class ScopeSourceEvidence:
    """Redacted source metadata passed to persistence; raw payload is excluded."""

    source_kind: str
    source_id: str
    schema_version: str
    canonical_schema_version: str
    source_digest: str
    source_state: SourceRowState
    anchors: ScopeAnchors | None
    asserted_tenant_id: str | None
    asserted_cabinet_id: str | None
    asserted_project_id: str | None

    def __post_init__(self) -> None:
        if not self.source_kind.strip() or len(self.source_kind) > 64:
            raise ValueError("source kind exceeds mapping evidence width")
        if not self.source_id.strip() or len(self.source_id) > 128:
            raise ValueError("source ID exceeds owner mapping evidence width")
        if not self.schema_version.strip() or len(self.schema_version) > 32:
            raise ValueError("source schema version exceeds mapping evidence width")
        if not self.canonical_schema_version.strip() or len(self.canonical_schema_version) > 32:
            raise ValueError("canonical schema version exceeds mapping evidence width")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_digest):
            raise ValueError("invalid source digest for trusted mapping evidence")


@dataclass(frozen=True, slots=True)
class ScopeSourceQuarantineEvidence:
    """Redacted mapping evidence for source IDs too wide for typed provenance."""

    source_kind: str
    source_id: str
    schema_version: str
    canonical_schema_version: str
    source_digest: str
    reason_code: str = "source_id_exceeds_typed_provenance"

    def __post_init__(self) -> None:
        if not self.source_kind.strip() or len(self.source_kind) > 64:
            raise ValueError("invalid source kind for redacted evidence")
        if not self.source_id.strip() or len(self.source_id) > 128:
            raise ValueError("source ID exceeds owner mapping evidence width")
        if not self.schema_version.strip() or len(self.schema_version) > 32:
            raise ValueError("invalid source schema version for redacted evidence")
        if not self.canonical_schema_version.strip() or len(self.canonical_schema_version) > 32:
            raise ValueError("invalid canonical schema version for redacted evidence")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_digest):
            raise ValueError("invalid source digest for redacted evidence")
        if not self.reason_code.strip():
            raise ValueError("source quarantine reason is required")


class MappingReplayDecision(StrEnum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ExistingScopeMapping:
    """Redacted prior mapping read inside the write transaction by its source key."""

    mapping_identity: tuple[str, str, str]
    source_digest: str
    canonical_schema_version: str
    canonical_identity: tuple[str, str, str, str | None, str | None, str | None] | None
    outcome: BackfillOutcome
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class MappingReplayAssessment:
    decision: MappingReplayDecision
    reason_code: str | None


def assess_mapping_replay(
    candidate: ScopeCandidate, existing: ExistingScopeMapping | None
) -> MappingReplayAssessment:
    """Classify an existing source-key row without mutating first-write evidence."""
    if existing is None:
        return MappingReplayAssessment(MappingReplayDecision.NEW, None)
    if existing.mapping_identity != candidate.mapping_identity:
        return MappingReplayAssessment(MappingReplayDecision.CONFLICT, "source_identity_mismatch")
    if existing.canonical_schema_version != candidate.canonical_schema_version:
        return MappingReplayAssessment(
            MappingReplayDecision.CONFLICT, "canonical_schema_version_changed"
        )
    if existing.source_digest != candidate.source_digest:
        return MappingReplayAssessment(MappingReplayDecision.CONFLICT, "source_digest_changed")
    expected_identity = (
        ScopeMigrationCoordinator._canonical_identity(candidate.anchors)
        if candidate.anchors is not None
        else None
    )
    if existing.outcome is BackfillOutcome.ACCEPTED and existing.canonical_identity != expected_identity:
        return MappingReplayAssessment(MappingReplayDecision.CONFLICT, "canonical_identity_changed")
    if existing.outcome is not BackfillOutcome.ACCEPTED and existing.canonical_identity not in (
        None,
        expected_identity,
    ):
        return MappingReplayAssessment(MappingReplayDecision.CONFLICT, "canonical_identity_changed")
    return MappingReplayAssessment(MappingReplayDecision.REPLAY, existing.reason_code)


@dataclass(frozen=True, slots=True)
class ScopeMigrationResult:
    outcome: BackfillOutcome
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    canonical_schema_version: str
    canonical_digest: str | None
    checkpoint: BackfillCheckpoint
    reason_code: str | None
    scope_kind: ScopeKind | None
    scope_id: str | None
    correlation_id: str
    writer_generation: int | None = None
    parent_compatibility_proof: ParentCompatibilityProof | None = None
    parent_evidence: ScopeParentEvidence | None = None
    parent_compatibility_transaction_id: str | None = None
    canonical_identity: tuple[str, str, str, str | None, str | None, str | None] | None = None
    canonical_positive_writes: bool = False
    mapping_id: str | None = None
    audit_event_id: str | None = None
    quarantine_id: str | None = None
    rls_evidence: ScopeRlsEvidence | None = None
    actor_membership_evidence: ScopeActorMembershipEvidence | None = None
    resolved_entry: ScopeRegistryEntry | None = None
    original_outcome: BackfillOutcome | None = None


@dataclass(frozen=True, slots=True)
class ScopeResolutionResult:
    outcome: ScopeResolution
    entry: ScopeRegistryEntry | None
    reason_code: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ScopeResolutionEvidence:
    """Untrusted transaction return bundle revalidated by the application boundary."""

    entry: ScopeRegistryEntry
    parent_proof: ParentCompatibilityProof
    parent_compatibility_transaction_id: str
    parent_evidence: ScopeParentEvidence
    rls_evidence: ScopeRlsEvidence
    actor_membership_evidence: ScopeActorMembershipEvidence


class ResolveScopePort(Protocol):
    """Trusted active-only resolution; no ORM or test fake is a production binding."""

    async def resolve_active(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> ScopeResolutionEvidence | None:
        """Return exactly one row with same-transaction parent cardinality evidence."""
        ...


class ScopeCheckpointPort(Protocol):
    async def load_checkpoint(
        self, *, context: MigrationEvidenceContext, stream: ScopeStreamIdentity
    ) -> BackfillCheckpoint | None:
        """Read the durable cursor by tenant/snapshot/schema lineage, inside the transaction."""
        ...


class TrustedScopeTransactionPort(ResolveScopePort, ScopeCheckpointPort, Protocol):
    """Must implement atomic transaction-local mapping and resolver operations."""

    async def classify_and_append(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        source_mapping_identity: tuple[str, str, str],
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        proposed_entry: ScopeRegistryEntry | None,
        proposed_quarantine: QuarantineRecord | ScopeSourceQuarantineEvidence | None,
        reason_code: str | None,
    ) -> ScopeMigrationResult:
        """Atomically map one source row and advance its tenant stream checkpoint.

        The DB transaction must validate/decode only redacted source metadata provided
        alongside the digest, establish all parent and exact-active-row cardinalities,
        compare the stream checkpoint via CAS, then append mapping, classification,
        audit and checkpoint. It re-derives parent tuples and active membership from
        authoritative tables (not ORM hints), rejects deleted/archived parents, and
        preserves the first mapping on source-digest or canonical-identity conflict.
        The transaction derives the current checkpoint in the same transaction and CAS-compares the expected sequence/cursor plus snapshot digest lineage. A changed snapshot digest or stale cursor is a conflict/quarantine and never resets or overwrites the checkpoint. Exact source mapping replay returns the original mapping/checkpoint without duplicating audit or consuming another cursor position.
        `proposed_entry`, when present, is quarantined evidence only. The transaction
        must derive all parents and active membership before writing/upserting any
        active registry entry; only after an exact post-write active-row read may it
        return `ACCEPTED` with `resolved_entry` set to that persisted row. Derive the
        actor's current tenant membership/principal binding in the same transaction.
        Look up `(source_kind, source_id, source_schema_version)` to match the M04-01
        unique key, then call `assess_mapping_replay`: exact replay returns the original
        mapping/audit identity without advancing the cursor; a digest/schema/identity
        conflict preserves the first mapping and appends redacted conflict/quarantine
        audit evidence. The source snapshot digest and cursor CAS are verified in this
        same transaction. Active resolution must additionally return reviewed RLS
        evidence for this transaction. No positive grant/instance write is permitted.
        """
        ...


class ScopeMigrationCoordinator:
    """Process exactly one source row per call; no local fallback persistence exists."""

    def __init__(self, port: TrustedScopeTransactionPort | None) -> None:
        self._port = port

    async def resume_checkpoint(
        self, *, stream: ScopeStreamIdentity, context: MigrationEvidenceContext
    ) -> ScopeCheckpointResume:
        correlation_id = context.correlation_id or "missing-correlation"
        if not context.usable:
            return ScopeCheckpointResume(None, "missing_trusted_transaction_context_or_port", correlation_id)
        # A caller-supplied stream is compared with the trusted transaction
        # context before any port is consulted: a foreign tenant or a stale
        # writer generation is a lineage defect of this call, not a retryable
        # port outage, and must never be reported as one.
        if stream.tenant_id != context.tenant_id:
            return ScopeCheckpointResume(None, "checkpoint_tenant_mismatch", correlation_id)
        if stream.writer_generation != context.writer_generation:
            return ScopeCheckpointResume(None, "writer_generation_mismatch", correlation_id)
        if self._port is None:
            return ScopeCheckpointResume(None, "missing_trusted_transaction_context_or_port", correlation_id)
        try:
            stored = await self._port.load_checkpoint(context=context, stream=stream)
        except Exception:
            return ScopeCheckpointResume(None, "checkpoint_read_failed", correlation_id)
        if stored is None:
            return ScopeCheckpointResume(stream.initial_checkpoint(), "checkpoint_initialized", correlation_id)
        try:
            stored.__post_init__()
        except (AttributeError, TypeError, ValueError):
            return ScopeCheckpointResume(None, "checkpoint_invalid", correlation_id)
        if stored.stream_key != stream.stream_key or stored.source_snapshot_digest != stream.source_snapshot_digest:
            return ScopeCheckpointResume(None, "checkpoint_lineage_changed", correlation_id)
        if stored.sequence == 0 and stored.after_source_id is not None:
            return ScopeCheckpointResume(None, "checkpoint_cursor_invalid", correlation_id)
        if stored.sequence > 0 and stored.after_source_id is None:
            return ScopeCheckpointResume(None, "checkpoint_cursor_invalid", correlation_id)
        if stored.after_source_id is not None and len(stored.after_source_id) > 128:
            return ScopeCheckpointResume(None, "checkpoint_cursor_invalid", correlation_id)
        return ScopeCheckpointResume(stored, "checkpoint_resumed", correlation_id)

    async def process_row(
        self,
        *,
        candidate: ScopeCandidate,
        checkpoint: BackfillCheckpoint,
        context: MigrationEvidenceContext,
    ) -> ScopeMigrationResult:
        digest = candidate.source_digest
        correlation_id = context.correlation_id or "missing-correlation"
        if not context.usable or self._port is None:
            return ScopeMigrationResult(
                outcome=BackfillOutcome.BLOCKED,
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                source_digest=digest,
                canonical_schema_version=candidate.canonical_schema_version,
                canonical_digest=None,
                checkpoint=checkpoint,
                reason_code="missing_trusted_transaction_context_or_port",
                scope_kind=candidate.anchors.scope_kind if candidate.anchors else None,
                scope_id=candidate.anchors.scope_id if candidate.anchors else None,
                correlation_id=correlation_id,
            )
        if checkpoint.tenant_id != context.tenant_id:
            return self._blocked(candidate, checkpoint, correlation_id, "checkpoint_tenant_mismatch")
        if context.writer_generation is None or checkpoint.writer_generation != context.writer_generation:
            return self._blocked(candidate, checkpoint, correlation_id, "writer_generation_mismatch")
        if candidate.schema_version != checkpoint.source_schema_version:
            return self._blocked(candidate, checkpoint, correlation_id, "source_schema_version_mismatch")
        if candidate.canonical_schema_version != checkpoint.canonical_schema_version:
            return self._blocked(candidate, checkpoint, correlation_id, "canonical_schema_version_mismatch")
        expected_identity = self._canonical_identity(candidate.anchors) if candidate.anchors else None
        next_checkpoint = BackfillCheckpoint(
            source_snapshot_id=checkpoint.source_snapshot_id,
            source_schema_version=checkpoint.source_schema_version,
            after_source_id=candidate.source_id,
            sequence=checkpoint.sequence + 1,
            source_snapshot_digest=checkpoint.source_snapshot_digest,
            tenant_id=checkpoint.tenant_id,
            writer_generation=checkpoint.writer_generation,
            stream_kind=checkpoint.stream_kind,
            canonical_schema_version=checkpoint.canonical_schema_version,
        )
        reason, entry, quarantine = self._proposed_outcome(candidate, context, digest)
        source_evidence = ScopeSourceEvidence(
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            canonical_schema_version=candidate.canonical_schema_version,
            source_digest=digest,
            source_state=candidate.source_state,
            anchors=candidate.anchors,
            asserted_tenant_id=candidate.asserted_tenant_id,
            asserted_cabinet_id=candidate.asserted_cabinet_id,
            asserted_project_id=candidate.asserted_project_id,
        )
        result = await self._port.classify_and_append(
            context=context,
            candidate=source_evidence,
            source_mapping_identity=candidate.mapping_identity,
            expected_checkpoint=checkpoint,
            next_checkpoint=next_checkpoint,
            proposed_entry=entry,
            proposed_quarantine=quarantine,
            reason_code=reason,
        )
        if result.source_digest != digest or result.correlation_id != context.correlation_id:
            raise RuntimeError("trusted transaction returned evidence for a different source operation")
        if result.source_kind != candidate.source_kind or result.source_id != candidate.source_id:
            raise RuntimeError("trusted transaction returned evidence for a different source identity")
        if result.schema_version != candidate.schema_version:
            raise RuntimeError("trusted transaction returned evidence for a different source schema")
        if (
            result.canonical_schema_version != candidate.canonical_schema_version
            and result.outcome is not BackfillOutcome.CONFLICT
        ):
            raise RuntimeError("trusted transaction returned evidence for a different canonical schema")
        if result.canonical_positive_writes:
            raise RuntimeError("M04 scope evidence cannot enable canonical positive writes")
        if result.outcome in {
            BackfillOutcome.ACCEPTED,
            BackfillOutcome.REPLAY,
            BackfillOutcome.QUARANTINED,
            BackfillOutcome.DEFERRED,
        } and (not result.mapping_id or not result.audit_event_id):
            raise RuntimeError("durable mapping outcome must return mapping and audit identities")
        if result.outcome is BackfillOutcome.CONFLICT and not result.audit_event_id:
            raise RuntimeError("durable conflict outcome must return an audit identity")
        if result.outcome in {
            BackfillOutcome.QUARANTINED,
            BackfillOutcome.DEFERRED,
        } and not result.quarantine_id:
            raise RuntimeError("quarantined mapping outcome must return its quarantine identity")
        if result.outcome is BackfillOutcome.ACCEPTED and (
            reason is not None
            or entry is None
            or result.resolved_entry is None
            or result.resolved_entry.owner_state is not OwnerState.ACTIVE
            or result.resolved_entry.anchors != entry.anchors
            or result.resolved_entry.provenance.source_kind != candidate.source_kind
            or result.resolved_entry.provenance.source_id != candidate.source_id
            or result.resolved_entry.provenance.schema_version != candidate.schema_version
            or result.resolved_entry.provenance.payload_digest != digest
            or result.parent_compatibility_proof is None
            or result.parent_compatibility_transaction_id != context.transaction_id
            or not result.parent_compatibility_proof.valid_for_scope(result.resolved_entry.anchors.scope_kind)
            or result.parent_evidence is None
            or result.parent_evidence.transaction_id != context.transaction_id
            or not result.parent_evidence.valid_parent_for(result.resolved_entry.anchors)
            or result.rls_evidence is None
            or not result.rls_evidence.valid_for(context=context, anchors=result.resolved_entry.anchors)
            or result.actor_membership_evidence is None
            or not result.actor_membership_evidence.valid_for(
                context=context, anchors=result.resolved_entry.anchors
            )
            or result.canonical_identity != self._canonical_identity(result.resolved_entry.anchors)
            or result.canonical_digest
            != self._canonical_digest(result.resolved_entry, candidate.canonical_schema_version)
        ):
            raise RuntimeError("persistence port accepted a row without exact active parent evidence")
        if candidate.anchors and candidate.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            if result.outcome is BackfillOutcome.ACCEPTED:
                raise RuntimeError("plugin-instance scope must remain deferred until M05")
            if reason == "plugin_instance_deferred_m05" and result.outcome is not BackfillOutcome.DEFERRED:
                raise RuntimeError("plugin-instance source row must return a deferred outcome")
        if result.outcome is BackfillOutcome.REPLAY:
            if result.original_outcome not in {
                BackfillOutcome.ACCEPTED,
                BackfillOutcome.QUARANTINED,
                BackfillOutcome.DEFERRED,
                BackfillOutcome.CONFLICT,
            }:
                raise RuntimeError("trusted transaction replay did not preserve the original mapping outcome")
            if result.original_outcome is BackfillOutcome.ACCEPTED and result.canonical_identity != expected_identity:
                raise RuntimeError("trusted transaction replayed a different source mapping")
            if result.original_outcome in {BackfillOutcome.QUARANTINED, BackfillOutcome.DEFERRED} and result.canonical_identity is not None:
                raise RuntimeError("quarantined replay cannot claim a canonical active identity")
            if result.original_outcome is BackfillOutcome.CONFLICT and not result.quarantine_id:
                raise RuntimeError("conflict replay must retain its redacted quarantine observation")
            if (
                result.checkpoint.stream_key != checkpoint.stream_key
                or result.checkpoint.source_snapshot_digest != checkpoint.source_snapshot_digest
                or result.checkpoint.sequence < checkpoint.sequence
            ):
                raise RuntimeError("exact replay returned an incompatible or regressed stream checkpoint")
            return result
        if result.outcome is BackfillOutcome.CONFLICT:
            if result.reason_code in {
                "source_digest_changed",
                "canonical_identity_changed",
                "canonical_schema_version_changed",
                "source_identity_mismatch",
            }:
                if (
                    result.checkpoint != next_checkpoint
                    or not result.mapping_id
                    or not result.quarantine_id
                ):
                    raise RuntimeError("source mapping conflict must preserve first mapping and resume after audited quarantine")
                return result
            if result.reason_code not in {
                "checkpoint_lineage_changed",
                "checkpoint_compare_and_swap_conflict",
            }:
                raise RuntimeError("trusted transaction returned an unknown conflict class")
            if result.checkpoint != checkpoint:
                raise RuntimeError("checkpoint conflict must preserve the caller's checkpoint")
            return result
        if result.outcome is BackfillOutcome.QUARANTINED:
            if result.reason_code not in {
                "parent_cardinality_or_lifecycle_invalid",
                "duplicate_or_ambiguous_scope",
                "parent_compatibility_unproven",
                "source_row_deleted",
                "source_row_archived",
                "source_row_quarantined",
                "source_row_lifecycle_unknown",
                "unresolved_typed_scope",
                "plugin_instance_deferred_m05",
                "cross_tenant_assertion",
                "cross_tenant_source_assertion",
                "cabinet_source_assertion_mismatch",
                "project_source_assertion_mismatch",
                "source_id_exceeds_typed_provenance",
            }:
                raise RuntimeError("trusted transaction returned an unknown quarantine class")
            if result.checkpoint != next_checkpoint:
                raise RuntimeError("quarantined source mapping did not advance the resumable checkpoint")
            return result
        if result.outcome is BackfillOutcome.BLOCKED:
            if result.reason_code not in {
                "missing_trusted_transaction_context_or_port",
                "scope_context_mismatch",
                "checkpoint_tenant_mismatch",
                "source_schema_version_mismatch",
                "canonical_schema_version_mismatch",
                "append_only_audit_unavailable",
            }:
                raise RuntimeError("trusted transaction returned an unknown blocked class")
            if result.checkpoint != checkpoint:
                raise RuntimeError("blocked scope mapping changed the source checkpoint")
            return result
        if result.outcome in {BackfillOutcome.QUARANTINED, BackfillOutcome.DEFERRED}:
            if result.checkpoint != next_checkpoint:
                raise RuntimeError("quarantined/deferred source row did not commit its resumable checkpoint")
            return result
        if result.checkpoint != next_checkpoint:
            raise RuntimeError("trusted transaction did not persist the requested resumable checkpoint")
        return result

    @staticmethod
    def _blocked(
        candidate: ScopeCandidate,
        checkpoint: BackfillCheckpoint,
        correlation_id: str,
        reason: str,
    ) -> ScopeMigrationResult:
        return ScopeMigrationResult(
            outcome=BackfillOutcome.BLOCKED,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_schema_version=candidate.canonical_schema_version,
            canonical_digest=None,
            checkpoint=checkpoint,
            reason_code=reason,
            scope_kind=candidate.anchors.scope_kind if candidate.anchors else None,
            scope_id=candidate.anchors.scope_id if candidate.anchors else None,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _canonical_identity(
        anchors: ScopeAnchors,
    ) -> tuple[str, str, str, str | None, str | None, str | None]:
        return (
            anchors.scope_kind.value,
            anchors.scope_id,
            anchors.tenant_id,
            anchors.cabinet_id,
            anchors.project_id,
            anchors.plugin_instance_id,
        )

    @classmethod
    def _canonical_digest(
        cls, entry: ScopeRegistryEntry, canonical_schema_version: str
    ) -> str:
        encoded = json.dumps(
            {
                "canonical_schema_version": canonical_schema_version,
                "identity": cls._canonical_identity(entry.anchors),
                "owner_state": entry.owner_state.value,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _quarantine(
        candidate: ScopeCandidate, digest: str, reason: str
    ) -> QuarantineRecord | ScopeSourceQuarantineEvidence:
        if len(candidate.source_id) > 40:
            return ScopeSourceQuarantineEvidence(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                canonical_schema_version=candidate.canonical_schema_version,
                source_digest=digest,
                reason_code=reason,
            )
        anchors = candidate.anchors
        return QuarantineRecord(
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            payload_digest=digest,
            reason_code=reason,
            tenant_id=anchors.tenant_id if anchors else None,
            scope_kind=anchors.scope_kind if anchors else None,
            scope_id=anchors.scope_id if anchors else None,
        )

    @staticmethod
    def _proposed_outcome(
        candidate: ScopeCandidate,
        context: MigrationEvidenceContext,
        source_digest: str,
    ) -> tuple[
        str | None,
        ScopeRegistryEntry | None,
        QuarantineRecord | ScopeSourceQuarantineEvidence | None,
    ]:
        anchors = candidate.anchors
        reason: str | None = None
        if candidate.source_state is SourceRowState.DELETED:
            reason = "source_row_deleted"
        elif candidate.source_state is SourceRowState.ARCHIVED:
            reason = "source_row_archived"
        elif candidate.source_state is SourceRowState.QUARANTINED:
            reason = "source_row_quarantined"
        elif candidate.source_state is SourceRowState.UNKNOWN:
            reason = "source_row_lifecycle_unknown"
        if reason is None:
            if anchors is None:
                reason = "unresolved_typed_scope"
            elif anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
                reason = "plugin_instance_deferred_m05"
            elif anchors.tenant_id != context.tenant_id:
                reason = "cross_tenant_assertion"
            elif candidate.asserted_tenant_id is not None and candidate.asserted_tenant_id != anchors.tenant_id:
                reason = "cross_tenant_source_assertion"
            elif candidate.asserted_cabinet_id is not None and candidate.asserted_cabinet_id != anchors.cabinet_id:
                reason = "cabinet_source_assertion_mismatch"
            elif candidate.asserted_project_id is not None and candidate.asserted_project_id != anchors.project_id:
                reason = "project_source_assertion_mismatch"

        if reason is not None:
            if len(candidate.source_id) > 40:
                return reason, None, ScopeSourceQuarantineEvidence(
                    source_kind=candidate.source_kind,
                    source_id=candidate.source_id,
                    schema_version=candidate.schema_version,
                    canonical_schema_version=candidate.canonical_schema_version,
                    source_digest=source_digest,
                    reason_code=reason,
                )
            return reason, None, ScopeMigrationCoordinator._quarantine(candidate, source_digest, reason)
        if len(candidate.source_id) > 40:
            reason = "source_id_exceeds_typed_provenance"
            return reason, None, ScopeSourceQuarantineEvidence(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                canonical_schema_version=candidate.canonical_schema_version,
                source_digest=source_digest,
                reason_code=reason,
            )
        entry = ScopeRegistryEntry(
            anchors=anchors,
            owner_state=OwnerState.QUARANTINED,
            provenance=ScopeProvenance(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                payload_digest=source_digest,
            ),
        )
        return None, entry, None


class ScopeResolver:
    """Active-only resolver; missing RLS or transaction proof always denies."""

    def __init__(self, port: ResolveScopePort | None) -> None:
        self._port = port

    async def resolve(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> ScopeResolutionResult:
        correlation_id = context.correlation_id or "missing-correlation"
        if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "plugin_instance_denied_m05", correlation_id)
        if not context.usable or self._port is None:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "missing_trusted_transaction_context_or_port", correlation_id)
        if not context.usable:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "trusted_scope_context_unusable", correlation_id
            )
        if context.tenant_id != anchors.tenant_id:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "tenant_context_mismatch", correlation_id
            )
        if not context.matches_scope(anchors):
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "scope_context_mismatch", correlation_id
            )
        try:
            resolved = await self._port.resolve_active(context=context, anchors=anchors)
        except Exception:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "trusted_scope_resolution_failed", correlation_id
            )
        if resolved is None:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "scope_missing_or_not_unique_active", correlation_id)
        return evaluate_active_scope_evidence(
            context=context,
            anchors=anchors,
            evidence=resolved,
            correlation_id=correlation_id,
        )


def evaluate_active_scope_evidence(
    *,
    context: MigrationEvidenceContext,
    anchors: ScopeAnchors,
    evidence: ScopeResolutionEvidence | None,
    correlation_id: str | None = None,
) -> ScopeResolutionResult:
    """Revalidate an adapter's evidence bundle; no adapter can grant by assertion."""
    correlation_id = correlation_id or context.correlation_id or "missing-correlation"
    if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
        return ScopeResolutionResult(ScopeResolution.DENY, None, "plugin_instance_denied_m05", correlation_id)
    if not context.usable:
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "trusted_scope_context_unusable", correlation_id
        )
    if context.tenant_id != anchors.tenant_id:
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "tenant_context_mismatch", correlation_id
        )
    if not context.matches_scope(anchors):
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "scope_context_mismatch", correlation_id
        )
    if evidence is None:
        return ScopeResolutionResult(ScopeResolution.DENY, None, "scope_missing_or_not_unique_active", correlation_id)
    entry = evidence.entry
    if entry.owner_state is not OwnerState.ACTIVE or entry.anchors != anchors:
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "scope_identity_or_state_mismatch", correlation_id
        )
    parent_reason = evidence.parent_evidence.invalid_reason_for(anchors)
    if evidence.parent_compatibility_transaction_id != context.transaction_id:
        return ScopeResolutionResult(
            ScopeResolution.QUARANTINED,
            None,
            "parent_compatibility_transaction_mismatch",
            correlation_id,
        )
    if (
        not evidence.parent_proof.valid_for_scope(anchors.scope_kind)
        or parent_reason is not None
    ):
        return ScopeResolutionResult(
            ScopeResolution.QUARANTINED,
            None,
            parent_reason or "parent_compatibility_unproven",
            correlation_id,
        )
    if evidence.parent_evidence.transaction_id != context.transaction_id:
        return ScopeResolutionResult(
            ScopeResolution.QUARANTINED, None, "parent_evidence_transaction_mismatch", correlation_id
        )
    if not evidence.rls_evidence.valid_for(context=context, anchors=anchors):
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "rls_evidence_missing_or_invalid", correlation_id
        )
    if not evidence.actor_membership_evidence.valid_for(context=context, anchors=anchors):
        return ScopeResolutionResult(
            ScopeResolution.DENY, None, "active_tenant_membership_unproven", correlation_id
        )
    return ScopeResolutionResult(ScopeResolution.ACTIVE, entry, "active_typed_scope", correlation_id)


__all__ = [
    "BackfillCheckpoint",
    "BackfillOutcome",
    "MigrationEvidenceContext",
    "ExistingScopeMapping",
    "MappingReplayAssessment",
    "MappingReplayDecision",
    "ResolveScopePort",
    "ScopeCheckpointPort",
    "ScopeCheckpointResume",
    "ScopeCandidate",
    "ScopeActorMembershipEvidence",
    "ScopeMigrationCoordinator",
    "ScopeMigrationResult",
    "ScopeMappingInput",
    "ScopeParentEvidence",
    "ScopeResolution",
    "ScopeResolutionEvidence",
    "ScopeResolutionResult",
    "ScopeRlsEvidence",
    "ScopeResolver",
    "ScopeStreamIdentity",
    "ScopeSourceEvidence",
    "ScopeSourceQuarantineEvidence",
    "SourceRowState",
    "TrustedScopeTransactionPort",
    "assess_mapping_replay",
    "evaluate_active_scope_evidence",
]
