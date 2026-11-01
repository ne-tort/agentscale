"""Exclusive, read/decision-only versioned capability mapping zone."""

from .contracts import (
    CapabilityMethodMapping,
    MappingAuditMetadata,
    MappingDecision,
    MappingReason,
    MappingResult,
    MethodIdentity,
    ResourceDomainConstraint,
    mapping_digest,
)
from .service import CapabilityMethodMappingService

__all__ = [
    "CapabilityMethodMapping",
    "CapabilityMethodMappingService",
    "MappingAuditMetadata",
    "MappingDecision",
    "MappingReason",
    "MappingResult",
    "MethodIdentity",
    "ResourceDomainConstraint",
    "mapping_digest",
]
