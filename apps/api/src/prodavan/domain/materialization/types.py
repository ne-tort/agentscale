"""Immutable, persistence-neutral contracts for workspace materialization.

The plan is intentionally separate from the legacy project materializer.  A
planner can persist these entries and an apply worker can safely retry them
without depending on a particular workspace writer or database model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


class OperationKind(StrEnum):
    """Materialization surface represented by a plan entry."""

    PROMPT = "prompt"
    FILE = "file"
    MCP = "mcp"
    ENV = "env"
    SCOPE = "scope"


class MaterializationStatus(StrEnum):
    """Lifecycle of one planned operation."""

    PLANNED = "planned"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Where an operation came from, including the source generation."""

    source_type: str
    source_id: str
    source_generation: int
    source_path: str | None = None
    source_revision: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_type.strip() or not self.source_id.strip():
            raise ValueError("source_type and source_id are required")
        if self.source_generation < 0:
            raise ValueError("source_generation must be non-negative")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    """Structured, non-secret failure detail retained after an apply attempt."""

    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("failure code and message are required")
        object.__setattr__(self, "details", _freeze(self.details))


@dataclass(frozen=True, slots=True)
class MaterializationOperation:
    """Immutable desired operation payload within a plan entry."""

    kind: OperationKind
    target: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("operation target is required")
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True, slots=True)
class MaterializationPlanEntry:
    """Persistable plan row with generation and idempotency state.

    ``desired_generation`` identifies the source state the planner observed;
    ``applied_generation`` is the last generation successfully applied.  An
    entry is idempotently applicable when its digest and idempotency key match
    the stored result for the same target.
    """

    entry_id: str
    project_id: str
    operation: MaterializationOperation
    provenance: SourceProvenance
    digest: str
    idempotency_key: str
    desired_generation: int
    applied_generation: int | None = None
    status: MaterializationStatus = MaterializationStatus.PLANNED
    failure: FailureDiagnostic | None = None

    def __post_init__(self) -> None:
        if not self.entry_id.strip() or not self.project_id.strip():
            raise ValueError("entry_id and project_id are required")
        if not self.digest.strip() or not self.idempotency_key.strip():
            raise ValueError("digest and idempotency_key are required")
        if self.desired_generation < 0:
            raise ValueError("desired_generation must be non-negative")
        if self.applied_generation is not None and self.applied_generation < 0:
            raise ValueError("applied_generation must be non-negative")
        if self.applied_generation is not None and self.applied_generation > self.desired_generation:
            raise ValueError("applied_generation cannot exceed desired_generation")
        if self.status is MaterializationStatus.FAILED and self.failure is None:
            raise ValueError("failed entry requires failure diagnostics")
        if self.status is not MaterializationStatus.FAILED and self.failure is not None:
            raise ValueError("failure diagnostics are only valid for failed entries")

    @property
    def is_idempotently_applied(self) -> bool:
        return (
            self.status is MaterializationStatus.APPLIED
            and self.applied_generation == self.desired_generation
        )
