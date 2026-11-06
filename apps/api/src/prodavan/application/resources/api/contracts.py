"""Immutable contracts for the canonical Resource API.

The canonical model for a resource, its immutable versions, its bindings and its
lifecycle is ``application.migration.resources.lifecycle_contracts`` (MIG-RES-01).
This module only adds the transport-shaped records the HTTP surface needs:

* ``ResolvedTenant`` — server-resolved tenant and actor (never client-supplied);
* ``StoredResource`` / ``StoredVersion`` / ``StoredBinding`` — reads returned by a
  persistence port, already scoped to one tenant;
* ``*View`` — wire payloads with a stable, JSON-safe ``as_payload()``.

No wire record carries secret material: payloads are rejected by
``reject_secret_material`` before they reach a view or a store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from prodavan.application.migration.resources.lifecycle_contracts import (
    BindingKind,
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    VersionStatus,
)

#: Schema marker for reconstructed immutable versions. The version row has no
#: schema column, so the API binds one constant schema per stored payload.
RESOURCE_VERSION_SCHEMA = "resource-version.v1"

#: ``resources.metadata`` keys owned by the API. Description, lifecycle and the
#: version change notes have no column in the current model, and the version
#: receipt map exists only because no receipt table exists yet (see README).
METADATA_DESCRIPTION = "description"
METADATA_LIFECYCLE = "x_lifecycle"
METADATA_VERSION_NOTES = "x_version_notes"
METADATA_VERSION_RECEIPTS = "x_version_receipts"
METADATA_CREATE_RECEIPT = "x_create_receipt"
METADATA_TARGET_PATH = "path"

#: Every reserved key is stripped from client input and never echoed back.
RESERVED_METADATA_KEYS = frozenset(
    {
        METADATA_LIFECYCLE,
        METADATA_VERSION_NOTES,
        METADATA_VERSION_RECEIPTS,
        METADATA_CREATE_RECEIPT,
    }
)

#: Kinds accepted on the wire (matches the web ``ResourceKind`` union). The
#: migration contracts keep pre-MIG values for compatibility; they are not
#: creatable through this API.
RESOURCE_KINDS = frozenset({"prompt", "file", "mcp"})

#: Kinds a resource root may be owned by. ``company`` is the tenant anchor
#: (``tenant_id == scope_id``); cabinet and project are narrower owners inside
#: the same tenant.
OWNER_SCOPE_KINDS = frozenset({"company", "cabinet", "project"})

#: Kinds a binding may target. ``module`` bindings are administrative only:
#: ``ScopeKind`` has no ``module`` member, so no materialization snapshot can be
#: built for them.
TARGET_SCOPE_KINDS = frozenset({"cabinet", "project", "module"})

#: Target kinds the existing materialization resolvers can expand into a plan.
MATERIALIZABLE_TARGET_KINDS = frozenset({"project"})

BINDING_KINDS = frozenset({"local", "global"})


def _jsonable(value: Any) -> Any:
    """Convert frozen mappings/sequences into plain JSON-compatible values."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip():
        raise ValueError(f"{name} must not be padded with whitespace")
    return value


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class ResolvedTenant:
    """Server-resolved tenant and actor for one request."""

    tenant_id: str
    actor_id: str
    is_platform_admin: bool = False

    def __post_init__(self) -> None:
        _required_text(self.tenant_id, "tenant_id")
        _required_text(self.actor_id, "actor_id")


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """A typed scope reference as received from a client."""

    kind: str
    scope_id: str

    def __post_init__(self) -> None:
        _required_text(self.kind, "scope kind")
        _required_text(self.scope_id, "scope id")
        if self.kind != self.kind.lower():
            raise ValueError("scope kind must be lower-case")

    def as_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.scope_id}


