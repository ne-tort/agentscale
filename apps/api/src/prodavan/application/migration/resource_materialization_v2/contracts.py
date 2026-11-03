"""Immutable contracts for deterministic, read-only resource plan building."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from prodavan.application.migration.resources import ResourceKind, ResourceScope, ResourceSnapshot, ScopeKind
from prodavan.application.migration.resources.lifecycle_contracts import freeze_json

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_GATES = frozenset({"MIG-M04-01", "MIG-M04E-01", "MIG-RES-01"})


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


@dataclass(frozen=True, slots=True)
class ResourcePlanSelection:
    """Explicit resource, binding, immutable version, profile and optional target."""

    resource_id: str
    binding_id: str
    version_id: str
    profile_id: str
    target_path: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        for name in ("resource_id", "binding_id", "version_id"):
            _required(getattr(self, name), name)
        _required(self.profile_id, "profile_id")
        if self.target_path is not None:
            _required(self.target_path, "target_path")


@dataclass(frozen=True, slots=True)
class ResourcePlanRequest:
    tenant_id: str
    subject_id: str
    project_id: str
    revision_id: str
    target_scope: ResourceScope
    desired_generation: int
    selections: tuple[ResourcePlanSelection, ...]

    def __post_init__(self) -> None:
        for name in ("tenant_id", "subject_id", "project_id", "revision_id"):
            _required(getattr(self, name), name)
        if self.desired_generation < 1:
            raise ValueError("desired_generation must be positive")
        if self.target_scope.kind is not ScopeKind.PROJECT:
            raise ValueError("target scope kind must be project")
        if self.target_scope.tenant_id != self.tenant_id or self.target_scope.scope_id != self.project_id:
            raise ValueError("target scope must be the requested tenant project")
        object.__setattr__(self, "selections", tuple(self.selections))


@dataclass(frozen=True, slots=True)
class UpstreamEvidence:
    """Trusted reference proving one upstream migration gate for this revision."""

    gate_id: str
    evidence_id: str
    evidence_digest: str
    tenant_id: str
    project_id: str
    revision_id: str
    generation: int
    status: str = "verified"

    def __post_init__(self) -> None:
        for name in ("gate_id", "evidence_id", "tenant_id", "project_id", "revision_id"):
            _required(getattr(self, name), name)
        if not _SHA256.fullmatch(self.evidence_digest):
            raise ValueError("evidence_digest must be sha256")
        if self.generation < 1:
            raise ValueError("evidence generation must be positive")


@dataclass(frozen=True, slots=True)
class FileRefAclEvidence:
    """Reference to a trusted ACL decision; contains no file bytes or credentials."""

    evidence_id: str
    evidence_digest: str
    tenant_id: str
    project_id: str
    subject_id: str
    resource_id: str
    version_id: str
    asset_id: str
    asset_version_id: str
    generation: int
    allowed: bool

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "tenant_id", "project_id", "subject_id", "resource_id",
            "version_id", "asset_id", "asset_version_id",
        ):
            _required(getattr(self, name), name)
        if not _SHA256.fullmatch(self.evidence_digest):
            raise ValueError("evidence_digest must be sha256")
        if self.generation < 1:
            raise ValueError("ACL evidence generation must be positive")


@dataclass(frozen=True, slots=True)
class ResourceOperationReadiness:
    operation_id: str
    ready: bool
    reason_code: str = "ready"

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")
        _required(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class PlanFailure:
    code: str
    message: str
    resource_id: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        _required(self.code, "code")
        _required(self.message, "message")


@dataclass(frozen=True, slots=True)
class PlannedResourceOperation:
    operation_id: str
    kind: ResourceKind
    target_path: str
    payload: Mapping[str, object]
    required: bool
    resource_id: str
    binding_id: str
    profile_id: str
    version_id: str
    version_digest: str
    asset_id: str | None
    asset_version_id: str | None
    asset_digest: str | None
    prompt_priority: int | None
    source_kind: str
    source_id: str
    source_digest: str
    scope_proof_id: str
    acl_evidence_id: str | None = None
    acl_evidence_digest: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "operation_id", "target_path", "resource_id", "binding_id", "profile_id",
            "version_id", "source_kind", "source_id", "scope_proof_id",
        ):
            _required(getattr(self, name), name)
        if not _SHA256.fullmatch(self.version_digest) or not _SHA256.fullmatch(self.source_digest):
            raise ValueError("version and source digests must be sha256")
        if (self.asset_id is None) != (self.asset_version_id is None):
            raise ValueError("asset id and version id must be supplied together")
        if self.asset_digest is not None and not _SHA256.fullmatch(self.asset_digest):
            raise ValueError("asset_digest must be sha256")
        if (self.acl_evidence_id is None) != (self.acl_evidence_digest is None):
            raise ValueError("ACL evidence id and digest must be supplied together")
        if self.acl_evidence_digest is not None and not _SHA256.fullmatch(self.acl_evidence_digest):
            raise ValueError("ACL evidence digest must be sha256")
        object.__setattr__(self, "kind", ResourceKind(self.kind))
        payload = freeze_json(self.payload)
        if not isinstance(payload, Mapping):
            raise ValueError("operation payload must be an object")
        object.__setattr__(self, "payload", payload)

    def canonical(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "target_path": self.target_path,
            "payload": self.payload,
            "required": self.required,
            "resource_id": self.resource_id,
            "binding_id": self.binding_id,
            "profile_id": self.profile_id,
            "version_id": self.version_id,
            "version_digest": self.version_digest,
            "asset_id": self.asset_id,
            "asset_version_id": self.asset_version_id,
            "asset_digest": self.asset_digest,
            "prompt_priority": self.prompt_priority,
            "source": {
                "kind": self.source_kind,
                "id": self.source_id,
                "digest": self.source_digest,
                "scope_proof_id": self.scope_proof_id,
            },
            "acl_evidence": {
                "id": self.acl_evidence_id,
                "digest": self.acl_evidence_digest,
            } if self.acl_evidence_id is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ResourceProjectPlan:
    request: ResourcePlanRequest
    operations: tuple[PlannedResourceOperation, ...]
    failures: tuple[PlanFailure, ...]
    upstream_evidence: tuple[UpstreamEvidence, ...]
    revision_digest: str
    snapshot_digest: str
    warnings: tuple[PlanFailure, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "failures", tuple(self.failures))
        object.__setattr__(self, "upstream_evidence", tuple(self.upstream_evidence))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if not _SHA256.fullmatch(self.revision_digest) or not _SHA256.fullmatch(self.snapshot_digest):
            raise ValueError("plan digests must be sha256")
        if self.failures and self.operations:
            raise ValueError("blocked plans cannot expose partial operations")

    @property
    def ready(self) -> bool:
        return not self.failures

    @property
    def prompt_order(self) -> tuple[PlannedResourceOperation, ...]:
        return tuple(item for item in self.operations if item.kind is ResourceKind.PROMPT)


class UpstreamResourceEvidencePort(Protocol):
    async def read_upstream_evidence(self, *, request: ResourcePlanRequest) -> tuple[UpstreamEvidence, ...]: ...


class ResourceSnapshotReadPort(Protocol):
    async def read_snapshots(
        self, *, request: ResourcePlanRequest, selections: tuple[ResourcePlanSelection, ...]
    ) -> tuple[ResourceSnapshot, ...]: ...


class FileRefAclEvidencePort(Protocol):
    async def read_file_ref_acl_evidence(
        self, *, request: ResourcePlanRequest, snapshot: ResourceSnapshot, asset_id: str, asset_version_id: str
    ) -> FileRefAclEvidence | None: ...


class ResourceOperationReadinessPort(Protocol):
    async def validate_operation(
        self, *, request: ResourcePlanRequest, operation: PlannedResourceOperation
    ) -> ResourceOperationReadiness: ...


@dataclass(frozen=True, slots=True)
class ResourcePlanPorts:
    upstream_evidence: UpstreamResourceEvidencePort
    snapshots: ResourceSnapshotReadPort
    file_acl: FileRefAclEvidencePort
    readiness: ResourceOperationReadinessPort


__all__ = [
    "FileRefAclEvidence", "FileRefAclEvidencePort",
    "PlanFailure", "PlannedResourceOperation", "ResourceOperationReadiness",
    "ResourceOperationReadinessPort", "ResourcePlanPorts", "ResourcePlanRequest",
    "ResourcePlanSelection", "ResourceProjectPlan", "ResourceSnapshotReadPort",
    "UpstreamEvidence", "UpstreamResourceEvidencePort",
]
