"""MIG-M06-02 evidence-only adapter and shadow parity contracts."""

from .adapter import (
    APPROVED_CAPABILITIES,
    OPERATION_MAP_VERSION,
    AdapterOutcome,
    AuthenticatedGrantContext,
    CapabilityGatewayService,
    DependencyGates,
    EvidenceReadPort,
    EvidenceTransaction,
    TrustedEvidenceTransactionPort,
    CAPABILITY_OPERATIONS,
    GrantSetAdapter,
    LegacyGrantSource,
    MappingOutcome,
    ResolvedGrantIdentity,
    ShadowDecision,
    ShadowObservation,
    SourceMappingRecord,
)
from .canonical_decision_adapter import CanonicalGrantShadowAdapter
from .identity_adapter import ApplicationIdentityResolutionAdapter
from .legacy_decision_adapter import LegacyPluginGrantDecisionAdapter

__all__ = [
    "AdapterOutcome", "AuthenticatedGrantContext", "CapabilityGatewayService",
    "APPROVED_CAPABILITIES", "OPERATION_MAP_VERSION", "CAPABILITY_OPERATIONS",
    "DependencyGates", "EvidenceReadPort", "EvidenceTransaction", "TrustedEvidenceTransactionPort",
    "ApplicationIdentityResolutionAdapter", "CanonicalGrantShadowAdapter",
    "GrantSetAdapter", "LegacyGrantSource", "MappingOutcome",
    "LegacyPluginGrantDecisionAdapter",
    "ResolvedGrantIdentity", "ShadowDecision", "ShadowObservation", "SourceMappingRecord",
]
