"""Immutable, persistence-neutral evidence handoff contracts for resources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from prodavan.application.resources import ResourceScope


class HandoffFailureCode(StrEnum):
    MISSING_RUNTIME_REVISION = "missing_runtime_revision"
    MISSING_EVIDENCE = "missing_evidence"
    REVISION_MISMATCH = "revision_mismatch"
    GENERATION_STALE = "generation_stale"
    OWNER_MISMATCH = "owner_mismatch"
    GRANT_MISMATCH = "grant_mismatch"
    PARTITION_MISMATCH = "partition_mismatch"
    PROVENANCE_MISSING = "provenance_missing"
    CAS_CONFLICT = "cas_conflict"


@dataclass(frozen=True, slots=True)
class PromotionPartition:
    """The exclusive ownership boundary for a promotion/apply operation."""

    tenant_id: str
    project_id: str
    partition_id: str
    owner_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.tenant_id, self.project_id, self.partition_id, self.owner_id)):
            raise ValueError("promotion partition identity is required")


@dataclass(frozen=True, slots=True)
class RuntimeRevisionEvidence:
    """Server-observed runtime revision and generation, never client-supplied."""

    project_id: str
    revision_id: str
    generation: int
    evidence_id: str
    provenance_digest: str
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.project_id, self.revision_id, self.evidence_id, self.provenance_digest)):
            raise ValueError("runtime revision evidence fields are required")
        if self.generation < 0:
            raise ValueError("runtime evidence generation must be non-negative")


@dataclass(frozen=True, slots=True)
class ResourceGrantEvidence:
    """Explicit owner/grant proof for one resource handoff."""

    resource_id: str
    owner: ResourceScope
    grant_id: str
    grant_generation: int

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or not self.grant_id.strip():
            raise ValueError("resource and grant identity are required")
        if self.grant_generation < 0:
            raise ValueError("grant_generation must be non-negative")


@dataclass(frozen=True, slots=True)
class ResourceProvenance:
    """Evidence linking the handoff to the exact source revision and operation."""

    resource_id: str
    resource_revision: str
    source_kind: str
    source_digest: str
    evidence_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.resource_id, self.resource_revision, self.source_kind, self.source_digest, self.evidence_id)):
            raise ValueError("resource provenance fields are required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ResourceEvidenceHandoff:
    """Complete apply input; incomplete handoffs are not eligible for apply."""

    project_id: str
    runtime: RuntimeRevisionEvidence
    partition: PromotionPartition
    grant: ResourceGrantEvidence
    provenance: tuple[ResourceProvenance, ...]
    expected_generation: int
    operation_digest: str

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.operation_digest.strip():
            raise ValueError("handoff identity is required")
        if self.expected_generation < 0:
            raise ValueError("expected_generation must be non-negative")
        if self.runtime.project_id != self.project_id or self.partition.project_id != self.project_id:
            raise ValueError("handoff project does not match runtime or partition")
        object.__setattr__(self, "provenance", tuple(self.provenance))


@dataclass(frozen=True, slots=True)
class HandoffFailure:
    code: HandoffFailureCode
    message: str
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceApplyResult:
    applied: bool
    cas_generation: int | None = None
    failures: tuple[HandoffFailure, ...] = ()
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(self.failures))
        if self.applied and self.failures:
            raise ValueError("an applied result cannot contain failures")

    @property
    def is_fail_closed(self) -> bool:
        return not self.applied


__all__ = [
    "HandoffFailure",
    "HandoffFailureCode",
    "PromotionPartition",
    "ResourceApplyResult",
    "ResourceEvidenceHandoff",
    "ResourceGrantEvidence",
    "ResourceProvenance",
    "RuntimeRevisionEvidence",
]
