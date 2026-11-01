"""M06 exclusive persistence boundary for trusted evidence and quarantine.

This package intentionally contains no ORM, schema, route, or positive-write
implementation.  It is the future transaction seam for immutable evidence.
"""

from .adapter import CanonicalGrantsPersistenceAdapter
from .contracts import McpAuditCorrelation
from .ports import EvidenceAppendTransaction, TrustedEvidenceRepository

__all__ = [
    "CanonicalGrantsPersistenceAdapter",
    "EvidenceAppendTransaction",
    "McpAuditCorrelation",
    "TrustedEvidenceRepository",
]
