"""MIG-M06-02 evidence-only adapter and shadow parity contracts."""

from .adapter import (
    AdapterOutcome,
    AuthenticatedGrantContext,
    CapabilityGatewayService,
    DependencyGates,
    EvidenceReadPort,
    EvidenceTransaction,
    TrustedEvidenceTransactionPort,
    GrantSetAdapter,
    LegacyGrantSource,
    MappingOutcome,
    ResolvedGrantIdentity,
    ShadowDecision,
    ShadowObservation,
    SourceMappingRecord,
)

__all__ = [
    "AdapterOutcome", "AuthenticatedGrantContext", "CapabilityGatewayService",
    "DependencyGates", "EvidenceReadPort", "EvidenceTransaction", "TrustedEvidenceTransactionPort",
    "GrantSetAdapter", "LegacyGrantSource", "MappingOutcome",
    "ResolvedGrantIdentity", "ShadowDecision", "ShadowObservation", "SourceMappingRecord",
]
