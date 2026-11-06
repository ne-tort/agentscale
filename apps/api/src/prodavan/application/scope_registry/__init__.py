"""M04 typed scope registry application boundary."""

from .contracts import (
    OwnerState,
    ParentCompatibilityProof,
    PositiveWriteDecision,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeAssertion,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from .service import ScopeRegistryService

__all__ = [
    "OwnerState",
    "ParentCompatibilityProof",
    "PositiveWriteDecision",
    "QuarantineRecord",
    "RlsContext",
    "ScopeAnchors",
    "ScopeAssertion",
    "ScopeKind",
    "ScopeProvenance",
    "ScopeRegistryEntry",
    "ScopeRegistryService",
]
