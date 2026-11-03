"""Ports for read-only M04 scope resolution in a trusted transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncContextManager, Protocol

from prodavan.application.migration.m04_scope.scope_migration import ScopeParentEvidence
from prodavan.application.scope_registry.contracts import (
    ParentCompatibilityProof,
    RlsContext,
    ScopeAnchors,
    ScopeRegistryEntry,
)


@dataclass(frozen=True, slots=True)
class ScopeRegistryGateEvidence:
    """Accepted upstream evidence for this exact tenant partition."""

    tenant_id: str
    m04_01_status: str
    m04_01_evidence_id: str | None
    m04_02_status: str
    m04_02_evidence_id: str | None
    source_snapshot_digest: str | None
    canonical_scope_digest: str | None
    checkpoint_id: str | None
    source_snapshot_id: str | None
    source_schema_version: str | None
    canonical_schema_version: str | None
    rls_review_evidence_id: str | None
    audit_review_evidence_id: str | None
    decision_record_id: str | None
    canonical_positive_writes: bool = False


@dataclass(frozen=True, slots=True)
class ScopeRegistryEvidenceIds:
    """Persistent IDs supporting a single exact registry and parent proof."""

    registry_id: str | None
    mapping_id: str | None
    audit_id: str | None
    tenant_anchor_id: str | None
    cabinet_anchor_id: str | None
    project_anchor_id: str | None
    cabinet_membership_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScopeRegistryRead:
    """Repository result; ambiguous rows must not be collapsed into one result."""

    transaction_id: str
    match_count: int
    entry: ScopeRegistryEntry | None
    parent_proof: ParentCompatibilityProof | None
    parent_evidence: ScopeParentEvidence | None
    evidence_ids: ScopeRegistryEvidenceIds
    mapping_source_kind: str | None = None
    mapping_source_id: str | None = None
    mapping_schema_version: str | None = None
    mapping_source_digest: str | None = None
    mapping_canonical_digest: str | None = None
    mapping_checkpoint_id: str | None = None
    mapping_canonical_schema_version: str | None = None
    source_snapshot_id: str | None = None
    source_snapshot_schema_version: str | None = None
    source_snapshot_digest: str | None = None
    partition_canonical_digest: str | None = None


@dataclass(frozen=True, slots=True)
class TransactionLocalRlsReceipt:
    transaction_id: str
    setting_scope: str
    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    user_id: str
    role: str


@dataclass(frozen=True, slots=True)
class TransactionLocalRlsClearReceipt:
    transaction_id: str
    setting_scope: str
    cleared: bool


class OwnerRegistryScopeRepositoryPort(Protocol):
    """Read M04-01 owner_registry models through a repository, never ORM here."""

    async def read_scope(
        self, *, anchors: ScopeAnchors, transaction_id: str
    ) -> ScopeRegistryRead:
        """Read exact registry, parent, membership and evidence rows in one UoW."""
        ...


class ScopeRegistryGateRepositoryPort(Protocol):
    async def read_gate(self, *, tenant_id: str) -> ScopeRegistryGateEvidence | None:
        """Read immutable accepted gate evidence from the current UoW."""
        ...


class ScopeRegistryTransactionPort(Protocol):
    @property
    def transaction_id(self) -> str: ...

    @property
    def owner_registry(self) -> OwnerRegistryScopeRepositoryPort: ...

    @property
    def migration_gates(self) -> ScopeRegistryGateRepositoryPort: ...

    async def set_transaction_local_rls_context(
        self, context: RlsContext
    ) -> TransactionLocalRlsReceipt:
        """Set server-resolved GUCs on this open transaction, not the session."""
        ...

    async def clear_transaction_local_rls_context(
        self,
    ) -> TransactionLocalRlsClearReceipt:
        """Clear GUCs before exit; transaction end must also reset them."""
        ...


class ScopeRegistryTransactionManagerPort(Protocol):
    def transaction(
        self, *, expected_transaction_id: str
    ) -> AsyncContextManager[ScopeRegistryTransactionPort]:
        """Open one transaction; an exception rolls it back and closes its context."""
        ...


__all__ = [
    "OwnerRegistryScopeRepositoryPort",
    "ScopeRegistryEvidenceIds",
    "ScopeRegistryGateEvidence",
    "ScopeRegistryRead",
    "ScopeRegistryTransactionManagerPort",
    "ScopeRegistryTransactionPort",
    "TransactionLocalRlsClearReceipt",
    "TransactionLocalRlsReceipt",
]
