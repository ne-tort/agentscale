"""Зона артефактов плагинов (F4): OCI/cosign, compatibility gate, реестр.

Публичный контракт зоны. Все отказы — fail-closed: ``QUARANTINED`` +
``QuarantineEvidence``, публикация без проверенного digest невозможна.
"""

from .errors import ArtifactError, ArtifactErrorCode
from .layers import (
    BACKEND_LAYER_PATH,
    EXPECTED_LAYER_COUNT,
    FRONTEND_LAYER_PATH,
    MIGRATIONS_LAYER_PATH,
    SELF_REFERENCE_KEY,
    build_oci_manifest,
)
from .oci import (
    BACKEND_MEDIA_TYPE,
    FORBIDDEN_TAGS,
    FRONTEND_MEDIA_TYPE,
    MIGRATIONS_MEDIA_TYPE,
    PLUGIN_PROTOCOL,
    OciArtifactRef,
    is_valid_digest,
    parse_oci_ref,
)
from .registry import (
    COMPATIBILITY_ERROR_CODE,
    DIGEST_CONFLICT_ERROR_CODE,
    InMemoryRegistryStore,
    PluginArtifactRecord,
    PluginArtifactRegistry,
    RegistryStorePort,
)
from .verification import (
    PLATFORM_VERSION,
    ArtifactVerifier,
    CapabilityGrantLike,
    CompatibilityGate,
    CompatibilityVerdict,
    CosignPort,
    ManifestValidatorPort,
    VerificationEvidence,
    VerificationOutcome,
    VerificationResult,
    VerificationStage,
    manifest_fingerprint,
)

# Удобный список кодов для CI-gate/логов (не копируем enum, а раскрываем его).
ARTIFACT_ERROR_CODES: tuple[str, ...] = tuple(code.value for code in ArtifactErrorCode)

__all__ = [
    "ARTIFACT_ERROR_CODES",
    "BACKEND_LAYER_PATH",
    "BACKEND_MEDIA_TYPE",
    "COMPATIBILITY_ERROR_CODE",
    "DIGEST_CONFLICT_ERROR_CODE",
    "EXPECTED_LAYER_COUNT",
    "FORBIDDEN_TAGS",
    "FRONTEND_LAYER_PATH",
    "FRONTEND_MEDIA_TYPE",
    "MIGRATIONS_LAYER_PATH",
    "MIGRATIONS_MEDIA_TYPE",
    "PLATFORM_VERSION",
    "PLUGIN_PROTOCOL",
    "SELF_REFERENCE_KEY",
    "ArtifactError",
    "ArtifactErrorCode",
    "ArtifactVerifier",
    "CapabilityGrantLike",
    "CompatibilityGate",
    "CompatibilityVerdict",
    "CosignPort",
    "InMemoryRegistryStore",
    "ManifestValidatorPort",
    "OciArtifactRef",
    "PluginArtifactRecord",
    "PluginArtifactRegistry",
    "RegistryStorePort",
    "VerificationEvidence",
    "VerificationOutcome",
    "VerificationResult",
    "VerificationStage",
    "build_oci_manifest",
    "is_valid_digest",
    "manifest_fingerprint",
    "parse_oci_ref",
]
