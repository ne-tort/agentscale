"""Доменные контракты плагинов (F0) — публичные экспорты.

Источник истины: docs/ARCHITECTURE/plugin-architecture-complete.md (v4).
Валидация манифеста — :mod:`prodavan.domain.plugins.validation`.
"""

from __future__ import annotations

from prodavan.domain.plugins.types import (
    ARTIFACT_DIGEST_PATTERN,
    CAPABILITY_ALLOWLIST,
    EGRESS_REF_PATTERN,
    EMBEDDED_EGRESS_FIELDS,
    PATH_PATTERN,
    PLUGIN_ID_PATTERN,
    PROTOCOL_VERSION,
    RELATIVE_PATH_PATTERN,
    SEMVER_PATTERN,
    SELF_REFERENCE_FIELDS,
    ArtifactDigest,
    BackendEntrypoint,
    Capability,
    CapabilityGrant,
    DecisionCode,
    EgressMode,
    EgressPolicy,
    FrontendEntrypoint,
    GatewayDecision,
    MigrationEngine,
    MigrationsDeclaration,
    PluginClaims,
    PluginInstance,
    PluginInstanceStatus,
    PluginManifest,
    QuotaRef,
    RegistryOutcome,
    RegistryResult,
)
from prodavan.domain.plugins.validation import (
    ManifestValidationErrorCode,
    PluginValidationError,
    load_schema,
    validate_manifest,
)

__all__ = [
    "ARTIFACT_DIGEST_PATTERN",
    "CAPABILITY_ALLOWLIST",
    "EGRESS_REF_PATTERN",
    "EMBEDDED_EGRESS_FIELDS",
    "PATH_PATTERN",
    "PLUGIN_ID_PATTERN",
    "PROTOCOL_VERSION",
    "RELATIVE_PATH_PATTERN",
    "SEMVER_PATTERN",
    "SELF_REFERENCE_FIELDS",
    "ArtifactDigest",
    "BackendEntrypoint",
    "Capability",
    "CapabilityGrant",
    "DecisionCode",
    "EgressMode",
    "EgressPolicy",
    "FrontendEntrypoint",
    "GatewayDecision",
    "ManifestValidationErrorCode",
    "MigrationEngine",
    "MigrationsDeclaration",
    "PluginClaims",
    "PluginInstance",
    "PluginInstanceStatus",
    "PluginManifest",
    "PluginValidationError",
    "QuotaRef",
    "RegistryOutcome",
    "RegistryResult",
    "load_schema",
    "validate_manifest",
]

