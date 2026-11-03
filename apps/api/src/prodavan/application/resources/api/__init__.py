"""Canonical Resource API application surface (MIG-RESOURCE-API-01).

``api/v1/resources.py`` is the only HTTP entry point. It composes three
application services with server-resolved tenant scope, a tenant-scoped store
port and the reused materialization resolver/CAS boundary. Nothing here writes
SQLAlchemy directly and nothing here re-implements lifecycle or planning.
"""

from .commands import (
    ResourceCommandService,
    build_version,
    normalize_slug,
    operation_digest,
    require_idempotency_key,
    require_kind,
    validate_payload,
)
from .contracts import (
    METADATA_LIFECYCLE,
    RESOURCE_KINDS,
    BindingView,
    MaterializationApplyView,
    MaterializationEntryView,
    MaterializationPlanView,
    NewVersion,
    ResolvedTenant,
    ResourceAggregate,
    ResourceProvenanceView,
    ResourceView,
    ScopeRef,
    ScopeView,
    StoredBinding,
    StoredResource,
    StoredVersion,
    VersionView,
)
from .errors import ResourceApiError, ResourceApiErrorCode, StaleResourceState, StoreConflict
from .materialization import PlannedSelection, ResourceMaterializationService, StoredPlan
from .ports import ProjectProof, ResourceScopeProofPort, ResourceStorePort
from .scope import ResolvedTarget, ResourceScopeService

__all__ = [
    "METADATA_LIFECYCLE",
    "RESOURCE_KINDS",
    "BindingView",
    "MaterializationApplyView",
    "MaterializationEntryView",
    "MaterializationPlanView",
    "NewVersion",
    "PlannedSelection",
    "ProjectProof",
    "ResolvedTarget",
    "ResolvedTenant",
    "ResourceAggregate",
    "ResourceApiError",
    "ResourceApiErrorCode",
    "ResourceCommandService",
    "ResourceMaterializationService",
    "ResourceProvenanceView",
    "ResourceScopeProofPort",
    "ResourceScopeService",
    "ResourceStorePort",
    "ResourceView",
    "ScopeRef",
    "ScopeView",
    "StaleResourceState",
    "StoredBinding",
    "StoredPlan",
    "StoredResource",
    "StoredVersion",
    "StoreConflict",
    "VersionView",
    "build_version",
    "normalize_slug",
    "operation_digest",
    "require_idempotency_key",
    "require_kind",
    "validate_payload",
]
