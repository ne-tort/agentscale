"""M04 exclusive application adapter zone for trusted scope persistence."""

from .protocols import (
    ScopeParentProofRepository,
    ScopeQuarantineRepository,
    ScopeRegistryReadRepository,
    ScopeRegistryWriteRepository,
    ScopeRlsRepository,
    TrustedScopeRegistryRepository,
)
from .service_adapter import ScopeRegistryServiceAdapter

__all__ = [
    "ScopeParentProofRepository",
    "ScopeQuarantineRepository",
    "ScopeRegistryReadRepository",
    "ScopeRegistryServiceAdapter",
    "ScopeRegistryWriteRepository",
    "ScopeRlsRepository",
    "TrustedScopeRegistryRepository",
]
