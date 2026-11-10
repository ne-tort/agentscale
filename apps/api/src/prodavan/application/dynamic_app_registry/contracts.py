"""Persistence-neutral contracts for the dynamic app registry lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from prodavan.domain.dynamic_apps import AppDefinition, AppGrant, AppLifecycleStatus, AppVersion


class RollbackConflictError(RuntimeError):
    """Typed rollback replay/current/semantic conflict shared by store implementations."""


class RegistryOutcome(StrEnum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    MISSING = "missing"


class RegistryReason(StrEnum):
    DEFINITION_MISSING = "definition_missing"
    VERSION_MISSING = "version_missing"
    INVALID_TRANSITION = "invalid_transition"
    VERSION_DEFINITION_MISMATCH = "version_definition_mismatch"
    ARTIFACT_DIGEST_CONFLICT = "artifact_digest_conflict"
    CURRENT_VERSION_CONFLICT = "current_version_conflict"
    VERSION_ALREADY_PUBLISHED = "version_already_published"
    GRANT_PROJECTION_CONFLICT = "grant_projection_conflict"
    UNSUPPORTED_GRANT = "unsupported_grant"
    CROSS_TENANT = "cross_tenant"
    ROLLBACK_TARGET_INVALID = "rollback_target_invalid"


@dataclass(frozen=True, slots=True)
class QuarantineEvidence:
    """Redacted evidence that must never be consumed as authorization input."""

    reason: RegistryReason
    source_kind: str
    source_id: str
    source_digest: str
    observed_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegistryResult:
    outcome: RegistryOutcome
    reason: RegistryReason | None = None
    definition: AppDefinition | None = None
    version: AppVersion | None = None
    projected_grants: tuple[AppGrant, ...] = ()
    quarantine: QuarantineEvidence | None = None

    def __post_init__(self) -> None:
        positive = self.outcome is RegistryOutcome.APPLIED
        if positive and (self.reason is not None or self.quarantine is not None):
            raise ValueError("applied result cannot carry a reason or quarantine")
        if self.outcome is RegistryOutcome.QUARANTINED and self.quarantine is None:
            raise ValueError("quarantined result requires evidence")
        if self.outcome is not RegistryOutcome.APPLIED and self.projected_grants:
            raise ValueError("negative result cannot expose projected grants")


@dataclass(frozen=True, slots=True)
class DraftCommand:
    definition: AppDefinition
    version: AppVersion
    actor: str
    expected_artifact_digest: str | None = None


@dataclass(frozen=True, slots=True)
class PublishCommand:
    definition_id: str
    version_id: str
    actor: str
    expected_current_version_id: str | None = None
    expected_artifact_digest: str | None = None
    grants: tuple[AppGrant, ...] = ()


@dataclass(frozen=True, slots=True)
class LifecycleCommand:
    definition_id: str
    version_id: str
    actor: str
    expected_current_version_id: str | None = None
    expected_artifact_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RollbackCommand:
    definition_id: str
    target_version_id: str
    actor: str
    reason: str
    expected_current_version_id: str | None = None
    expected_artifact_digest: str | None = None
    expected_current_lifecycle_version: int | None = None

    def __post_init__(self) -> None:
        if self.expected_current_lifecycle_version is not None and (
            not isinstance(self.expected_current_lifecycle_version, int)
            or isinstance(self.expected_current_lifecycle_version, bool)
            or self.expected_current_lifecycle_version < 0
        ):
            raise ValueError("expected_current_lifecycle_version must be a non-negative int")


def rollback_command_fingerprint(command: RollbackCommand) -> str:
    """Return a deterministic, redacted fingerprint of every command field."""
    if not isinstance(command, RollbackCommand):
        raise TypeError("rollback_command_fingerprint expects RollbackCommand")
    payload = {
        "definition_id": command.definition_id,
        "target_version_id": command.target_version_id,
        "actor": command.actor,
        "reason": command.reason,
        "expected_current_version_id": command.expected_current_version_id,
        "expected_artifact_digest": command.expected_artifact_digest,
        "expected_current_lifecycle_version": command.expected_current_lifecycle_version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "DraftCommand", "LifecycleCommand", "PublishCommand", "QuarantineEvidence",
    "RegistryOutcome", "RegistryReason", "RegistryResult", "RollbackCommand",
    "RollbackConflictError", "rollback_command_fingerprint",
]
