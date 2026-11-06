"""Typed owner-anchor contracts for the migration application boundary.

This zone is persistence-neutral. It does not convert legacy values, write
quarantine rows, or treat caller assertions as ownership authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

TENANT_ID_MAX_LENGTH = 40
OWNER_ID_MAX_LENGTH = 40


class ScopeKind(StrEnum):
    TENANT = "tenant"
    CABINET = "cabinet"
    PROJECT = "project"
    PLUGIN_INSTANCE = "plugin_instance"


class OwnerState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    REVOKED = "revoked"


class OwnerAnchorReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_KEY = "invalid_owner_key"
    MISSING_RLS_CONTEXT = "missing_rls_context"
    RLS_CONTEXT_MISMATCH = "rls_context_mismatch"
    OWNER_NOT_FOUND = "owner_not_found"
    DUPLICATE_OWNER = "duplicate_owner"
    OWNER_KEY_MISMATCH = "owner_key_mismatch"
    TENANT_NOT_ACTIVE = "tenant_not_active"
    CABINET_NOT_COMPATIBLE = "cabinet_not_compatible"
    PROJECT_NOT_COMPATIBLE = "project_not_compatible"
    PLUGIN_INSTANCE_NOT_COMPATIBLE = "plugin_instance_not_compatible"
    OWNER_ARCHIVED = "owner_archived"
    OWNER_QUARANTINED = "owner_quarantined"


@dataclass(frozen=True, slots=True)
class OwnerKey:
    """Canonical typed owner identity; no polymorphic ID or implicit cast."""

    scope_kind: ScopeKind
    scope_id: str
    tenant_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_instance_id: str | None = None

    def __post_init__(self) -> None:
        _require_exact_id(self.scope_id, "scope_id", OWNER_ID_MAX_LENGTH)
        _require_exact_id(self.tenant_id, "tenant_id", TENANT_ID_MAX_LENGTH)
        for name, value in (("cabinet_id", self.cabinet_id), ("project_id", self.project_id), ("plugin_instance_id", self.plugin_instance_id)):
            if value is not None:
                _require_exact_id(value, name, OWNER_ID_MAX_LENGTH)
        if self.scope_kind is ScopeKind.TENANT:
            if self.scope_id != self.tenant_id or any((self.cabinet_id, self.project_id, self.plugin_instance_id)):
                raise ValueError("tenant owner shape is invalid")
        elif self.scope_kind is ScopeKind.CABINET:
            if self.scope_id != self.cabinet_id or self.project_id is not None or self.plugin_instance_id is not None:
                raise ValueError("cabinet owner shape is invalid")
        elif self.scope_kind is ScopeKind.PROJECT:
            if self.scope_id != self.project_id or self.cabinet_id is None or self.plugin_instance_id is not None:
                raise ValueError("project owner shape is invalid")
        elif self.scope_id != self.plugin_instance_id:
            raise ValueError("plugin instance owner shape is invalid")


@dataclass(frozen=True, slots=True)
class RlsContext:
    """Server-resolved transaction context, never a client assertion."""

    tenant_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_instance_id: str | None = None
    user_id: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        _require_exact_id(self.tenant_id, "tenant_id", TENANT_ID_MAX_LENGTH)
        for name, value in (("cabinet_id", self.cabinet_id), ("project_id", self.project_id), ("plugin_instance_id", self.plugin_instance_id), ("user_id", self.user_id)):
            if value is not None:
                _require_exact_id(value, name, OWNER_ID_MAX_LENGTH)


@dataclass(frozen=True, slots=True)
class ParentCompatibility:
    """Trusted server-side parent and lifecycle evidence."""

    company_exists: bool
    company_status: LifecycleStatus
    company_deleted: bool
    company_id: str | None = None
    cabinet_exists: bool = False
    cabinet_status: LifecycleStatus | None = None
    cabinet_id: str | None = None
    cabinet_tenant_matches: bool = False
    cabinet_membership_active: bool = False
    project_exists: bool = False
    project_status: LifecycleStatus | None = None
    project_id: str | None = None
    project_tenant_matches: bool = False
    project_cabinet_matches: bool = False
    plugin_instance_exists: bool = False
    plugin_instance_id: str | None = None

    def valid_for(self, key: OwnerKey) -> bool:
        if not self.company_exists or self.company_deleted or self.company_status is not LifecycleStatus.ACTIVE or self.company_id != key.tenant_id:
            return False
        cabinet_ok = (
            self.cabinet_exists
            and self.cabinet_id == key.cabinet_id
            and self.cabinet_status is LifecycleStatus.ACTIVE
            and self.cabinet_tenant_matches
            and self.cabinet_membership_active
        )
        project_ok = (
            self.project_exists
            and self.project_id == key.project_id
            and self.project_status is LifecycleStatus.ACTIVE
            and self.project_tenant_matches
            and self.project_cabinet_matches
        )
        if key.scope_kind is ScopeKind.TENANT:
            return True
        if key.scope_kind is ScopeKind.CABINET:
            return cabinet_ok
        if key.scope_kind is ScopeKind.PROJECT:
            return cabinet_ok and project_ok
        if not self.plugin_instance_exists or self.plugin_instance_id != key.plugin_instance_id:
            return False
        if key.cabinet_id is not None and not cabinet_ok:
            return False
        if key.project_id is not None and not project_ok:
            return False
        return True


@dataclass(frozen=True, slots=True)
class OwnerAnchorRecord:
    key: OwnerKey
    owner_state: OwnerState
    lifecycle_status: LifecycleStatus


@dataclass(frozen=True, slots=True)
class OwnerAnchorDecision:
    allowed: bool
    reason: OwnerAnchorReason
    record: OwnerAnchorRecord | None = None

    def __post_init__(self) -> None:
        if self.allowed != (self.reason is OwnerAnchorReason.ALLOWED):
            raise ValueError("allowed must match decision reason")
        if self.allowed and self.record is None:
            raise ValueError("allowed decision requires an owner record")
        if not self.allowed and self.record is not None:
            raise ValueError("denied decision must not expose an owner record")


def _require_exact_id(value: str, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty, whitespace-exact string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds String({maximum})")
