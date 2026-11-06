"""M05 plugin-instance/runtime fencing contracts.

The zone is deliberately persistence- and ORM-independent.  A positive write
result is evidence that every installation, runtime, caller, and fencing gate
matched at one point in time; callers must still use the returned token for CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PluginInstanceState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class PluginInstanceDecisionReason(StrEnum):
    ALLOWED = "allowed"
    NOT_FOUND = "plugin_instance_not_found"
    IDENTITY_MISMATCH = "plugin_instance_identity_mismatch"
    DEFINITION_MISMATCH = "definition_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    TENANT_MISMATCH = "tenant_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    DIGEST_MISMATCH = "artifact_digest_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    ARCHIVED = "plugin_instance_archived"
    QUARANTINED = "plugin_instance_quarantined"
    RUNTIME_GENERATION_STALE = "runtime_generation_stale"
    WRITER_GENERATION_STALE = "writer_generation_stale"
    LEASE_MISSING = "lease_missing"
    LEASE_EXPIRED = "lease_expired"
    LEASE_OWNER_MISMATCH = "lease_owner_mismatch"
    LEASE_ID_MISMATCH = "lease_id_mismatch"
    LEASE_CAS_CONFLICT = "lease_cas_conflict"
    WRITER_CAS_CONFLICT = "writer_cas_conflict"


@dataclass(frozen=True, slots=True)
class PluginInstanceIdentity:
    """The complete binding key; plugin_instance_id alone is never sufficient."""

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
        for name in ("plugin_instance_id", "definition_id", "version_id", "tenant_id", "scope_kind", "scope_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.artifact_digest.startswith("sha256:") or len(self.artifact_digest) != 71:
            raise ValueError("artifact_digest must be a sha256: digest")


@dataclass(frozen=True, slots=True)
class FencingToken:
    runtime_generation: int
    writer_generation: int

    def __post_init__(self) -> None:
        if self.runtime_generation < 0 or self.writer_generation < 0:
            raise ValueError("fencing generations must be non-negative")


@dataclass(frozen=True, slots=True)
class PluginInstanceRecord:
    identity: PluginInstanceIdentity
    subject_id: str
    session_id: str
    state: PluginInstanceState
    fencing: FencingToken
    lease_owner: str | None = None
    lease_id: str | None = None
    lease_until: datetime | None = None

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.session_id.strip():
            raise ValueError("subject_id and session_id must not be empty")
        if self.lease_until is not None:
            if not self.lease_owner:
                raise ValueError("lease owner is required when lease_until is set")
            if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
                raise ValueError("lease_until must be timezone-aware")
        if self.lease_owner is not None and not self.lease_owner.strip():
            raise ValueError("lease_owner must not be blank")


@dataclass(frozen=True, slots=True)
class PluginInstanceWriteRequest:
    identity: PluginInstanceIdentity
    subject_id: str
    session_id: str
    fencing: FencingToken
    lease_owner: str
    lease_id: str
    now: datetime

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.session_id.strip() or not self.lease_owner.strip() or not self.lease_id.strip():
            raise ValueError("subject_id, session_id, lease_owner and lease_id must not be empty")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PositiveWriteDecision:
    allowed: bool
    reason: PluginInstanceDecisionReason
    record: PluginInstanceRecord | None = None

    def __post_init__(self) -> None:
        if self.allowed:
            if self.reason is not PluginInstanceDecisionReason.ALLOWED or self.record is None:
                raise ValueError("positive decision requires an allowed record")
        elif self.record is not None:
            raise ValueError("negative decision must not expose a positive record")


@dataclass(frozen=True, slots=True)
class LeaseCasRequest:
    identity: PluginInstanceIdentity
    subject_id: str
    session_id: str
    expected: FencingToken
    lease_owner: str
    lease_id: str
    lease_until: datetime

    @property
    def plugin_instance_id(self) -> str:
        return self.identity.plugin_instance_id

    @property
    def owner_id(self) -> str:
        return self.lease_owner

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.session_id.strip():
            raise ValueError("subject_id and session_id are required")
        if not self.lease_owner.strip() or not self.lease_id.strip():
            raise ValueError("lease_owner and lease_id are required")
        if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise ValueError("lease_until must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LeaseCasDecision:
    acquired: bool
    reason: PluginInstanceDecisionReason
    record: PluginInstanceRecord | None = None

    def __post_init__(self) -> None:
        if self.acquired and (self.reason is not PluginInstanceDecisionReason.ALLOWED or self.record is None):
            raise ValueError("acquired lease requires an allowed record")
        if not self.acquired and self.record is not None:
            raise ValueError("failed lease CAS must not expose a positive record")


@dataclass(frozen=True, slots=True)
class WriterCasRequest:
    identity: PluginInstanceIdentity
    subject_id: str
    session_id: str
    expected: FencingToken
    lease_owner: str
    lease_id: str
    lease_until: datetime

    @property
    def plugin_instance_id(self) -> str:
        return self.identity.plugin_instance_id

    @property
    def owner_id(self) -> str:
        return self.lease_owner

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.session_id.strip():
            raise ValueError("subject_id and session_id are required")
        if not self.lease_owner.strip() or not self.lease_id.strip():
            raise ValueError("lease_owner and lease_id are required")
        if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise ValueError("lease_until must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OwnerCasDecision:
    """Explicit result of an owner/generation compare-and-set operation."""

    changed: bool
    reason: PluginInstanceDecisionReason
    record: PluginInstanceRecord | None = None

    def __post_init__(self) -> None:
        if self.changed and (self.reason is not PluginInstanceDecisionReason.ALLOWED or self.record is None):
            raise ValueError("successful owner CAS requires an allowed record")
        if not self.changed and self.record is not None:
            raise ValueError("failed owner CAS must not expose a positive record")


__all__ = [
    "FencingToken", "LeaseCasDecision", "LeaseCasRequest", "OwnerCasDecision",
    "PluginInstanceDecisionReason", "PluginInstanceIdentity", "PluginInstanceRecord",
    "PluginInstanceState", "PluginInstanceWriteRequest", "PositiveWriteDecision", "WriterCasRequest",
]
