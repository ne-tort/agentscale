"""Immutable contracts for the resource/materialization plan adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from prodavan.application.runtime_revision import MaterializationEntry
from prodavan.domain.materialization import OperationKind, SourceProvenance


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ResourceOperation:
    """A resource operation supplied by a resource/materialization planner."""

    kind: OperationKind | str
    target: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    provenance: SourceProvenance | None = None
    required: bool = True

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, OperationKind) else OperationKind(str(self.kind))
        target = self.target.strip()
        if not target:
            raise ValueError("operation target is required")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True, slots=True)
class ResourceMaterializationRequest:
    """Input snapshot for building one immutable project materialization plan."""

    project_id: str
    revision_id: str
    desired_generation: int
    operations: tuple[ResourceOperation, ...] = ()
    source: SourceProvenance | None = None
    required_kinds: frozenset[OperationKind] = frozenset()
    applied_generation: int | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.revision_id.strip():
            raise ValueError("project_id and revision_id are required")
        if self.desired_generation < 0:
            raise ValueError("desired_generation must be non-negative")
        if self.applied_generation is not None and self.applied_generation < 0:
            raise ValueError("applied_generation must be non-negative")
        if self.applied_generation is not None and self.applied_generation > self.desired_generation:
            raise ValueError("applied_generation cannot exceed desired_generation")
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "required_kinds", frozenset(self.required_kinds))


@dataclass(frozen=True, slots=True)
class ResourceMaterializationPlan:
    """Immutable adapter output, ready for runtime revision persistence."""

    project_id: str
    revision_id: str
    desired_generation: int
    applied_generation: int | None
    entries: tuple[MaterializationEntry, ...]
    digest: str
    idempotency_key: str
    required_operations: tuple[str, ...] = ()
    missing_required: tuple[OperationKind, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.digest.strip() or not self.idempotency_key.strip():
            raise ValueError("plan digest and idempotency key are required")
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "required_operations", tuple(self.required_operations))
        object.__setattr__(self, "missing_required", tuple(self.missing_required))
        object.__setattr__(self, "diagnostics", _freeze(self.diagnostics))

    @property
    def is_complete(self) -> bool:
        return not self.missing_required and not self.diagnostics.get("duplicate_targets")

    @property
    def desired_applied_generation(self) -> tuple[int, int | None]:
        return self.desired_generation, self.applied_generation
