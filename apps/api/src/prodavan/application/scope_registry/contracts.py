"""Typed scope-registry contracts for the M04 application boundary.

This module intentionally contains no ORM or persistence assumptions.  ``tenant_id``
is the canonical ``companies.id`` value and is therefore bounded to 40 characters.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


TENANT_ID_MAX_LENGTH = 40


class ScopeKind(StrEnum):
    TENANT = "tenant"
    CABINET = "cabinet"
    PROJECT = "project"
    PLUGIN_INSTANCE = "plugin_instance"


class OwnerState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class ScopeContractError(ValueError):
    """Raised when a typed scope cannot be represented safely."""


class ScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ScopeAnchors(ScopeModel):
    """Typed identity and parent anchors for one registry row."""

    scope_kind: ScopeKind
    scope_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    tenant_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    cabinet_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    project_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    plugin_instance_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)

    @model_validator(mode="after")
    def validate_shape(self) -> "ScopeAnchors":
        values = self.model_dump()
        if self.scope_kind is ScopeKind.TENANT:
            expected = {"scope_id": self.tenant_id}
            if self.scope_id != self.tenant_id or any(
                values[key] is not None for key in ("cabinet_id", "project_id", "plugin_instance_id")
            ):
                raise ValueError("tenant scope must equal tenant_id and have no child anchors")
            return self
        if self.scope_kind is ScopeKind.CABINET:
            if self.scope_id != self.cabinet_id or self.project_id or self.plugin_instance_id:
                raise ValueError("cabinet scope must equal cabinet_id and have no project/plugin anchor")
            return self
        if self.scope_kind is ScopeKind.PROJECT:
            if not self.cabinet_id or self.scope_id != self.project_id or self.plugin_instance_id:
                raise ValueError("project scope requires cabinet_id and must equal project_id")
            return self
        if self.scope_id != self.plugin_instance_id:
            raise ValueError("plugin scope must equal plugin_instance_id")
        if self.project_id is not None and self.cabinet_id is None:
            raise ValueError("plugin scope with project anchor requires cabinet_id")
        return self


class ScopeProvenance(ScopeModel):
    source_kind: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    schema_version: str = Field(min_length=1, max_length=40)
    payload_digest: str = Field(min_length=1, max_length=128)


class ScopeRegistryEntry(ScopeModel):
    anchors: ScopeAnchors
    owner_state: OwnerState
    provenance: ScopeProvenance
    observed_at: datetime | None = None


class ParentCompatibilityProof(ScopeModel):
    """Server-side evidence; caller claims cannot substitute for this proof."""

    tenant_exists: bool
    tenant_retained: bool
    cabinet_exists: bool = False
    cabinet_tenant_compatible: bool = False
    cabinet_membership_active: bool = False
    project_exists: bool = False
    project_tenant_compatible: bool = False
    project_cabinet_compatible: bool = False
    project_policy_eligible: bool = False

    @property
    def valid_for(self) -> bool:
        return self.tenant_exists and self.tenant_retained

    def valid_for_scope(self, kind: ScopeKind) -> bool:
        if kind is ScopeKind.PLUGIN_INSTANCE:
            return False
        if not self.valid_for:
            return False
        if kind is ScopeKind.TENANT:
            return True
        if not (self.cabinet_exists and self.cabinet_tenant_compatible and self.cabinet_membership_active):
            return False
        if kind is ScopeKind.CABINET:
            return True
        if kind is ScopeKind.PROJECT:
            return self.project_exists and self.project_tenant_compatible and self.project_cabinet_compatible and self.project_policy_eligible
        return False

    def valid_for_plugin(self, anchors: ScopeAnchors) -> bool:
        """Validate the installed plugin's parent chain after exact registry lookup."""
        if not self.valid_for:
            return False
        if anchors.project_id is not None:
            return (
                anchors.cabinet_id is not None
                and self.cabinet_exists
                and self.cabinet_tenant_compatible
                and self.cabinet_membership_active
                and self.project_exists
                and self.project_tenant_compatible
                and self.project_cabinet_compatible
                and self.project_policy_eligible
            )
        if anchors.cabinet_id is not None:
            return self.cabinet_exists and self.cabinet_tenant_compatible and self.cabinet_membership_active
        return True


class RlsContext(ScopeModel):
    """Transaction-local context required by tenant-owned RLS policies."""

    tenant_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    cabinet_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    project_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    user_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    role: str = Field(min_length=1, max_length=80)


class PositiveWriteDecision(ScopeModel):
    allowed: bool
    reason_code: str
    entry: ScopeRegistryEntry | None = None
    quarantine_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class QuarantineRecord(ScopeModel):
    source_kind: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    schema_version: str = Field(min_length=1, max_length=40)
    payload_digest: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=80)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    scope_kind: ScopeKind | None = None
    scope_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    details: dict[str, Any] = Field(default_factory=dict)


class ScopeAssertion(ScopeModel):
    """Optional legacy/client assertion, accepted only for equality checking."""

    tenant_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    cabinet_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    project_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
    scope_kind: ScopeKind | None = None
    scope_id: str | None = Field(default=None, min_length=1, max_length=TENANT_ID_MAX_LENGTH)
