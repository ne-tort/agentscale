"""Bounded M04 scope evidence migration with mandatory trusted persistence ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class ScopeResolution(StrEnum):
    ACTIVE = "active"
    DENY = "deny"
    DEFERRED = "deferred"
    QUARANTINED = "quarantined"


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

    @property
    def usable(self) -> bool:
        return bool(
            self.authenticated
            and self.tenant_id
            and self.transaction_id
            and self.correlation_id
            and self.user_id
            and self.role
            and self.rls_context
            and self.rls_context.tenant_id == self.tenant_id
        )


@dataclass(frozen=True, slots=True)
class ScopeCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: dict[str, object]
    anchors: ScopeAnchors | None
    # Source fields are assertions to compare with trusted parent resolution.
    asserted_tenant_id: str | None = None
    asserted_cabinet_id: str | None = None
    asserted_project_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_kind.strip() or not self.source_id.strip() or not self.schema_version.strip():
            raise ValueError("source kind, ID and schema version are required")
        if len(self.source_kind) > 80 or len(self.source_id) > 40 or len(self.schema_version) > 40:
            raise ValueError("source identity exceeds the evidence contract")

    @property
    def source_digest(self) -> str:
        encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    source_snapshot_id: str
    source_schema_version: str
    after_source_id: str | None
    sequence: int
    source_snapshot_digest: str

    def __post_init__(self) -> None:
        if not self.source_snapshot_id.strip() or not self.source_schema_version.strip():
            raise ValueError("snapshot identity and schema version are required")
        if self.sequence < 0 or not self.source_snapshot_digest.startswith("sha256:"):
            raise ValueError("invalid checkpoint sequence or snapshot digest")


@dataclass(frozen=True, slots=True)
class ScopeMigrationResult:
    outcome: BackfillOutcome
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    canonical_digest: str | None
    checkpoint: BackfillCheckpoint
    reason_code: str | None
    scope_kind: ScopeKind | None
    scope_id: str | None
    correlation_id: str
    writer_generation: int | None = None
    parent_compatibility_proof: ParentCompatibilityProof | None = None
    canonical_identity: tuple[str, str, str, str | None, str | None, str | None] | None = None
    canonical_positive_writes: bool = False


@dataclass(frozen=True, slots=True)
class ScopeResolutionResult:
    outcome: ScopeResolution
    entry: ScopeRegistryEntry | None
    reason_code: str
    correlation_id: str


class TrustedScopeTransactionPort(Protocol):
    """Must implement one DB transaction including tenant GUC, proofs and evidence."""

    async def classify_and_append(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeCandidate,
        checkpoint: BackfillCheckpoint,
        source_digest: str,
        proposed_entry: ScopeRegistryEntry | None,
        proposed_quarantine: QuarantineRecord | None,
        reason_code: str | None,
    ) -> ScopeMigrationResult:
        """Resolve proof, classify mapping and append all evidence in one transaction.

        The implementation establishes transaction-local GUCs from authenticated
        context, re-derives tenant/cabinet/project and active membership proof inside
        this transaction, compares source digest and typed canonical identity before
        writes, preserves the first mapping on conflict, and never overwrites an
        existing registry identity. Exact digest+identity replay returns the original
        mapping. For a new source identity it atomically appends mapping/quarantine/
        audit/checkpoint. It returns the proof used by that same transaction; no
        positive grant/instance write is permitted.
        """
        ...

    async def resolve_active(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> tuple[ScopeRegistryEntry, ParentCompatibilityProof] | None:
        """Read one row and prove parents in one trusted transaction.

        Must set/verify transaction-local RLS context, query active state only, enforce
        exact typed anchors and return None for zero or multiple active matches.
        """
        ...


class ScopeMigrationCoordinator:
    """Process exactly one source row per call; no local fallback persistence exists."""

    def __init__(self, port: TrustedScopeTransactionPort | None) -> None:
        self._port = port

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
                canonical_digest=None,
                checkpoint=checkpoint,
                reason_code="missing_trusted_transaction_context_or_port",
                scope_kind=candidate.anchors.scope_kind if candidate.anchors else None,
                scope_id=candidate.anchors.scope_id if candidate.anchors else None,
                correlation_id=correlation_id,
            )

        next_checkpoint = BackfillCheckpoint(
            source_snapshot_id=checkpoint.source_snapshot_id,
            source_schema_version=checkpoint.source_schema_version,
            after_source_id=candidate.source_id,
            sequence=checkpoint.sequence + 1,
            source_snapshot_digest=checkpoint.source_snapshot_digest,
        )
        reason, entry, quarantine = self._proposed_outcome(candidate, context, digest)
        result = await self._port.classify_and_append(
            context=context,
            candidate=candidate,
            checkpoint=next_checkpoint,
            source_digest=digest,
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
        if result.canonical_positive_writes:
            raise RuntimeError("M04 scope evidence cannot enable canonical positive writes")
        expected_identity = self._canonical_identity(entry.anchors) if entry is not None else None
        if result.outcome is BackfillOutcome.ACCEPTED and (
            reason is not None
            or entry is None
            or entry.owner_state is not OwnerState.ACTIVE
            or result.parent_compatibility_proof is None
            or not result.parent_compatibility_proof.valid_for_scope(entry.anchors.scope_kind)
            or result.canonical_identity != expected_identity
        ):
            raise RuntimeError("persistence port accepted a row without transaction-bound active identity proof")
        if candidate.anchors and candidate.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            if result.outcome is BackfillOutcome.ACCEPTED:
                raise RuntimeError("plugin-instance scope must remain deferred until M05")
        if result.outcome is not BackfillOutcome.REPLAY and result.checkpoint != next_checkpoint:
            raise RuntimeError("trusted transaction did not persist the requested resumable checkpoint")
        return result

    @staticmethod
    def _canonical_identity(
        anchors: ScopeAnchors,
    ) -> tuple[str, str, str, str | None, str | None, str | None]:
        return (
            anchors.scope_kind.value, anchors.scope_id, anchors.tenant_id,
            anchors.cabinet_id, anchors.project_id, anchors.plugin_instance_id,
        )

    @staticmethod
    def _proof_valid(anchors: ScopeAnchors, proof: ParentCompatibilityProof) -> bool:
        return proof.valid_for_scope(anchors.scope_kind)

    @staticmethod
    def _quarantine(candidate: ScopeCandidate, digest: str, reason: str) -> QuarantineRecord:
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
    ) -> tuple[str | None, ScopeRegistryEntry | None, QuarantineRecord | None]:
        anchors = candidate.anchors
        reason: str | None = None
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
        # Parent compatibility is deliberately not inferred here; the transaction port
        # must prove it before committing the proposed mapping.
        if reason is not None:
            quarantine = QuarantineRecord(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                payload_digest=source_digest,
                reason_code=reason,
                tenant_id=anchors.tenant_id if anchors else context.tenant_id,
                scope_kind=anchors.scope_kind if anchors else None,
                scope_id=anchors.scope_id if anchors else None,
            )
            return reason, None, quarantine
        entry = ScopeRegistryEntry(
            anchors=anchors,
            owner_state=OwnerState.ACTIVE,
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

    def __init__(self, port: TrustedScopeTransactionPort | None) -> None:
        self._port = port

    async def resolve(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> ScopeResolutionResult:
        correlation_id = context.correlation_id or "missing-correlation"
        if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            return ScopeResolutionResult(ScopeResolution.DEFERRED, None, "plugin_instance_deferred_m05", correlation_id)
        if not context.usable or self._port is None:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "missing_trusted_transaction_context_or_port", correlation_id)
        expected_rls = context.rls_context
        if (
            context.tenant_id != anchors.tenant_id
            or expected_rls is None
            or expected_rls.tenant_id != anchors.tenant_id
            or expected_rls.cabinet_id != anchors.cabinet_id
            or expected_rls.project_id != anchors.project_id
        ):
            return ScopeResolutionResult(ScopeResolution.DENY, None, "scope_context_mismatch", correlation_id)
        resolved = await self._port.resolve_active(context=context, anchors=anchors)
        if resolved is None:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "scope_missing_or_not_unique_active", correlation_id)
        entry, proof = resolved
        if entry.owner_state is not OwnerState.ACTIVE or entry.anchors != anchors:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "scope_identity_or_state_mismatch", correlation_id)
        if not proof.valid_for_scope(anchors.scope_kind):
            return ScopeResolutionResult(ScopeResolution.QUARANTINED, None, "parent_compatibility_unproven", correlation_id)
        return ScopeResolutionResult(ScopeResolution.ACTIVE, entry, "active_typed_scope", correlation_id)


__all__ = [
    "BackfillCheckpoint",
    "BackfillOutcome",
    "MigrationEvidenceContext",
    "ScopeCandidate",
    "ScopeMigrationCoordinator",
    "ScopeMigrationResult",
    "ScopeResolution",
    "ScopeResolutionResult",
    "ScopeResolver",
    "TrustedScopeTransactionPort",
]
