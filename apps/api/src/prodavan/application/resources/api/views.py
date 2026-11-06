"""Contract reconstruction and view construction for the Resource API.

Two invariants live here:

1. A stored version is rebuilt as an immutable ``ResourceVersion`` contract, so
   its payload digest is re-verified on every read. A payload mutated under a
   published version therefore fails closed instead of being served.
2. A view never exposes reserved metadata, secret references or the receipt map.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from prodavan.application.migration.resources.lifecycle_contracts import (
    ResourceKind,
    ResourceLifecycle,
    ResourceVersion,
    VersionStatus,
    canonical_digest,
)

from .contracts import (
    METADATA_DESCRIPTION,
    METADATA_LIFECYCLE,
    METADATA_TARGET_PATH,
    METADATA_VERSION_NOTES,
    RESERVED_METADATA_KEYS,
    RESOURCE_VERSION_SCHEMA,
    BindingView,
    ResourceAggregate,
    ResourceProvenanceView,
    ResourceView,
    ScopeView,
    StoredBinding,
    StoredResource,
    StoredVersion,
    VersionView,
)
from .errors import ResourceApiErrorCode, fail


def public_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Strip API-owned keys so a client cannot spoof lifecycle or receipts."""
    return {
        str(key): value
        for key, value in metadata.items()
        if str(key) not in RESERVED_METADATA_KEYS
    }


def lifecycle_of(resource: StoredResource) -> ResourceLifecycle:
    """Read the lifecycle from the reserved metadata key, defaulting to draft."""
    raw = resource.metadata.get(METADATA_LIFECYCLE)
    if raw is None:
        return ResourceLifecycle.DRAFT
    try:
        return ResourceLifecycle(raw)
    except ValueError:
        # An unknown stored lifecycle is a corrupt row, not a draft.
        fail(
            ResourceApiErrorCode.RESOURCE_LIFECYCLE_FORBIDDEN,
            detail="stored resource lifecycle is not recognised",
            reason="stored_lifecycle_invalid",
        )


def description_of(resource: StoredResource) -> str:
    value = resource.metadata.get(METADATA_DESCRIPTION)
    return value if isinstance(value, str) else ""


def target_path_of(resource: StoredResource) -> str | None:
    value = resource.metadata.get(METADATA_TARGET_PATH)
    return value if isinstance(value, str) and value.strip() else None


def version_notes(metadata: Mapping[str, Any]) -> dict[str, Any]:
    value = metadata.get(METADATA_VERSION_NOTES)
    return dict(value) if isinstance(value, Mapping) else {}


def to_contract_version(version: StoredVersion) -> ResourceVersion:
    """Rebuild the immutable contract; mismatched payload digest fails closed."""
    try:
        return ResourceVersion(
            version_id=version.version_id,
            sequence=version.number,
            schema_version=RESOURCE_VERSION_SCHEMA,
            content_digest=version.content_digest,
            payload=dict(version.payload),
            resource_id=version.resource_id,
            status=version.status,
            created_at=version.created_at,
            created_by=version.created_by,
        )
    except ValueError as exc:  # digest mismatch, secret material, bad shape
        fail(
            ResourceApiErrorCode.RESOURCE_VERSION_IMMUTABLE,
            detail="stored resource version no longer matches its digest",
            reason=f"stored_version_invalid:{exc}",
        )


def provenance_of(resource: StoredResource, version: StoredVersion | None) -> ResourceProvenanceView:
    """Digest-bound, payload-free provenance for a resource root."""
    if version is None:
        return ResourceProvenanceView(
            source_type=f"resource:{resource.kind.value}",
            source_id=resource.resource_id,
            source_generation=0,
            digest=canonical_digest({"resource_id": resource.resource_id, "kind": resource.kind.value}),
            source_path=target_path_of(resource),
            captured_at=resource.created_at.isoformat() if resource.created_at else None,
        )
    asset_digest = None
    try:
        asset_digest = to_contract_version(version).asset_digest
    except Exception:  # noqa: BLE001 - provenance must not break a read
        asset_digest = None
    captured = version.created_at or resource.updated_at or resource.created_at
    return ResourceProvenanceView(
        source_type=f"resource:{resource.kind.value}",
        source_id=resource.resource_id,
        source_generation=version.number,
        digest=asset_digest or version.content_digest,
        source_revision=version.version_id,
        source_path=target_path_of(resource),
        captured_at=captured.isoformat() if captured is not None else None,
    )


def resource_view(
    resource: StoredResource,
    *,
    active: StoredVersion | None,
    aggregate: ResourceAggregate,
    replayed: bool = False,
) -> ResourceView:
    return ResourceView(
        id=resource.resource_id,
        kind=resource.kind.value,
        slug=resource.slug,
        title=resource.title,
        description=description_of(resource),
        scope=ScopeView(kind=resource.scope.kind.value, id=resource.scope.scope_id),
        lifecycle=lifecycle_of(resource).value,
        active_version=resource.active_version,
        version_numbers=aggregate.version_numbers,
        binding_count=aggregate.binding_count,
        created_at=resource.created_at.isoformat() if resource.created_at else None,
        updated_at=resource.updated_at.isoformat() if resource.updated_at else None,
        provenance=provenance_of(resource, active),
        replayed=replayed,
    )


def version_view(resource: StoredResource, version: StoredVersion, *, replayed: bool = False) -> VersionView:
    notes = version_notes(resource.metadata).get(str(version.number))
    note = notes if isinstance(notes, Mapping) else {}
    label = note.get("label")
    change_note = note.get("change_note")
    contract = to_contract_version(version)
    return VersionView(
        id=version.version_id,
        resource_id=version.resource_id,
        number=version.number,
        status=version.status.value,
        label=label if isinstance(label, str) and label else version.label,
        change_note=change_note if isinstance(change_note, str) else version.change_note,
        payload=contract.payload,
        digest=version.content_digest,
        created_at=version.created_at.isoformat() if version.created_at else None,
        created_by=version.created_by,
        replayed=replayed,
    )


def binding_view(binding: StoredBinding, *, replayed: bool = False) -> BindingView:
    return BindingView(
        id=binding.binding_id,
        resource_id=binding.resource_id,
        target=ScopeView(kind=binding.target_kind, id=binding.target_id),
        mode=binding.binding_kind.value,
        version_number=binding.version_number,
        child_may_edit=binding.child_may_edit,
        project_ids=binding.project_ids,
        materializable=binding.is_materializable,
        created_at=binding.created_at.isoformat() if binding.created_at else None,
        updated_at=binding.updated_at.isoformat() if binding.updated_at else None,
        replayed=replayed,
    )


def count_by_resource(bindings: Sequence[StoredBinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for binding in bindings:
        counts[binding.resource_id] = counts.get(binding.resource_id, 0) + 1
    return counts


def not_a_kind(value: str) -> ResourceKind:
    try:
        return ResourceKind(value)
    except ValueError:
        fail(
            ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
            detail=f"resource kind {value!r} is not supported",
            reason="resource_kind_unsupported",
        )


__all__ = [
    "binding_view",
    "count_by_resource",
    "description_of",
    "lifecycle_of",
    "not_a_kind",
    "provenance_of",
    "public_metadata",
    "resource_view",
    "target_path_of",
    "to_contract_version",
    "version_notes",
    "version_view",
]
