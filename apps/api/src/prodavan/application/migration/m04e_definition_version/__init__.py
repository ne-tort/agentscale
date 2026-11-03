"""MIG-M04E-02 evidence backfill and M05 handoff contracts.

These contracts intentionally remain evidence-only. They cannot enable definition,
instance, or grant writes and fail closed until M04/M04E-01 gates are evidenced.
"""

from .backfill import (
    BackfillContext,
    BackfillBatch,
    BackfillCheckpoint,
    BackfillOutcome,
    BackfillRecord,
    CandidateProof,
    DefinitionVersionEvidenceBackfill,
    EvidenceCandidate,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus,
    M05EvidenceExport,
    M05HandoffMarker,
    M05HandoffPort,
    PromotionRecord,
    UpstreamGateEvidence,
    export_m05_evidence,
    persist_m05_handoff,
)
from .repository import (
    ActiveTypedScopeProof,
    ArtifactContentPort,
    DefinitionVersionProof,
    MigrationEvidenceContext as ResolverEvidenceContext,
    ServerDefinitionVersionRepository,
)
from .readiness import UPSTREAM_BLOCKERS, upstream_blockers

__all__ = [
    "BackfillBatch",
    "BackfillContext",
    "BackfillCheckpoint",
    "BackfillOutcome",
    "BackfillRecord",
    "CandidateProof",
    "DefinitionVersionEvidenceBackfill",
    "EvidenceCandidate",
    "EvidenceStatus",
    "HandoffGate",
    "HandoffStatus",
    "M05EvidenceExport",
    "M05HandoffMarker",
    "M05HandoffPort",
    "PromotionRecord",
    "UpstreamGateEvidence",
    "ActiveTypedScopeProof",
    "ArtifactContentPort",
    "DefinitionVersionProof",
    "ResolverEvidenceContext",
    "ServerDefinitionVersionRepository",
    "UPSTREAM_BLOCKERS",
    "upstream_blockers",
    "export_m05_evidence",
    "persist_m05_handoff",
]
