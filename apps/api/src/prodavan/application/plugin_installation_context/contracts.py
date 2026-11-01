"""Cross-domain plugin installation anchor contracts.

This package is the read/admission boundary between typed owner resolution,
M05 runtime fencing, and M06 grant authorization.  It deliberately contains
no persistence or mutation primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InstallationAdmissionReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_CONTEXT = "invalid_context"
    ANCHOR_MISMATCH = "anchor_mismatch"
    INSTALLATION_NOT_FOUND = "installation_not_found"
    INSTALLATION_INACTIVE = "installation_inactive"
    RUNTIME_FENCE_DENIED = "runtime_fence_denied"


@dataclass(frozen=True, slots=True)
class PluginInstallationContext:
    """Complete server-resolved identity for one plugin installation call."""

    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    scope_kind: str
    scope_id: str
    plugin_instance_id: str
    definition_id: str
    version_id: str
    artifact_digest: str
    subject_id: str
    session_id: str
    runtime_generation: int
    writer_generation: int
    lease_owner: str
    lease_id: str
    observed_at: datetime

    def __post_init__(self) -> None:
        required = (
            "tenant_id", "scope_kind", "scope_id", "plugin_instance_id",
            "definition_id", "version_id", "artifact_digest", "subject_id",
            "session_id", "lease_owner", "lease_id",
        )
        if any(not getattr(self, name).strip() for name in required):
            raise ValueError("installation context identity fields must not be blank")
        if not self.artifact_digest.startswith("sha256:") or len(self.artifact_digest) != 71:
            raise ValueError("artifact_digest must be a sha256: digest")
        if self.runtime_generation < 0 or self.writer_generation < 0:
            raise ValueError("runtime and writer generations must be non-negative")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.scope_kind == "tenant":
            if self.scope_id != self.tenant_id:
                raise ValueError("tenant scope must use tenant_id as scope_id")
            if any(value is not None for value in (self.cabinet_id, self.project_id)):
                raise ValueError("tenant scope must not carry cabinet or project anchors")
        if self.scope_kind == "cabinet" and (
            self.cabinet_id is None
            or self.scope_id != self.cabinet_id
            or self.project_id is not None
        ):
            raise ValueError("cabinet scope requires matching cabinet and no project anchor")
        if self.scope_kind == "project" and (
            self.project_id is None
            or self.cabinet_id is None
            or self.scope_id != self.project_id
        ):
            raise ValueError("project scope requires matching project and cabinet anchors")
        if self.scope_kind == "plugin_instance":
            if self.scope_id != self.plugin_instance_id:
                raise ValueError("plugin_instance scope must use plugin_instance_id as scope_id")
            if self.project_id is not None and self.cabinet_id is None:
                raise ValueError("plugin_instance scope with project anchor requires cabinet_id")
        if self.scope_kind not in {"tenant", "cabinet", "project", "plugin_instance"}:
            raise ValueError("unsupported typed installation scope")

    @property
    def instance_key(self) -> tuple[str, str, str, str, str, str]:
        return (self.plugin_instance_id, self.definition_id, self.version_id,
                self.tenant_id, self.scope_kind, self.scope_id)

    @property
    def fencing_key(self) -> tuple[str, str, int, int]:
        return (self.plugin_instance_id, self.session_id,
                self.runtime_generation, self.writer_generation)


@dataclass(frozen=True, slots=True)
class InstallationAdmissionRequest:
    context: PluginInstallationContext
    asserted_tenant_id: str | None = None
    asserted_cabinet_id: str | None = None
    asserted_project_id: str | None = None
    asserted_plugin_instance_id: str | None = None


@dataclass(frozen=True, slots=True)
class M05CasContext:
    """Exact handoff to runtime fencing; it carries no client assertions."""

    installation: PluginInstallationContext


@dataclass(frozen=True, slots=True)
class M06GrantHandoff:
    """Exact handoff to M06 grant lookup/gateway after M05 admission."""

    installation: PluginInstallationContext
    runtime_admitted: bool

    def __post_init__(self) -> None:
        if not self.runtime_admitted:
            raise ValueError("M06 handoff requires successful M05 runtime admission")


@dataclass(frozen=True, slots=True)
class InstallationAdmissionDecision:
    admitted: bool
    reason: InstallationAdmissionReason
    m05: M05CasContext | None = None
    m06: M06GrantHandoff | None = None

    def __post_init__(self) -> None:
        if self.admitted != (self.m05 is not None and self.m06 is not None):
            raise ValueError("admission handoffs must match admitted state")
        if not self.admitted and (self.m05 is not None or self.m06 is not None):
            raise ValueError("denied admission must not expose handoffs")
        if self.admitted and self.reason is not InstallationAdmissionReason.ALLOWED:
            raise ValueError("admitted decision must be allowed")


__all__ = [
    "InstallationAdmissionDecision", "InstallationAdmissionReason",
    "InstallationAdmissionRequest", "M05CasContext", "M06GrantHandoff",
    "PluginInstallationContext",
]
