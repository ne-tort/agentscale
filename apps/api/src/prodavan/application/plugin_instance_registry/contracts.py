"""Persistence-independent contracts for the plugin-instance registry adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class PluginInstanceRegistryState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class PluginInstanceRegistryOutcome(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class PluginInstanceRegistryReason(StrEnum):
    NOT_FOUND = "plugin_instance_not_found"
    ARCHIVED = "plugin_instance_archived"
    QUARANTINED = "plugin_instance_quarantined"
    INVALID_SNAPSHOT = "invalid_plugin_instance_snapshot"
    IDENTITY_MISMATCH = "plugin_instance_identity_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    ARTIFACT_MISMATCH = "artifact_digest_mismatch"
    RUNTIME_GENERATION_MISMATCH = "runtime_generation_mismatch"
    WRITER_GENERATION_MISMATCH = "writer_generation_mismatch"


@dataclass(frozen=True, slots=True)
class PluginInstanceRegistryIdentity:
    """Complete lookup key; an instance id alone is never a valid lookup."""

    plugin_instance_id: str
    definition_id: str
    version_id: str
    tenant_id: str
    scope_kind: str
    scope_id: str
    artifact_digest: str

    @property
    def app_definition_id(self) -> str:
        return self.definition_id

    @property
    def app_version_id(self) -> str:
        return self.version_id

    def __post_init__(self) -> None:
        for name in (
            "plugin_instance_id", "definition_id", "version_id", "tenant_id",
            "scope_kind", "scope_id",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.scope_kind not in {"tenant", "cabinet", "project", "plugin_instance"}:
            raise ValueError("scope_kind must be a supported typed scope")
        if self.scope_kind == "plugin_instance" and self.scope_id != self.plugin_instance_id:
            raise ValueError("plugin_instance scope_id must match plugin_instance_id")
        if (
            not isinstance(self.artifact_digest, str)
            or not self.artifact_digest.startswith("sha256:")
            or len(self.artifact_digest) != 71
            or any(char not in "0123456789abcdef" for char in self.artifact_digest[7:])
        ):
            raise ValueError("artifact_digest must be a sha256: digest")


@dataclass(frozen=True, slots=True)
class PluginInstanceRegistryRecord:
    identity: PluginInstanceRegistryIdentity
    subject_id: str
    session_id: str
    state: PluginInstanceRegistryState
    runtime_generation: int
    writer_generation: int = 0

    @property
    def artifact_digest(self) -> str:
        return self.identity.artifact_digest

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.session_id.strip():
            raise ValueError("subject_id and session_id must not be empty")
        if self.runtime_generation < 0 or self.writer_generation < 0:
            raise ValueError("fencing generations must be non-negative")


@dataclass(frozen=True, slots=True)
class PluginInstanceRegistryQuery:
    identity: PluginInstanceRegistryIdentity
    subject_id: str | None = None
    session_id: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None

    def __post_init__(self) -> None:
        if self.subject_id is not None and not self.subject_id.strip():
            raise ValueError("subject_id must not be blank")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if self.runtime_generation is not None and self.runtime_generation < 0:
            raise ValueError("runtime_generation must be non-negative")
        if self.writer_generation is not None and self.writer_generation < 0:
            raise ValueError("writer_generation must be non-negative")


@dataclass(frozen=True, slots=True)
class PluginInstanceRegistryResolution:
    outcome: PluginInstanceRegistryOutcome
    reason: PluginInstanceRegistryReason | None = None
    record: PluginInstanceRegistryRecord | None = None
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is PluginInstanceRegistryOutcome.RESOLVED:
            if self.reason is not None or self.record is None:
                raise ValueError("resolved result requires a record and no reason")
        elif self.record is not None or self.reason is None:
            raise ValueError("negative result requires a reason and no record")


__all__ = [
    "PluginInstanceRegistryIdentity", "PluginInstanceRegistryOutcome",
    "PluginInstanceRegistryQuery", "PluginInstanceRegistryReason",
    "PluginInstanceRegistryRecord", "PluginInstanceRegistryResolution",
    "PluginInstanceRegistryState",
]
