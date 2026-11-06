"""Persistence capability grants for versioned dynamic apps and plugins.

The contract is intentionally deny-by-default: a grant only authorizes an
exact app/version, tenant, scope, operation, resource, and domain match while
it is within its validity window and has not been revoked.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GrantScopeKind(StrEnum):
    TENANT = "tenant"
    CABINET = "cabinet"
    PROJECT = "project"


class GrantDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class GrantDenyReason(StrEnum):
    APP_MISMATCH = "app_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    TENANT_MISMATCH = "tenant_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    NOT_YET_ISSUED = "not_yet_issued"
    EXPIRED = "expired"
    REVOKED = "revoked"
    OPERATION_NOT_GRANTED = "operation_not_granted"
    RESOURCE_NOT_GRANTED = "resource_not_granted"
    DOMAIN_NOT_GRANTED = "domain_not_granted"


class GrantScope(BaseModel):
    """Tenant, cabinet, or project scope to which a grant applies."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: GrantScopeKind
    id: str = Field(min_length=1, max_length=80)


class GrantAuthorization(BaseModel):
    """Transport-neutral result of checking a request against one grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: GrantDecision
    reason: GrantDenyReason | None = None
    grant_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is GrantDecision.ALLOW


class PersistenceCapabilityGrant(BaseModel):
    """An explicit, revocable grant for one immutable app version."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    app_id: str = Field(min_length=1, max_length=160)
    app_version_id: str = Field(min_length=1, max_length=80)
    tenant_id: str = Field(min_length=1, max_length=80)
    scope: GrantScope
    operations: tuple[str, ...] = Field(min_length=1)
    resources: tuple[str, ...] = Field(min_length=1)
    domains: tuple[str, ...] = Field(min_length=1)
    constraints: Mapping[str, Any] = Field(default_factory=dict)
    issued_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    issued_by: str = Field(min_length=1, max_length=80)
    revoked_by: str | None = Field(default=None, max_length=80)
    revoke_reason: str | None = Field(default=None, max_length=2_000)
    audit_metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("operations", "resources", "domains")
    @classmethod
    def entries_are_non_empty_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("grant entries must not be empty")
        if len(set(value)) != len(value):
            raise ValueError("grant entries must be unique")
        return value

    @field_validator("issued_at", "expires_at", "revoked_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("grant timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> PersistenceCapabilityGrant:
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.revoked_at is not None and self.revoked_at < self.issued_at:
            raise ValueError("revoked_at must not precede issued_at")
        if self.revoked_at is None and (self.revoked_by is not None or self.revoke_reason is not None):
            raise ValueError("revoke metadata requires revoked_at")
        if self.revoked_at is not None and self.revoked_by is None:
            raise ValueError("revoked grant requires revoked_by")
        return self

    def authorize(
        self,
        *,
        app_id: str,
        app_version_id: str,
        tenant_id: str,
        scope: GrantScope,
        operation: str,
        resource: str,
        domain: str,
        now: datetime,
    ) -> GrantAuthorization:
        """Authorize one request; every mismatch is a deterministic denial."""
        if app_id != self.app_id:
            return self._deny(GrantDenyReason.APP_MISMATCH)
        if app_version_id != self.app_version_id:
            return self._deny(GrantDenyReason.VERSION_MISMATCH)
        if tenant_id != self.tenant_id:
            return self._deny(GrantDenyReason.TENANT_MISMATCH)
        if scope != self.scope:
            return self._deny(GrantDenyReason.SCOPE_MISMATCH)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if now < self.issued_at:
            return self._deny(GrantDenyReason.NOT_YET_ISSUED)
        if self.expires_at is not None and now >= self.expires_at:
            return self._deny(GrantDenyReason.EXPIRED)
        if self.revoked_at is not None and now >= self.revoked_at:
            return self._deny(GrantDenyReason.REVOKED)
        if operation not in self.operations:
            return self._deny(GrantDenyReason.OPERATION_NOT_GRANTED)
        if resource not in self.resources:
            return self._deny(GrantDenyReason.RESOURCE_NOT_GRANTED)
        if domain not in self.domains:
            return self._deny(GrantDenyReason.DOMAIN_NOT_GRANTED)
        return GrantAuthorization(decision=GrantDecision.ALLOW, grant_id=self.id)

    def _deny(self, reason: GrantDenyReason) -> GrantAuthorization:
        return GrantAuthorization(decision=GrantDecision.DENY, reason=reason, grant_id=self.id)


__all__ = [
    "GrantAuthorization",
    "GrantDecision",
    "GrantDenyReason",
    "GrantScope",
    "GrantScopeKind",
    "PersistenceCapabilityGrant",
]
