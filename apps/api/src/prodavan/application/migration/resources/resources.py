"""Compatibility facade for the MIG-RES-01 resource lifecycle."""
from .lifecycle_algorithms import (
    ResourceImportCoordinator,
    import_compatibility_candidate,
    resolve_profile,
    resolve_resource,
    select_deterministic_profile,
)
from .lifecycle_contracts import (
    AssetReference,
    Binding,
    BindingKind,
    BindingState,
    CompatibilityCandidate, ContentResource,
    ImportCheckpoint, ImportOutcome, ImportRecord, ProfileCandidate, ProfileSelection,
    ProjectSelectionPolicy, Resource, ResourceBinding, ResourceImportContext,
    ResourceImportPage, ResourceImportPartition, ResourceKind, ResourceLifecycle,
    ResourceProvenance, ResourceResolution, ResourceScope, ResourceSnapshot,
    ResourceVersion, ScopeKind, ScopeProof, SelectionPolicy, SecretReference,
    ResourceImportSourcePort, ResourceImportScopePort, ResourceProfilePort,
    ResumableResourceImportPort,
    TrustedResourceImportPort, TrustedResourceScopePort, TrustedResourceSnapshotPort,
    VersionStatus, canonical_digest,
)
from .lifecycle_validation import (
    candidate_asset_digests, candidate_canonical_digest, validate_candidate,
    validate_resource_payload,
)

__all__ = [
    "AssetReference", "Binding", "BindingKind", "BindingState", "CompatibilityCandidate",
    "ContentResource", "ImportCheckpoint", "ImportOutcome", "ImportRecord",
    "ProfileCandidate", "ProfileSelection", "ProjectSelectionPolicy", "Resource",
    "ResourceBinding", "ResourceImportContext", "ResourceImportCoordinator",
    "ResourceImportPage", "ResourceImportPartition", "ResourceKind", "ResourceLifecycle",
    "ResourceProvenance", "ResourceResolution", "ResourceScope", "ResourceSnapshot",
    "ResourceVersion", "ScopeKind", "ScopeProof", "SelectionPolicy", "SecretReference",
    "ResourceImportSourcePort", "ResourceImportScopePort", "ResourceProfilePort",
    "ResumableResourceImportPort",
    "TrustedResourceImportPort", "TrustedResourceScopePort", "TrustedResourceSnapshotPort",
    "VersionStatus", "candidate_asset_digests", "candidate_canonical_digest", "canonical_digest",
    "import_compatibility_candidate", "resolve_profile", "resolve_resource",
    "select_deterministic_profile", "validate_candidate", "validate_resource_payload",
]
