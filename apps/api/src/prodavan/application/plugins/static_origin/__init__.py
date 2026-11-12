"""Static evidence application zone (Scope B1, cluster-internal).

Read-only digest verification of published plugin frontend bundles via the
shared GHCR loader composition. No Ingress surface, no write API, no tenant
MinIO/DB/RBAC: composition lives in ``prodavan.main_plugin_static``.
"""

from .service import (
    BundleEvidence,
    PluginStaticEvidenceService,
    StaticEvidenceArtifactLoader,
    StaticEvidenceConfig,
    StaticEvidenceConfigError,
    StaticEvidenceError,
    StaticEvidenceErrorCode,
)

__all__ = [
    "BundleEvidence",
    "PluginStaticEvidenceService",
    "StaticEvidenceArtifactLoader",
    "StaticEvidenceConfig",
    "StaticEvidenceConfigError",
    "StaticEvidenceError",
    "StaticEvidenceErrorCode",
]
