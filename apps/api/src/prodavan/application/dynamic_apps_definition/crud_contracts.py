"""Command/response contracts for the dynamic-app CRUD surface (MIG-DYN-CRUD-01).

Transport-neutral on purpose: the HTTP layer only parses bodies and maps these results
into the response envelope from ``docs/02-architecture/api-style.md``. Every mutation is
described by an immutable command carrying the *server-resolved* tenant, so a client can
never move a write into another tenant by sending a body field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from prodavan.domain.dynamic_apps import AppScopeKind, CapabilityDecision, GrantActor


class DynamicAppOperation(StrEnum):
    """Idempotency/idempotency-audit scope per mutating scenario."""

    CREATE_DEFINITION = "definition.create"
    CREATE_VERSION = "version.create"
    PUBLISH_VERSION = "version.publish"
    ARCHIVE_DEFINITION = "definition.archive"
    INSTALL_INSTANCE = "instance.install"
    ISSUE_GRANT = "grant.issue"
    REVOKE_GRANT = "grant.revoke"


class DynamicAppAuditEvent(StrEnum):
    """Durable audit event types written in the same transaction as the mutation.

    These live in ``platform_events`` (the platform lifecycle bus). They must also be
    registered in ``PLATFORM_EVENT_TYPES``; that constant is owned by
    ``domain/projects/types.py`` and is outside this task's zone, so the registration is
    reported as an open item instead of being edited in place.
    """

    DEFINITION_CREATED = "dynamic_app.definition.created"
    VERSION_CREATED = "dynamic_app.version.created"
    VERSION_PUBLISHED = "dynamic_app.version.published"
    DEFINITION_ARCHIVED = "dynamic_app.definition.archived"
    INSTANCE_INSTALLED = "dynamic_app.instance.installed"
    GRANT_ISSUED = "dynamic_app.grant.issued"
    GRANT_REVOKED = "dynamic_app.grant.revoked"


class DynamicAppErrorCode(StrEnum):
    """Stable error codes for this surface (mapped to RFC 7807 by the API layer)."""

    TENANT_CONTEXT_REQUIRED = "TENANT_CONTEXT_REQUIRED"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_REPLAY = "IDEMPOTENCY_REPLAY"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    APP_ID_TAKEN = "APP_ID_TAKEN"
    VERSION_EXISTS = "VERSION_EXISTS"
    VERSION_ALREADY_PUBLISHED = "VERSION_ALREADY_PUBLISHED"
    VERSION_NOT_PUBLISHED = "VERSION_NOT_PUBLISHED"
    INVALID_LIFECYCLE_TRANSITION = "INVALID_LIFECYCLE_TRANSITION"
    ALREADY_ARCHIVED = "ALREADY_ARCHIVED"
    APP_ARCHIVED = "APP_ARCHIVED"
    APP_NOT_PUBLISHED = "APP_NOT_PUBLISHED"
    STATE_CHANGED = "STATE_CHANGED"
    INSTANCE_EXISTS = "INSTANCE_EXISTS"
    CAPABILITY_NOT_REQUESTED = "CAPABILITY_NOT_REQUESTED"
    GRANT_ALREADY_REVOKED = "GRANT_ALREADY_REVOKED"
    GRANT_REVOKED = "GRANT_REVOKED"
    SCOPE_DENIED = "SCOPE_DENIED"
    INVALID_SCOPE = "INVALID_SCOPE"


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for request digests and manifest digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest_of(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def idempotency_scope(*, tenant_id: str, operation: DynamicAppOperation, key: str) -> str:
    """Tenant- and operation-scoped idempotency identity.

    The raw client key is hashed so the stored scope stays bounded (the audit column is
    ``String(200)``) while remaining deterministic for exact replay.
    """
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
    return f"dapp:{operation.value}:{tenant_id}:{key_digest}"[:200]


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    """Server-resolved caller identity; never derived from the request body."""

    tenant_id: str
    actor_id: str
    granted_by: GrantActor = GrantActor.ADMINISTRATOR

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.actor_id.strip():
            raise ValueError("actor identity requires tenant and actor")


@dataclass(frozen=True, slots=True)
class MutationContext:
    """Per-request mutation envelope: identity plus idempotency provenance."""

    identity: ActorIdentity
    idempotency_key: str
    operation: DynamicAppOperation

    @property
    def scope(self) -> str:
        return idempotency_scope(
            tenant_id=self.identity.tenant_id,
            operation=self.operation,
            key=self.idempotency_key,
        )

    @property
    def trace_id(self) -> str:
        seed = f"{self.identity.tenant_id}\0{self.identity.actor_id}\0{self.operation.value}\0{self.idempotency_key}"
        return "dynamic-app:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CreateDefinitionRequest:
    app_id: str
    display_name: str
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.app_id.strip() or not self.display_name.strip():
            raise ValueError("app_id and display_name are required")


@dataclass(frozen=True, slots=True)
class CreateVersionRequest:
    manifest: Mapping[str, Any]
    artifact_digest: str
    artifact_uri: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, Mapping):
            raise ValueError("manifest must be an object")
        if not self.artifact_digest.startswith("sha256:"):
            raise ValueError("artifact_digest must be sha256-prefixed")


@dataclass(frozen=True, slots=True)
class PublishVersionRequest:
    definition_id: str
    version_id: str
    expected_current_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArchiveDefinitionRequest:
    definition_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InstallInstanceRequest:
    definition_id: str
    scope_kind: AppScopeKind
    scope_id: str
    version_id: str | None = None
    runtime_ref: str | None = None

    def __post_init__(self) -> None:
        if self.scope_kind is AppScopeKind.TENANT:
            raise ValueError("instances are cabinet- or project-scoped")
        if not self.scope_id.strip():
            raise ValueError("scope_id is required")


@dataclass(frozen=True, slots=True)
class GrantRequest:
    """One requested capability inside a grant command."""

    name: str
    decision: CapabilityDecision = CapabilityDecision.GRANTED
    constraints: Mapping[str, Any] | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssueGrantRequest:
    definition_id: str
    version_id: str
    scope_kind: AppScopeKind
    scope_id: str
    capabilities: tuple[GrantRequest, ...]
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("at least one capability is required")
        names = [item.name for item in self.capabilities]
        if len(set(names)) != len(names):
            raise ValueError("duplicate capability in grant request")


@dataclass(frozen=True, slots=True)
class RevokeGrantRequest:
    definition_id: str
    version_id: str
    scope_kind: AppScopeKind
    scope_id: str
    capability: str
    reason: str


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """Public representation returned to the caller and stored for exact replay."""

    data: Mapping[str, Any]
    trace_id: str
    replayed: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "ActorIdentity",
    "ArchiveDefinitionRequest",
    "CreateDefinitionRequest",
    "CreateVersionRequest",
    "DynamicAppAuditEvent",
    "DynamicAppErrorCode",
    "DynamicAppOperation",
    "GrantRequest",
    "InstallInstanceRequest",
    "IssueGrantRequest",
    "MutationContext",
    "MutationReceipt",
    "PublishVersionRequest",
    "RevokeGrantRequest",
    "canonical_json",
    "digest_of",
    "idempotency_scope",
]