@dataclass(frozen=True, slots=True)
class NewVersion:
    """A version to append. The digest is derived, never trusted from a client."""

    payload: Mapping[str, Any]
    content_digest: str
    label: str = ""
    change_note: str = ""

    def __post_init__(self) -> None:
        _required_text(self.content_digest, "content_digest")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ResourceAggregate:
    """Cheap counts the list/detail views need, computed by the store."""

    version_numbers: tuple[int, ...] = ()
    binding_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_numbers", tuple(sorted(self.version_numbers)))
        if self.binding_count < 0:
            raise ValueError("binding_count cannot be negative")


@dataclass(frozen=True, slots=True)
class StoredResource:
    """One tenant-scoped resource root as returned by the store."""

    tenant_id: str
    resource_id: str
    kind: ResourceKind
    slug: str
    title: str
    description: str
    scope: ResourceScope
    lifecycle: ResourceLifecycle
    active_version: int | None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _required_text(self.resource_id, "resource_id")
        _required_text(self.slug, "slug")
        object.__setattr__(self, "kind", ResourceKind(self.kind))
        object.__setattr__(self, "lifecycle", ResourceLifecycle(self.lifecycle))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.active_version is not None and self.active_version < 1:
            raise ValueError("active_version must be positive when set")

    def receipt_for(self, idempotency_key: str) -> Mapping[str, Any] | None:
        receipts = self.metadata.get(METADATA_VERSION_RECEIPTS)
        if not isinstance(receipts, Mapping):
            return None
        entry = receipts.get(idempotency_key)
        return entry if isinstance(entry, Mapping) else None

    @property
    def create_receipt(self) -> Mapping[str, Any] | None:
        value = self.metadata.get(METADATA_CREATE_RECEIPT)
        return value if isinstance(value, Mapping) else None


@dataclass(frozen=True, slots=True)
class StoredVersion:
    """One immutable version row as returned by the store."""

    resource_id: str
    version_id: str
    number: int
    status: VersionStatus
    payload: Mapping[str, Any]
    content_digest: str
    label: str = ""
    change_note: str = ""
    created_by: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _required_text(self.resource_id, "resource_id")
        _required_text(self.version_id, "version_id")
        _required_text(self.content_digest, "content_digest")
        if self.number < 1:
            raise ValueError("version number must be positive")
        object.__setattr__(self, "status", VersionStatus(self.status))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class StoredBinding:
    """One resource binding row as returned by the store."""

    tenant_id: str
    binding_id: str
    resource_id: str
    target_kind: str
    target_id: str
    binding_kind: BindingKind
    child_may_edit: bool
    version_number: int
    project_ids: tuple[str, ...] = ()
    session_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _required_text(self.binding_id, "binding_id")
        _required_text(self.resource_id, "resource_id")
        _required_text(self.target_kind, "target_kind")
        _required_text(self.target_id, "target_id")
        object.__setattr__(self, "binding_kind", BindingKind(self.binding_kind))
        object.__setattr__(self, "project_ids", tuple(self.project_ids))
        if self.version_number < 1:
            raise ValueError("binding version number must be positive")

    @property
    def target(self) -> ScopeRef:
        return ScopeRef(self.target_kind, self.target_id)

    @property
    def is_materializable(self) -> bool:
        return self.target_kind in MATERIALIZABLE_TARGET_KINDS


@dataclass(frozen=True, slots=True)
class ScopeView:
    kind: str
    id: str

    def as_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True, slots=True)
class ResourceProvenanceView:
    """Redacted provenance for a resource root: digest-bound, payload-free."""

    source_type: str
    source_id: str
    source_generation: int
    digest: str
    source_revision: str | None = None
    source_path: str | None = None
    captured_at: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "sourceGeneration": self.source_generation,
            "sourceRevision": self.source_revision,
            "sourcePath": self.source_path,
            "digest": self.digest,
            "capturedAt": self.captured_at,
        }


