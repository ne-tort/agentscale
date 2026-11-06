"""Immutable contracts for selecting and materializing scoped resources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from prodavan.application.resources import ResourceScope
from prodavan.application.materialization_adapter import ResourceMaterializationRequest, ResourceOperation
from prodavan.domain.materialization import OperationKind, SourceProvenance


@dataclass(frozen=True, slots=True)
class ResourceSelection:
    """A fully explicit resource choice; no implicit latest/default is allowed."""

    resource_id: str
    binding_id: str
    scope: ResourceScope
    version_number: int
    target: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or not self.binding_id.strip():
            raise ValueError("resource_id and binding_id are required")
        if self.version_number < 1:
            raise ValueError("version_number must be positive")
        if self.target is not None and not self.target.strip():
            raise ValueError("target cannot be blank")


@dataclass(frozen=True, slots=True)
class ResourceMaterializationContext:
    project_id: str
    revision_id: str
    target_scope: ResourceScope
    desired_generation: int
    selections: tuple[ResourceSelection, ...] = ()
    applied_generation: int | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.revision_id.strip():
            raise ValueError("project_id and revision_id are required")
        if self.desired_generation < 0:
            raise ValueError("desired_generation must be non-negative")
        if self.applied_generation is not None and not 0 <= self.applied_generation <= self.desired_generation:
            raise ValueError("applied_generation must be within desired generation")
        object.__setattr__(self, "selections", tuple(self.selections))


@dataclass(frozen=True, slots=True)
class ResolutionFailure:
    code: str
    message: str
    resource_id: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("failure code and message are required")


class MaterializationApplyOutcome(StrEnum):
    APPLIED = "applied"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


def _snapshot_digest(context: ResourceMaterializationContext) -> str:
    payload = {
        "project_id": context.project_id,
        "revision_id": context.revision_id,
        "target_scope": {"kind": context.target_scope.kind.value, "id": context.target_scope.id},
        "desired_generation": context.desired_generation,
        "selections": [
            {
                "resource_id": selection.resource_id,
                "binding_id": selection.binding_id,
                "scope": {"kind": selection.scope.kind.value, "id": selection.scope.id},
                "version_number": selection.version_number,
                "target": selection.target,
                "required": selection.required,
            }
            for selection in context.selections
        ],
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ResourceMaterializationResolution:
    """Resolver output; operations are empty whenever a required failure exists."""

    context: ResourceMaterializationContext
    operations: tuple[ResourceOperation, ...] = ()
    failures: tuple[ResolutionFailure, ...] = ()
    source: SourceProvenance | None = None
    snapshot_digest: str = ""
    snapshot_generation: int | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "failures", tuple(self.failures))
        digest = _snapshot_digest(self.context)
        if self.snapshot_digest and self.snapshot_digest != digest:
            raise ValueError("selection snapshot digest mismatch")
        if self.snapshot_generation is not None and self.snapshot_generation != self.context.desired_generation:
            raise ValueError("selection snapshot generation mismatch")
        object.__setattr__(self, "snapshot_generation", self.context.desired_generation)
        object.__setattr__(self, "snapshot_digest", digest)
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def is_complete(self) -> bool:
        return not self.failures and bool(self.snapshot_digest)

    @property
    def operation_set_digest(self) -> str:
        payload = [
            {
                "kind": operation.kind.value,
                "target": operation.target,
                "payload": operation.payload,
                "required": operation.required,
            }
            for operation in self.operations
        ]
        return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def to_request(self) -> ResourceMaterializationRequest:
        """Adapt only a complete resolution; fail closed before adapter planning."""
        if self.failures:
            raise ValueError("cannot materialize an incomplete resource resolution")
        return ResourceMaterializationRequest(
            project_id=self.context.project_id,
            revision_id=self.context.revision_id,
            desired_generation=self.context.desired_generation,
            applied_generation=self.context.applied_generation,
            operations=self.operations,
            source=self.source,
            required_kinds=frozenset(operation.kind for operation in self.operations if operation.required),
        )


__all__ = [
    "ResourceMaterializationContext",
    "ResourceMaterializationResolution",
    "ResourceSelection",
    "ResolutionFailure",
]
