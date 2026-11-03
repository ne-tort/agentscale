"""Commands for the canonical Resource API.

Lifecycle transitions are **not** re-implemented here. Every state change goes
through the immutable ``MIG-RES-01`` contracts:

* ``Resource.publish_version`` / ``archive`` decide whether a transition is
  legal and what the version status projection becomes;
* ``ResourceVersion`` re-derives the content digest and rejects secret material;
* ``Binding.policy_errors`` classifies binding policy before persistence.

The service supplies only what those contracts cannot do on their own:
server-resolved scope, durable idempotency, and compare-and-set against the
observed active version.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from prodavan.application.migration.resources.lifecycle_contracts import (
    Binding,
    BindingKind,
    BindingState,
    ProjectSelectionPolicy,
    Resource,
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    ScopeKind,
    canonical_digest,
    safe_workspace_path,
)
from prodavan.application.migration.resources.lifecycle_validation import (
    validate_resource_payload,
)

from . import views
from .contracts import (
    METADATA_DESCRIPTION,
    METADATA_LIFECYCLE,
    METADATA_TARGET_PATH,
    METADATA_VERSION_NOTES,
    RESOURCE_KINDS,
    BindingView,
    NewVersion,
    ResolvedTenant,
    ResourceAggregate,
    ResourceView,
    ScopeRef,
    StoredBinding,
    StoredResource,
    StoredVersion,
    VersionView,
)
from .errors import ResourceApiErrorCode, StaleResourceState, StoreConflict, fail
from .ports import ResourceScopeProofPort, ResourceStorePort
from .scope import ResolvedTarget, ResourceScopeService

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,158}[a-z0-9])?$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:~+/-]{1,200}$")
_TITLE_LIMIT = 260
_DESCRIPTION_LIMIT = 4000
_LABEL_LIMIT = 200
_NOTE_LIMIT = 2000


def operation_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_idempotency_key(value: str | None) -> str:
    """Validate the ``Idempotency-Key`` header; an empty key is rejected."""
    key = (value or "").strip()
    if not key or not _IDEMPOTENCY_KEY.fullmatch(key):
        fail(
            ResourceApiErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            detail="Idempotency-Key must contain 1 to 200 URL-safe characters",
            reason="idempotency_key_malformed",
        )
    return key


def normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not _SLUG.fullmatch(slug):
        fail(
            ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
            detail="slug must be lower-case alphanumeric with '._-' separators",
            reason="slug_invalid",
        )
    return slug


def _bounded(value: str, limit: int) -> str:
    if len(value) > limit:
        fail(
            ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
            detail=f"value exceeds {limit} characters",
            reason="value_too_long",
        )
    return value


def require_kind(value: str) -> ResourceKind:
    """Only the canonical prompt/file/mcp kinds are creatable through this API."""
    if value not in RESOURCE_KINDS:
        fail(
            ResourceApiErrorCode.RESOURCE_SCOPE_UNSUPPORTED,
            detail=f"resource kind {value!r} is not part of the canonical resource API",
            reason="resource_kind_unsupported",
        )
    return ResourceKind(value)


def validate_payload(kind: ResourceKind, payload: Mapping[str, Any]) -> None:
    """Reject a payload the migration contract could not materialize."""
    if not isinstance(payload, Mapping) or not payload:
        fail(
            ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
            detail="payload must be a non-empty object",
            reason="payload_empty",
        )
    reasons = validate_resource_payload(kind, dict(payload))
    if reasons:
        fail(
            ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
            detail="resource payload failed lifecycle validation",
            reason=reasons[0],
            extra={"reasons": list(reasons)},
        )


def build_version(payload: Mapping[str, Any], *, label: str = "", change_note: str = "") -> NewVersion:
    """Create the immutable payload identity; the digest is derived here."""
    return NewVersion(
        payload=dict(payload),
        content_digest=canonical_digest(dict(payload)),
        label=_bounded(label.strip(), _LABEL_LIMIT),
        change_note=_bounded(change_note.strip(), _NOTE_LIMIT),
    )


def per_binding_path_unsupported(workspace_path: str | None) -> bool:
    """A per-binding path cannot be persisted, so it is never silently dropped."""
    return workspace_path is not None and workspace_path.strip() != ""


@dataclass(slots=True)
class ResourceCommandService:
    """Read and write commands for resource roots, versions and bindings."""

    store: ResourceStorePort
    scopes: ResourceScopeService
    proofs: ResourceScopeProofPort | None = None

    # -- read -----------------------------------------------------------

    async def list_resources(
        self,
        tenant: ResolvedTenant,
        *,
        kind: str | None = None,
        lifecycle: str | None = None,
        limit: int = 50,
        after_id: str | None = None,
    ) -> tuple[ResourceView, ...]:
        kind_value = require_kind(kind) if kind is not None else None
        lifecycle_value: ResourceLifecycle | None = None
        if lifecycle is not None:
            try:
                lifecycle_value = ResourceLifecycle(lifecycle)
            except ValueError:
                fail(
                    ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
                    detail=f"lifecycle filter {lifecycle!r} is not recognised",
                    reason="lifecycle_filter_unsupported",
                )
        rows = await self.store.list_resources(
            tenant_id=tenant.tenant_id,
            kind=kind_value,
            lifecycle=lifecycle_value,
            limit=max(1, min(limit, 200)),
            after_id=after_id,
        )
        aggregates = await self.store.aggregates(
            tenant_id=tenant.tenant_id, resource_ids=[row.resource_id for row in rows]
        )
        out: list[ResourceView] = []
        for row in rows:
            out.append(
                views.resource_view(
                    row,
                    active=await self._active_version(row),
                    aggregate=aggregates.get(row.resource_id, ResourceAggregate()),
                )
            )
        return tuple(out)

    async def get_resource(self, tenant: ResolvedTenant, resource_id: str) -> ResourceView:
        row, aggregate, active = await self._load(tenant, resource_id)
        return views.resource_view(row, active=active, aggregate=aggregate)

    async def list_versions(
        self, tenant: ResolvedTenant, resource_id: str
    ) -> tuple[VersionView, ...]:
        row = await self._require(tenant, resource_id)
        versions = await self.store.list_versions(resource_id=row.resource_id)
        return tuple(views.version_view(row, version) for version in versions)

    async def get_version(
        self, tenant: ResolvedTenant, resource_id: str, number: int
    ) -> VersionView:
        row = await self._require(tenant, resource_id)
        version = await self.store.get_version(resource_id=row.resource_id, number=number)
        if version is None:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="resource version does not exist",
                reason="version_not_found",
            )
        return views.version_view(row, version)

    async def list_bindings(
        self, tenant: ResolvedTenant, resource_id: str
    ) -> tuple[BindingView, ...]:
        row = await self._require(tenant, resource_id)
        bindings = await self.store.list_bindings(
            tenant_id=tenant.tenant_id, resource_id=row.resource_id
        )
        return tuple(views.binding_view(binding) for binding in bindings)

    async def list_project_bindings(
        self, tenant: ResolvedTenant, project_id: str
    ) -> tuple[BindingView, ...]:
        await self.scopes.require_project(
            tenant, project_id, reason="cross_tenant_project_scope"
        )
        bindings = await self.store.list_project_bindings(
            tenant_id=tenant.tenant_id, project_id=project_id
        )
        visible = await self.store.aggregates(
            tenant_id=tenant.tenant_id,
            resource_ids=sorted({binding.resource_id for binding in bindings}),
        )
        return tuple(
            views.binding_view(binding)
            for binding in bindings
            if binding.resource_id in visible
        )

    # -- create ---------------------------------------------------------

    async def create_resource(
        self,
        tenant: ResolvedTenant,
        *,
        kind: str,
        slug: str,
        title: str,
        description: str,
        scope: ScopeRef,
        payload: Mapping[str, Any],
        label: str = "",
        change_note: str = "",
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str,
    ) -> ResourceView:
        kind_value = require_kind(kind)
        clean_slug = normalize_slug(slug)
        clean_title = _bounded(title.strip(), _TITLE_LIMIT)
        if not clean_title:
            fail(
                ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
                detail="title is required",
                reason="title_required",
            )
        clean_description = _bounded(description.strip(), _DESCRIPTION_LIMIT)
        owner = await self.scopes.owner_scope(tenant, scope)
        validate_payload(kind_value, payload)
        version = build_version(payload, label=label, change_note=change_note)

        user_metadata = views.public_metadata(metadata or {})
        target_path = user_metadata.get(METADATA_TARGET_PATH)
        if target_path is not None and not safe_workspace_path(target_path):
            fail(
                ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID,
                detail="metadata.path must be a relative workspace path",
                reason="unsafe_target_path",
            )
        request_digest = operation_digest(
            {
                "operation": "resource.create",
                "tenant_id": tenant.tenant_id,
                "actor_id": tenant.actor_id,
                "kind": kind_value.value,
                "slug": clean_slug,
                "title": clean_title,
                "description": clean_description,
                "scope": {"kind": owner.kind.value, "id": owner.scope_id},
                "payload": dict(payload),
                "label": version.label,
                "change_note": version.change_note,
                "metadata": user_metadata,
            }
        )
        stored_metadata: dict[str, object] = dict(user_metadata)
        stored_metadata[METADATA_DESCRIPTION] = clean_description
        stored_metadata[METADATA_LIFECYCLE] = ResourceLifecycle.DRAFT.value
        stored_metadata[METADATA_VERSION_NOTES] = {
            "1": {"label": version.label, "change_note": version.change_note}
        }
        try:
            row, replayed = await self.store.create_resource(
                tenant_id=tenant.tenant_id,
                kind=kind_value,
                slug=clean_slug,
                title=clean_title,
                description=clean_description,
                scope=owner,
                metadata=stored_metadata,
                created_by=tenant.actor_id,
                first_version=version,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except StoreConflict as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_SLUG_CONFLICT,
                detail="a resource with this kind and slug already exists in the scope",
                reason=exc.reason,
            )
        if replayed:
            receipt = row.create_receipt
            if receipt is not None and receipt.get("request_digest") != request_digest:
                fail(
                    ResourceApiErrorCode.IDEMPOTENCY_REPLAY,
                    detail="the idempotency key was already used for another resource body",
                    reason="idempotency_key_reused",
                )
        aggregate = await self._aggregate(tenant, row)
        return views.resource_view(
            row,
            active=await self._active_version(row),
            aggregate=aggregate,
            replayed=replayed,
        )

    # -- versions -------------------------------------------------------

    async def create_version(
        self,
        tenant: ResolvedTenant,
        resource_id: str,
        *,
        payload: Mapping[str, Any],
        label: str = "",
        change_note: str = "",
        expected_active_version: int | None = None,
        idempotency_key: str,
    ) -> VersionView:
        row = await self._require(tenant, resource_id)
        lifecycle = views.lifecycle_of(row)
        if lifecycle in {ResourceLifecycle.ARCHIVED, ResourceLifecycle.QUARANTINED}:
            fail(
                ResourceApiErrorCode.RESOURCE_LIFECYCLE_FORBIDDEN,
                detail=f"a {lifecycle.value} resource is immutable",
                reason="resource_not_writable",
            )
        if expected_active_version is not None and expected_active_version != row.active_version:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_STALE,
                detail="the resource active version changed since the client read it",
                reason="active_version_stale",
            )
        validate_payload(row.kind, payload)
        version = build_version(payload, label=label, change_note=change_note)
        for existing in await self.store.list_versions(resource_id=row.resource_id):
            if existing.content_digest == version.content_digest:
                # The payload is byte-identical to an existing immutable version.
                # Appending it again would create a duplicate row with no new
                # content, so the existing version is returned as a replay.
                return views.version_view(row, existing, replayed=True)

        request_digest = operation_digest(
            {
                "operation": "resource.version.create",
                "tenant_id": tenant.tenant_id,
                "resource_id": row.resource_id,
                "content_digest": version.content_digest,
                "label": version.label,
                "change_note": version.change_note,
            }
        )
        try:
            stored, replayed = await self.store.append_version(
                resource_id=row.resource_id,
                version=version,
                created_by=tenant.actor_id,
                expected_active_version=row.active_version,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except StaleResourceState as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_STALE,
                detail="the resource active version changed while the version was appended",
                reason=exc.reason,
            )
        except StoreConflict as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_IMMUTABLE,
                detail="the version could not be appended without rewriting history",
                reason=exc.reason,
            )
        refreshed = await self._require(tenant, row.resource_id)
        if replayed:
            receipt = refreshed.receipt_for(idempotency_key)
            if receipt is not None and receipt.get("request_digest") != request_digest:
                fail(
                    ResourceApiErrorCode.IDEMPOTENCY_REPLAY,
                    detail="the idempotency key was already used for another version body",
                    reason="idempotency_key_reused",
                )
        return views.version_view(refreshed, stored, replayed=replayed)

    async def publish_version(
        self,
        tenant: ResolvedTenant,
        resource_id: str,
        number: int,
        *,
        expected_active_version: int | None = None,
    ) -> ResourceView:
        row = await self._require(tenant, resource_id)
        if expected_active_version is not None and expected_active_version != row.active_version:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_STALE,
                detail="the resource active version changed since the client read it",
                reason="active_version_stale",
            )
        versions = await self.store.list_versions(resource_id=row.resource_id)
        target = next((item for item in versions if item.number == number), None)
        if target is None:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="resource version does not exist",
                reason="version_not_found",
            )
        contract = self._contract(row, versions)
        try:
            published = contract.publish_version(target.version_id)
        except ValueError as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_LIFECYCLE_FORBIDDEN,
                detail="the lifecycle contract rejected this publish transition",
                reason=str(exc),
            )
        return await self._apply_lifecycle(row, published)

    async def archive(
        self,
        tenant: ResolvedTenant,
        resource_id: str,
        *,
        expected_active_version: int | None = None,
    ) -> ResourceView:
        """Archive is the only terminal transition exposed by this API."""
        row = await self._require(tenant, resource_id)
        if expected_active_version is not None and expected_active_version != row.active_version:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_STALE,
                detail="the resource active version changed since the client read it",
                reason="active_version_stale",
            )
        versions = await self.store.list_versions(resource_id=row.resource_id)
        if not versions:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="a resource without versions has no lifecycle",
                reason="resource_without_versions",
            )
        contract = self._contract(row, versions)
        return await self._apply_lifecycle(row, contract.archive())

    # -- bindings -------------------------------------------------------

    async def create_binding(
        self,
        tenant: ResolvedTenant,
        resource_id: str,
        *,
        target: ScopeRef,
        mode: str,
        version_number: int | None = None,
        child_may_edit: bool = False,
        project_ids: Sequence[str] = (),
        workspace_path: str | None = None,
        idempotency_key: str,
    ) -> BindingView:
        row = await self._require(tenant, resource_id)
        lifecycle = views.lifecycle_of(row)
        if lifecycle is not ResourceLifecycle.ACTIVE or row.active_version is None:
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_BINDABLE,
                detail=f"a {lifecycle.value} resource cannot be bound; publish a version first",
                reason="resource_not_active",
            )
        pinned = version_number if version_number is not None else row.active_version
        if pinned != row.active_version:
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_BINDABLE,
                detail="a binding must pin the current published version",
                reason="binding_version_not_active",
            )
        version = await self.store.get_version(resource_id=row.resource_id, number=pinned)
        if version is None:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="the pinned resource version does not exist",
                reason="version_not_found",
            )
        if per_binding_path_unsupported(workspace_path):
            fail(
                ResourceApiErrorCode.RESOURCE_WORKSPACE_PATH_UNSUPPORTED,
                detail=(
                    "the current model does not persist a per-binding workspace path; "
                    "set metadata.path on the resource instead"
                ),
                reason="workspace_path_not_persisted",
            )
        binding_mode = self.scopes.binding_kind(mode)
        resolved = await self.scopes.binding_target(
            tenant, target, project_ids=tuple(project_ids)
        )
        self._check_binding_policy(row, resolved, binding_mode, pinned, version.version_id)

        request_digest = operation_digest(
            {
                "operation": "resource.binding.create",
                "tenant_id": tenant.tenant_id,
                "resource_id": row.resource_id,
                "target": {"kind": resolved.kind, "id": resolved.scope_id},
                "mode": binding_mode,
                "version_number": pinned,
                "child_may_edit": bool(child_may_edit),
            }
        )
        candidate = StoredBinding(
            tenant_id=tenant.tenant_id,
            binding_id="pending",
            resource_id=row.resource_id,
            target_kind=resolved.kind,
            target_id=resolved.scope_id,
            binding_kind=binding_mode,
            child_may_edit=bool(child_may_edit),
            version_number=pinned,
            project_ids=resolved.project_ids,
            created_by=tenant.actor_id,
        )
        try:
            stored, replayed = await self.store.create_binding(
                tenant_id=tenant.tenant_id,
                binding=candidate,
                request_digest=request_digest,
            )
        except StoreConflict as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_EXISTS,
                detail="this resource is already bound to that target",
                reason=exc.reason,
            )
        return views.binding_view(stored, replayed=replayed)

    async def delete_binding(
        self, tenant: ResolvedTenant, resource_id: str, binding_id: str
    ) -> dict[str, object]:
        row = await self._require(tenant, resource_id)
        deleted = await self.store.delete_binding(
            tenant_id=tenant.tenant_id, resource_id=row.resource_id, binding_id=binding_id
        )
        if not deleted:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_NOT_FOUND,
                detail="the resource binding does not exist",
                reason="binding_not_found",
            )
        return {"deleted": True, "id": binding_id, "resource_id": row.resource_id}

    # -- internals ------------------------------------------------------

    @staticmethod
    def _check_binding_policy(
        row: StoredResource,
        target: ResolvedTarget,
        binding_mode: str,
        pinned: int,
        version_id: str,
    ) -> None:
        """Run the immutable ``Binding.policy_errors`` contract before persisting."""
        is_project = target.kind == "project"
        binding = Binding(
            binding_id="pending",
            resource_id=row.resource_id,
            version_id=version_id,
            scope=row.scope,
            state=BindingState.ACTIVE,
            project_id=target.scope_id if is_project else None,
            target_scope=(
                ResourceScope(ScopeKind.PROJECT, target.scope_id, row.scope.tenant_id)
                if is_project
                else None
            ),
            project_ids=(target.scope_id,) if is_project else None,
            selection_policy=(
                ProjectSelectionPolicy.EXPLICIT_PROJECTS if is_project else None
            ),
            binding_kind=BindingKind(binding_mode),
        )
        errors = binding.policy_errors()
        if errors:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                detail="the binding policy contract rejected this edge",
                reason=errors[0],
                extra={"reasons": list(errors)},
            )
        if pinned < 1:  # defensive: contracts already require a positive version
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="the pinned resource version is invalid",
                reason="version_number_invalid",
            )

    def _contract(self, row: StoredResource, versions: Sequence[StoredVersion]) -> Resource:
        """Rebuild the migration contract used for lifecycle decisions."""
        if not versions:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND,
                detail="a resource without versions has no lifecycle",
                reason="resource_without_versions",
            )
        ordered = sorted(versions, key=lambda item: item.number)
        contract_versions = tuple(views.to_contract_version(item) for item in ordered)
        try:
            return Resource(
                resource_id=row.resource_id,
                kind=row.kind,
                scope=row.scope,
                lifecycle=views.lifecycle_of(row),
                versions=contract_versions,
                metadata=dict(row.metadata),
                active_version=row.active_version,
                source_kind=f"resource:{row.kind.value}",
                source_id=row.resource_id,
                source_digest=contract_versions[-1].content_digest,
            )
        except ValueError as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_IMMUTABLE,
                detail="the stored resource no longer satisfies the lifecycle contract",
                reason=f"stored_resource_invalid:{exc}",
            )

    async def _apply_lifecycle(self, row: StoredResource, next_state: Resource) -> ResourceView:
        statuses = [(item.version_id, item.status) for item in next_state.versions]
        try:
            updated = await self.store.transition_lifecycle(
                resource_id=row.resource_id,
                lifecycle=next_state.lifecycle,
                active_version=next_state.active_version,
                version_statuses=statuses,
                expected_active_version=row.active_version,
            )
        except StaleResourceState as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_VERSION_STALE,
                detail="the resource changed while the lifecycle transition ran",
                reason=exc.reason,
            )
        except StoreConflict as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_LIFECYCLE_FORBIDDEN,
                detail="the lifecycle transition was rejected by persistence",
                reason=exc.reason,
            )
        aggregate = await self._aggregate(updated)
        return views.resource_view(
            updated, active=await self._active_version(updated), aggregate=aggregate
        )

    async def _active_version(self, row: StoredResource) -> StoredVersion | None:
        if row.active_version is None:
            return None
        return await self.store.get_version(
            resource_id=row.resource_id, number=row.active_version
        )

    async def _require(self, tenant: ResolvedTenant, resource_id: str) -> StoredResource:
        row = await self.store.get_resource(
            tenant_id=tenant.tenant_id, resource_id=resource_id
        )
        if row is None or row.tenant_id != tenant.tenant_id:
            # A foreign resource is *not found*, never forbidden: existence must
            # not leak across tenants.
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_FOUND,
                detail="resource does not exist",
                reason="resource_not_found",
            )
        return row

    async def _aggregate(
        self, tenant: ResolvedTenant, row: StoredResource
    ) -> ResourceAggregate:
        aggregates = await self.store.aggregates(
            tenant_id=tenant.tenant_id, resource_ids=[row.resource_id]
        )
        return aggregates.get(row.resource_id, ResourceAggregate())

    async def _load(
        self, tenant: ResolvedTenant, resource_id: str
    ) -> tuple[StoredResource, ResourceAggregate, StoredVersion | None]:
        row = await self._require(tenant, resource_id)
        aggregate = await self._aggregate(tenant, row)
        return row, aggregate, await self._active_version(row)


__all__ = [
    "ResourceCommandService",
    "build_version",
    "normalize_slug",
    "operation_digest",
    "per_binding_path_unsupported",
    "require_idempotency_key",
    "require_kind",
    "validate_payload",
]
