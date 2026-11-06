"""Typed domain contracts for declarative apps and isolated plugins."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AppScopeKind(StrEnum):
    TENANT = "tenant"
    CABINET = "cabinet"
    PROJECT = "project"


class AppLifecycleStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    REVOKED = "revoked"


class CapabilityDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"


class GrantActor(StrEnum):
    PLATFORM = "platform"
    ADMINISTRATOR = "administrator"
    USER = "user"


class CapabilityName(StrEnum):
    UI_RENDER = "ui.render"
    UI_NAVIGATION = "ui.navigation"
    STORAGE_KV = "storage.kv"
    STORAGE_FILES = "storage.files"
    NETWORK_FETCH = "network.fetch"
    EVENTS_SUBSCRIBE = "events.subscribe"
    PLATFORM_COMMANDS = "platform.commands"
    PLATFORM_QUERIES = "platform.queries"
    SECRETS_READ = "secrets.read"
    IDENTITY_READ = "identity.read"


class AppScope(_Contract):
    kind: AppScopeKind
    id: str = Field(min_length=1, max_length=80)


class RequestedCapability(_Contract):
    name: CapabilityName
    reason: str = Field(min_length=1, max_length=2_000)
    constraints: Mapping[str, Any] | None = None
    optional: bool = False


class CapabilityGrant(_Contract):
    name: CapabilityName
    decision: CapabilityDecision
    constraints: Mapping[str, Any] | None = None
    expires_at: datetime | None = None
    granted_by: GrantActor

    @field_validator("expires_at")
    @classmethod
    def expiry_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must be timezone-aware")
        return value


class RollbackMetadata(_Contract):
    from_version_id: str | None = Field(default=None, max_length=80)
    to_version_id: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=2_000)
    occurred_at: datetime | None = None

    @field_validator("occurred_at")
    @classmethod
    def rollback_timestamp_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_pair(self) -> RollbackMetadata:
        if (self.from_version_id is None) != (self.to_version_id is None):
            raise ValueError("rollback metadata requires both source and target versions")
        if self.occurred_at is not None and self.from_version_id is None:
            raise ValueError("rollback timestamp requires rollback versions")
        return self


class AppDefinition(_Contract):
    id: str = Field(min_length=1, max_length=80)
    tenant_id: str = Field(min_length=1, max_length=80)
    app_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=260)
    description: str | None = Field(default=None, max_length=2_000)
    scope: AppScope
    status: AppLifecycleStatus = AppLifecycleStatus.DRAFT
    current_version_id: str | None = Field(default=None, max_length=80)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_update_order(self) -> AppDefinition:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.status is AppLifecycleStatus.PUBLISHED and self.current_version_id is None:
            raise ValueError("published app requires current_version_id")
        return self


_APP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


class AppVersion(_Contract):
    id: str = Field(min_length=1, max_length=80)
    app_definition_id: str = Field(min_length=1, max_length=80)
    manifest_version: str = Field(min_length=1, max_length=128)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest: Mapping[str, Any]
    requested_capabilities: tuple[RequestedCapability, ...] = ()
    status: AppLifecycleStatus = AppLifecycleStatus.DRAFT
    rollback: RollbackMetadata = Field(default_factory=RollbackMetadata)
    created_at: datetime
    published_at: datetime | None = None

    @field_validator("created_at", "published_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value

    @field_validator("manifest_version")
    @classmethod
    def manifest_version_is_semver(cls, value: str) -> str:
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError("manifest_version must be semantic version")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> AppVersion:
        if self.manifest.get("protocol") != "1.0":
            raise ValueError("manifest protocol must be 1.0")
        app_id = self.manifest.get("appId")
        if not isinstance(app_id, str) or not _APP_ID_PATTERN.fullmatch(app_id):
            raise ValueError("manifest appId is invalid")
        display_name = self.manifest.get("displayName")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("manifest displayName is required")
        if self.manifest.get("version") != self.manifest_version:
            raise ValueError("manifest version must match manifest_version")
        if not isinstance(self.manifest.get("requestedCapabilities"), list):
            raise ValueError("manifest requestedCapabilities must be an array")
        entrypoints = self.manifest.get("entrypoints")
        if not isinstance(entrypoints, Mapping) or not entrypoints:
            raise ValueError("manifest entrypoints must be a non-empty object")
        plugin = entrypoints.get("plugin")
        if isinstance(plugin, Mapping):
            artifact = plugin.get("artifact")
            if isinstance(artifact, Mapping) and artifact.get("sha256") is not None:
                if artifact["sha256"] != self.artifact_digest.removeprefix("sha256:"):
                    raise ValueError("plugin artifact sha256 must match artifact_digest")
        return self

    @model_validator(mode="after")
    def validate_publication(self) -> AppVersion:
        if self.status is AppLifecycleStatus.PUBLISHED and self.published_at is None:
            raise ValueError("published version requires published_at")
        if self.status is not AppLifecycleStatus.PUBLISHED and self.published_at is not None:
            raise ValueError("only published version may have published_at")
        return self


class AppGrant(_Contract):
    id: str = Field(min_length=1, max_length=80)
    app_definition_id: str = Field(min_length=1, max_length=80)
    app_version_id: str = Field(min_length=1, max_length=80)
    scope: AppScope
    grants: tuple[CapabilityGrant, ...] = ()
    issued_at: datetime
    expires_at: datetime | None = None

    @field_validator("issued_at", "expires_at")
    @classmethod
    def grant_timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_expiry(self) -> AppGrant:
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class AppInstance(_Contract):
    id: str = Field(min_length=1, max_length=80)
    app_definition_id: str = Field(min_length=1, max_length=80)
    app_version_id: str = Field(min_length=1, max_length=80)
    scope: AppScope
    status: AppLifecycleStatus = AppLifecycleStatus.DRAFT
    rollback: RollbackMetadata = Field(default_factory=RollbackMetadata)
    runtime_ref: str | None = Field(default=None, max_length=200)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_update_order(self) -> AppInstance:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


__all__ = [
    "AppDefinition",
    "AppGrant",
    "AppInstance",
    "AppLifecycleStatus",
    "AppScope",
    "AppScopeKind",
    "AppVersion",
    "CapabilityDecision",
    "CapabilityGrant",
    "CapabilityName",
    "GrantActor",
    "RequestedCapability",
    "RollbackMetadata",
]
