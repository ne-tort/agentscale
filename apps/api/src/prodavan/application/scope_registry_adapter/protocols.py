"""Trusted repository protocols for the M04 scope-registry adapter.

The protocols deliberately expose typed M04 contracts rather than ORM models.  A
persistence implementation may use companies/cabinets/projects as its backing
relations, but it must return one coherent set of anchors.
"""

from __future__ import annotations

from typing import Protocol

from prodavan.application.scope_registry.contracts import (
    ParentCompatibilityProof,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeRegistryEntry,
)


class ScopeRegistryReadRepository(Protocol):
    """Trusted active-or-evidence read for one exact typed anchor tuple."""

    async def get_scope(self, anchors: ScopeAnchors) -> ScopeRegistryEntry | None: ...


class ScopeRegistryWriteRepository(Protocol):
    """Trusted persistence boundary for a validated active registry row."""

    async def put_scope(self, entry: ScopeRegistryEntry) -> ScopeRegistryEntry: ...


class ScopeParentProofRepository(Protocol):
    """Server-side company/cabinet/project compatibility evidence."""

    async def prove(self, anchors: ScopeAnchors) -> ParentCompatibilityProof: ...


class ScopeQuarantineRepository(Protocol):
    """Append-only quarantine sink; quarantined rows never authorize writes."""

    async def record(self, record: QuarantineRecord) -> None: ...


class ScopeRlsRepository(Protocol):
    """Transaction-local RLS context boundary populated from trusted auth state."""

    async def set_transaction_context(self, context: RlsContext) -> None: ...

    async def clear_transaction_context(self) -> None: ...


class TrustedScopeRegistryRepository(
    ScopeRegistryReadRepository,
    ScopeRegistryWriteRepository,
    ScopeParentProofRepository,
    ScopeQuarantineRepository,
    ScopeRlsRepository,
    Protocol,
):
    """Complete repository required by ``ScopeRegistryService``.

    This is an application-facing composition protocol only.  It does not imply
    a concrete table layout or grant ORM access to the adapter.
    """


__all__ = [
    "ScopeParentProofRepository",
    "ScopeQuarantineRepository",
    "ScopeRegistryReadRepository",
    "ScopeRegistryWriteRepository",
    "ScopeRlsRepository",
    "TrustedScopeRegistryRepository",
]
