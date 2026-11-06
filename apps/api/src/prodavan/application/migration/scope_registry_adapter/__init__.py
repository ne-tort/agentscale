"""Scope adapter package public API."""

from .adapter import ScopeAdapterOutcome, ScopeAdapterResult, ScopeRegistryApplicationAdapter
from .ports import ScopeRegistryEvidenceIds, ScopeRegistryGateEvidence, ScopeRegistryRead

__all__ = [
    "ScopeAdapterOutcome",
    "ScopeAdapterResult",
    "ScopeRegistryApplicationAdapter",
    "ScopeRegistryEvidenceIds",
    "ScopeRegistryGateEvidence",
    "ScopeRegistryRead",
]
