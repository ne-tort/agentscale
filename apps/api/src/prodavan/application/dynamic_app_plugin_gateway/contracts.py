"""Transport-neutral contracts for the exclusive dynamic plugin gateway zone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID, uuid4

from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppScope,
    AppVersion,
    CapabilityGrant,
)


class GatewayOutcome(StrEnum):
    OPENED = "opened"
    ALLOWED = "allowed"
    DENIED = "denied"
    QUARANTINED = "quarantined"
    MISSING = "missing"


class GatewayReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    APP_MISSING = "app_missing"
    VERSION_MISSING = "version_missing"
    MANIFEST_INVALID = "manifest_invalid"
    GRANTS_MISSING = "grants_missing"
    INSTANCE_MISSING = "instance_missing"
    CROSS_TENANT = "cross_tenant"
    CROSS_VERSION = "cross_version"
    LIFECYCLE_DENIED = "lifecycle_denied"
    HANDLE_INVALID = "handle_invalid"
    METHOD_UNKNOWN = "method_unknown"
    CAPABILITY_NOT_REQUESTED = "capability_not_requested"
    GRANT_DENIED = "grant_denied"
    SCOPE_DENIED = "scope_denied"
    QUOTA_EXCEEDED = "quota_exceeded"
    EXECUTOR_MISSING = "executor_missing"
    EXECUTOR_FAILED = "executor_failed"
    PORT_FAILED = "port_failed"


@dataclass(frozen=True, slots=True)
class PluginOpenRequest:
    tenant_id: str
    app_id: str
    manifest_version: str
    scope: AppScope
    subject_id: str


@dataclass(frozen=True, slots=True)
class PluginInvokeRequest:
    handle: "PluginHandle"
    method: str
    params: Mapping[str, Any] = field(default_factory=dict)
    requested_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class PluginMethod:
    """One exact manifest method binding; no prefix or wildcard matching."""

    method: str
    capability: str
    operation: str
    resource: str
    domain: str
    constraints: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginRecord:
    definition: AppDefinition
    version: AppVersion
    grants: tuple[AppGrant, ...]
    instance: AppInstance
    methods: tuple[PluginMethod, ...]


@dataclass(frozen=True, slots=True)
class PluginContext:
    tenant_id: str
    app_id: str
    app_definition_id: str
    app_version_id: str
    manifest_version: str
    artifact_digest: str
    instance_id: str
    scope: AppScope
    subject_id: str
    grants: tuple[CapabilityGrant, ...]
    requested_capabilities: frozenset[str]
    methods: tuple[PluginMethod, ...]
    grant_rows: tuple[AppGrant, ...] = ()


class PluginHandle:
    """Opaque capability context; callers cannot obtain a raw credential/token."""

    __slots__ = ("__nonce",)

    def __init__(self) -> None:
        self.__nonce: UUID = uuid4()

    def __hash__(self) -> int:
        return hash(self.__nonce)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PluginHandle) and self.__nonce == other.__nonce

    def __repr__(self) -> str:
        return "PluginHandle(<opaque>)"


@dataclass(frozen=True, slots=True)
class GatewayResult:
    outcome: GatewayOutcome
    reason: GatewayReason | None = None
    handle: PluginHandle | None = None
    context: PluginContext | None = None
    value: Any | None = None
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is GatewayOutcome.OPENED and (self.handle is None or self.context is None):
            raise ValueError("opened result requires opaque handle and context")
        if self.outcome is not GatewayOutcome.OPENED and self.handle is not None:
            raise ValueError("only opened result may expose a handle")
        if self.outcome is not GatewayOutcome.ALLOWED and self.value is not None:
            raise ValueError("only allowed result may expose a value")
        if self.outcome in {GatewayOutcome.DENIED, GatewayOutcome.QUARANTINED, GatewayOutcome.MISSING} and self.reason is None:
            raise ValueError("negative result requires reason")


@dataclass(frozen=True, slots=True)
class QuarantineEvidence:
    reason: GatewayReason
    tenant_id: str
    app_id: str
    source_id: str
    observed_at: datetime
    metadata: Mapping[str, str] = field(default_factory=dict)


__all__ = [
    "GatewayOutcome", "GatewayReason", "GatewayResult", "PluginContext",
    "PluginHandle", "PluginInvokeRequest", "PluginMethod", "PluginOpenRequest",
    "PluginRecord", "QuarantineEvidence",
]