@dataclass(frozen=True, slots=True)
class ResourceView:
    id: str
    kind: str
    slug: str
    title: str
    description: str
    scope: ScopeView
    lifecycle: str
    active_version: int | None
    version_numbers: tuple[int, ...]
    binding_count: int
    created_at: str | None
    updated_at: str | None
    provenance: ResourceProvenanceView
    replayed: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "scope": self.scope.as_payload(),
            "lifecycle": self.lifecycle,
            "active_version": self.active_version,
            "version_numbers": list(self.version_numbers),
            "binding_count": self.binding_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": self.provenance.as_payload(),
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class VersionView:
    id: str
    resource_id: str
    number: int
    status: str
    label: str
    change_note: str
    payload: Mapping[str, Any]
    digest: str
    created_at: str | None
    created_by: str | None
    replayed: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "number": self.number,
            "status": self.status,
            "label": self.label,
            "change_note": self.change_note,
            "payload": _jsonable(self.payload),
            "digest": self.digest,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class BindingView:
    id: str
    resource_id: str
    target: ScopeView
    mode: str
    version_number: int
    child_may_edit: bool
    project_ids: tuple[str, ...]
    materializable: bool
    created_at: str | None
    updated_at: str | None
    replayed: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "target": self.target.as_payload(),
            "mode": self.mode,
            "version_number": self.version_number,
            "child_may_edit": self.child_may_edit,
            "project_ids": list(self.project_ids),
            "materializable": self.materializable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class MaterializationEntryView:
    id: str
    resource_id: str
    kind: str
    target: str
    required: bool
    status: str
    digest: str
    provenance: Mapping[str, Any]
    diagnostic: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "kind": self.kind,
            "target": self.target,
            "required": self.required,
            "status": self.status,
            "digest": self.digest,
            "provenance": _jsonable(self.provenance),
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class MaterializationPlanView:
    id: str
    project_id: str
    desired_generation: int
    expected_generation: int
    applied_generation: int | None
    status: str
    digest: str
    idempotency_key: str
    snapshot_digest: str
    entries: tuple[MaterializationEntryView, ...]
    blockers: tuple[str, ...]
    created_at: str

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "desired_generation": self.desired_generation,
            "expected_generation": self.expected_generation,
            "applied_generation": self.applied_generation,
            "status": self.status,
            "ready": self.ready,
            "digest": self.digest,
            "idempotency_key": self.idempotency_key,
            "snapshot_digest": self.snapshot_digest,
            "entries": [entry.as_payload() for entry in self.entries],
            "blockers": list(self.blockers),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class MaterializationApplyView:
    plan_id: str
    project_id: str
    outcome: str
    expected_generation: int
    desired_generation: int
    applied_generation: int | None
    blockers: tuple[str, ...] = ()
    idempotent_replay: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "outcome": self.outcome,
            "expected_generation": self.expected_generation,
            "desired_generation": self.desired_generation,
            "applied_generation": self.applied_generation,
            "blockers": list(self.blockers),
            "idempotent_replay": self.idempotent_replay,
        }


__all__ = [
    "BINDING_KINDS",
    "MATERIALIZABLE_TARGET_KINDS",
    "METADATA_CREATE_RECEIPT",
    "METADATA_DESCRIPTION",
    "METADATA_LIFECYCLE",
    "METADATA_TARGET_PATH",
    "METADATA_VERSION_NOTES",
    "METADATA_VERSION_RECEIPTS",
    "OWNER_SCOPE_KINDS",
    "RESERVED_METADATA_KEYS",
    "RESOURCE_KINDS",
    "RESOURCE_VERSION_SCHEMA",
    "TARGET_SCOPE_KINDS",
    "BindingView",
    "MaterializationApplyView",
    "MaterializationEntryView",
    "MaterializationPlanView",
    "NewVersion",
    "ResolvedTenant",
    "ResourceAggregate",
    "ResourceProvenanceView",
    "ResourceView",
    "ScopeRef",
    "ScopeView",
    "StoredBinding",
    "StoredResource",
    "StoredVersion",
    "VersionView",
]
