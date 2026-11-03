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
from .composition import (
    ResourceApiBackend,
    ResourceApiPorts,
    assemble_resource_api,
    build_resource_api,
    install_resource_api_ports,
    resource_api_port_factory,
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
from .ports import (
    ApplyReceipt,
    ProjectProof,
    ResourceApplyReceiptPort,
    ResourceScopeProofPort,
    ResourceStorePort,
)
from .scope import ResolvedTarget, ResourceScopeService

__all__ = [
    "METADATA_LIFECYCLE",
    "RESOURCE_KINDS",
    "ApplyReceipt",
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
    "ResourceApiBackend",
    "ResourceApiError",
    "ResourceApiErrorCode",
    "ResourceApiPorts",
    "ResourceApplyReceiptPort",
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
    "assemble_resource_api",
    "build_resource_api",
    "build_version",
    "install_resource_api_ports",
    "normalize_slug",
    "operation_digest",
    "require_idempotency_key",
    "require_kind",
    "resource_api_port_factory",
    "validate_payload",
]
