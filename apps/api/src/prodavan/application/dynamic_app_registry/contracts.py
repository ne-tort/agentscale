"""Persistence-neutral contracts for the dynamic app registry lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from prodavan.domain.dynamic_apps import AppDefinition, AppGrant, AppLifecycleStatus, AppVersion


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


__all__ = [
    "DraftCommand", "LifecycleCommand", "PublishCommand", "QuarantineEvidence",
    "RegistryOutcome", "RegistryReason", "RegistryResult", "RollbackCommand",
]
