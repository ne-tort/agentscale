"""Typed application contracts for composing and applying runtime revisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class RevisionState(StrEnum):
    PLANNED = "planned"
    VALIDATED = "validated"
    APPLYING = "applying"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class MaterializationStatus(StrEnum):
    PLANNED = "planned"
    STAGED = "staged"
    APPLIED = "applied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CapabilityBundle:
    """Immutable canonical manifest and its content digest."""

    bundle_id: str
    project_id: str
    revision: int
    manifest: dict[str, Any]
    digest: str


@dataclass(frozen=True, slots=True)
class MaterializationEntry:
    """One generated path with hash and source provenance."""

    entry_id: str
    revision_id: str
    workspace_path: str
    kind: str
    generation: int
    status: MaterializationStatus = MaterializationStatus.PLANNED
    content_hash: str | None = None
    source_asset_id: str | None = None
    source_capability_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    idempotency_key: str | None = None
    operation_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectRuntimeRevision:
    """Immutable desired snapshot plus durable lifecycle projection."""

    revision_id: str
    project_id: str
    revision: int
    bundle: CapabilityBundle
    state: RevisionState = RevisionState.PLANNED
    applied_revision_id: str | None = None
    pod_id: str | None = None
    pod_uid: str | None = None
    hydrate_generation: int = 0
    bridge_generation: int = 0
    runtime_image_digest: str | None = None
    failure_code: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    source_event: str | None = None


class RuntimeRevisionRepository(Protocol):
    """Persistence port for revision snapshots and their materialization entries."""

    async def get(self, *, revision_id: str) -> ProjectRuntimeRevision | None: ...

    async def get_latest(self, *, project_id: str) -> ProjectRuntimeRevision | None: ...

    async def save(self, *, revision: ProjectRuntimeRevision) -> ProjectRuntimeRevision: ...

    async def replace_entries(
        self,
        *,
        revision_id: str,
        entries: list[MaterializationEntry],
    ) -> list[MaterializationEntry]: ...

    async def list_entries(self, *, revision_id: str) -> list[MaterializationEntry]: ...


class RuntimeRevisionService(Protocol):
    """Prepare/apply boundary; implementations must fail closed on partial apply."""

    async def prepare(
        self,
        *,
        project_id: str,
        created_by: str | None = None,
        source_event: str | None = None,
    ) -> ProjectRuntimeRevision: ...

    async def apply(self, *, revision_id: str) -> ProjectRuntimeRevision: ...

    async def rollback(self, *, project_id: str, revision_id: str) -> ProjectRuntimeRevision: ...
