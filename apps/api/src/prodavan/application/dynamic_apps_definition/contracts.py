"""Contracts for the M04.e dynamic-app definition/version adapter.

The adapter is deliberately resolution-only: it can prove an immutable definition/version
pair for a tenant, but it cannot create or mutate registry, grant, or instance rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from prodavan.domain.dynamic_apps import AppDefinition, AppLifecycleStatus, AppVersion


class DefinitionResolutionOutcome(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"


class DefinitionResolutionReason(StrEnum):
    MISSING_DEFINITION = "missing_definition"
    MISSING_VERSION = "missing_version"
    CROSS_TENANT_DEFINITION = "cross_tenant_definition"
    CROSS_APP_DEFINITION = "cross_app_definition"
    CROSS_DEFINITION_VERSION = "cross_definition_version"
    AMBIGUOUS_VERSION = "ambiguous_version"
    INVALID_DEFINITION_HISTORY = "invalid_definition_history"
    INVALID_VERSION_HISTORY = "invalid_version_history"
    ARTIFACT_DIGEST_CONFLICT = "artifact_digest_conflict"
    PUBLISHED_VERSION_MISMATCH = "published_version_mismatch"
    DISALLOWED_VERSION_STATUS = "disallowed_version_status"


@dataclass(frozen=True, slots=True)
class DefinitionVersionRef:
    """Resolved identity and immutable artifact proof for downstream adapters."""

    tenant_id: str
    app_id: str
    app_definition_id: str
    app_version_id: str
    manifest_version: str
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class DefinitionResolution:
    """Fail-closed result; only RESOLVED contains a usable positive reference."""

    outcome: DefinitionResolutionOutcome
    reason: DefinitionResolutionReason | None = None
    reference: DefinitionVersionRef | None = None
    definition: AppDefinition | None = None
    version: AppVersion | None = None
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is DefinitionResolutionOutcome.RESOLVED:
            if self.reason is not None or self.reference is None:
                raise ValueError("resolved result requires reference and no reason")
            if self.definition is None or self.version is None:
                raise ValueError("resolved result requires definition and version")
        elif self.reference is not None or self.definition is not None or self.version is not None:
            raise ValueError("negative result must not expose a positive definition/version")
        if self.outcome is not DefinitionResolutionOutcome.RESOLVED and self.reason is None:
            raise ValueError("negative result requires reason")


@dataclass(frozen=True, slots=True)
class DefinitionVersionQuery:
    tenant_id: str
    app_id: str
    manifest_version: str
    expected_artifact_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.app_id.strip() or not self.manifest_version.strip():
            raise ValueError("tenant_id, app_id and manifest_version must not be empty")
        if self.expected_artifact_digest is not None and not self.expected_artifact_digest.startswith("sha256:"):
            raise ValueError("expected_artifact_digest must be sha256-prefixed")


__all__ = [
    "DefinitionResolution",
    "DefinitionResolutionOutcome",
    "DefinitionResolutionReason",
    "DefinitionVersionQuery",
    "DefinitionVersionRef",
]
