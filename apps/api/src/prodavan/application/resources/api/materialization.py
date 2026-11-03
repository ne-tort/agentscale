"""Materialization planning and apply for the canonical Resource API.

This module contains **no** planning logic of its own. It only:

1. selects the bindings that explicitly target the requested project (a client
   never supplies a selection list, so a resource cannot be smuggled into a plan);
2. re-reads each selected resource/version through the tenant-scoped store and
   converts it to the existing ``application.resources`` contracts;
3. hands the selections to ``ResourceMaterializationResolver`` (MIG-RES-r06),
   the single owner of failure classification and operation planning;
4. applies through ``apply_resolution`` and the reused
   ``AtomicMaterializationApplyPort`` — one fenced CAS call, never a per-resource
   loop, and never a materialization write performed by this package;
5. replays a completed apply from the durable receipt port, so one idempotency
   key can neither apply twice nor be reused for another plan.

A plan that produced any failure exposes **no** operations, and ``apply`` refuses
it, so a partially resolvable selection can never be applied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from prodavan.application.migration.resources.lifecycle_contracts import (
    BindingKind,
    ResourceScope,
    ScopeKind,
    VersionStatus,
    canonical_digest,
)
from prodavan.application.resource_materialization import (
    AtomicMaterializationApplyPort,
    MaterializationApplyOutcome,
    ResourceMaterializationContext,
    ResourceMaterializationResolution,
    ResourceMaterializationResolver,
    ResourceSelection,
    apply_resolution,
)
from prodavan.application.resource_materialization.ports import (
    ResourceMaterializationSourcePort,
)
from prodavan.application.resources.contracts import (
    ContentResource,
    ResourceBinding,
    ResourceVersion,
)

from . import views
from .commands import operation_digest, require_idempotency_key
from .contracts import (
    MaterializationApplyView,
    MaterializationEntryView,
    MaterializationPlanView,
    ResolvedTenant,
    ScopeRef,
)
from .errors import ResourceApiErrorCode, StoreConflict, fail
from .ports import ApplyReceipt, ResourceApplyReceiptPort, ResourceStorePort
from .scope import ResourceScopeService


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlannedSelection:
    """One binding selected for a project plan, already proven in-tenant."""

    resource_id: str
    binding_id: str
    version_number: int
    version_id: str
    target: str | None


@dataclass(frozen=True, slots=True)
class StoredPlan:
    """A plan persisted for later apply: the exact resolution plus its inputs.

    ``expected_generation`` is the generation observed when the plan was built and
    ``desired_generation`` is the generation the plan advances to. ``apply`` must
    present ``desired_generation`` and re-validates ``expected_generation``.
    """

    plan_id: str
    project_id: str
    tenant_id: str
    expected_generation: int
    desired_generation: int
    status: str
    created_at: str
    resolution: ResourceMaterializationResolution
    selections: tuple[PlannedSelection, ...]
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.resolution.is_complete and not self.blockers

    @property
    def request_digest(self) -> str:
        return _digest(
            {
                "plan_id": self.plan_id,
                "project_id": self.project_id,
                "tenant_id": self.tenant_id,
                "expected_generation": self.expected_generation,
                "desired_generation": self.desired_generation,
                "snapshot_digest": self.resolution.snapshot_digest,
                "operation_set_digest": self.resolution.operation_set_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class _SelectionSnapshot:
    """Tenant-scoped read result handed to the resolver's source port."""

    tenant_id: str
    selections: tuple[PlannedSelection, ...]
    scopes: Mapping[str, ResourceScope]
    warnings: tuple[str, ...] = ()

    def scope_of(self, resource_id: str) -> ResourceScope:
        scope = self.scopes.get(resource_id)
        if scope is None:
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_FOUND,
                detail="selected resource does not exist in this tenant",
                reason="resource_not_found",
            )
        return scope


@dataclass(slots=True)
class ResourceMaterializationService:
    """Plan and apply a project's resource bindings through the existing resolver."""

    store: ResourceStorePort
    scopes: ResourceScopeService
    apply_port: AtomicMaterializationApplyPort | None = None
    receipts: ResourceApplyReceiptPort | None = None

    async def plan(
        self,
        tenant: ResolvedTenant,
        project: ScopeRef,
        *,
        expected_generation: int | None = None,
        revision_id: str | None = None,
    ) -> MaterializationPlanView:
        """Read-only plan preview; nothing is persisted and nothing is applied."""
        plan = await self.build_plan(
            tenant,
            project,
            expected_generation=expected_generation,
            revision_id=revision_id,
        )
        return _plan_view(plan)

    async def build_plan(
        self,
        tenant: ResolvedTenant,
        project: ScopeRef,
        *,
        expected_generation: int | None = None,
        revision_id: str | None = None,
    ) -> StoredPlan:
        """Build the plan record; the identity is derived from observable state."""
        if project.kind != "project":
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_MATERIALIZABLE,
                detail="materialization is defined for a project scope only",
                reason="materialization_scope_not_project",
            )
        await self.scopes.require_project(
            tenant, project.scope_id, reason="cross_tenant_project_scope"
        )
        observed = await self.store.project_generation(
            tenant_id=tenant.tenant_id, project_id=project.scope_id
        )
        if expected_generation is not None and expected_generation != observed:
            fail(
                ResourceApiErrorCode.RESOURCE_PLAN_STALE,
                detail="the project materialization generation changed since the client read it",
                reason="generation_stale",
                extra={"observed_generation": observed},
            )
        desired = observed + 1
        snapshot = await self._select(tenant, project.scope_id)
        resolution = await ResourceMaterializationResolver(
            source=_StoreSource(
                tenant_id=tenant.tenant_id, store=self.store, snapshot=snapshot
            )
        ).resolve(
            ResourceMaterializationContext(
                project_id=project.scope_id,
                revision_id=revision_id
                or f"resource-plan:{project.scope_id}:{desired}",
                target_scope=ResourceScope(
                    ScopeKind.PROJECT, project.scope_id, tenant.tenant_id
                ),
                desired_generation=desired,
                selections=tuple(
                    ResourceSelection(
                        resource_id=item.resource_id,
                        binding_id=item.binding_id,
                        # Selection scope is the binding target for a project
                        # binding; the resolver re-checks it against the resource.
                        scope=snapshot.scope_of(item.resource_id),
                        version_number=item.version_number,
                        target=item.target,
                        required=True,
                    )
                    for item in snapshot.selections
                ),
                applied_generation=None,
            )
        )
        blockers = tuple(failure.code for failure in resolution.failures)
        if not blockers and not snapshot.selections:
            # An empty selection is legitimate state, but presenting it as a
            # ready plan would let a caller believe resources were materialized.
            blockers = ("no_project_resource_bindings",)
        plan = StoredPlan(
            plan_id=_digest(
                {
                    "tenant_id": tenant.tenant_id,
                    "project_id": project.scope_id,
                    "expected_generation": observed,
                    "desired_generation": desired,
                    "snapshot_digest": resolution.snapshot_digest,
                    "selections": [
                        [item.resource_id, item.binding_id, item.version_number]
                        for item in snapshot.selections
                    ],
                }
            ),
            project_id=project.scope_id,
            tenant_id=tenant.tenant_id,
            expected_generation=observed,
            desired_generation=desired,
            status="ready" if (resolution.is_complete and not blockers) else "blocked",
            created_at=datetime.now(timezone.utc).isoformat(),
            resolution=resolution,
            selections=snapshot.selections,
            blockers=blockers,
        )
        return plan

    async def open_plan(
        self,
        tenant: ResolvedTenant,
        project: ScopeRef,
        *,
        plan_id: str,
        desired_generation: int,
    ) -> StoredPlan:
        """Rebuild the plan a client was handed and prove it is still current.

        The plan identity is derived from observable state, so rebuilding is the
        only admissible way to recover a plan: a client-supplied plan body (a
        selection or operation list) is never trusted. A rebuild whose identity
        differs from the one the client quotes is stale, not "close enough".
        """
        if desired_generation < 1:
            fail(
                ResourceApiErrorCode.RESOURCE_PLAN_STALE,
                detail="the plan generation must be positive",
                reason="expected_generation_invalid",
            )
        plan = await self.build_plan(
            tenant, project, expected_generation=desired_generation - 1
        )
        if plan.plan_id != plan_id:
            fail(
                ResourceApiErrorCode.RESOURCE_PLAN_STALE,
                detail="the project resource state changed after this plan was built",
                reason="plan_digest_stale",
                extra={"current_plan_id": plan.plan_id},
            )
        return plan

    async def apply_plan(
        self,
        tenant: ResolvedTenant,
        project: ScopeRef,
        *,
        plan_id: str,
        expected_generation: int,
        idempotency_key: str | None,
    ) -> MaterializationApplyView:
        """Apply the current plan once per idempotency key.

        Order is deliberate: the key is validated first (a malformed request is a
        ``400`` before any read), then a stored receipt is replayed verbatim, and
        only then is the plan rebuilt and applied through the reused CAS boundary.
        """
        key = require_idempotency_key(idempotency_key)
        if project.kind != "project":
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_MATERIALIZABLE,
                detail="materialization is defined for a project scope only",
                reason="materialization_scope_not_project",
            )
        if self.receipts is None:
            # Fail closed: without durable receipts a repeat apply could not be
            # distinguished from a second apply, so apply is unavailable.
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_UNAVAILABLE,
                detail="no durable apply-receipt store is configured",
                reason="apply_receipt_store_unwired",
            )
        await self.scopes.require_project(
            tenant, project.scope_id, reason="cross_tenant_project_scope"
        )
        request_digest = operation_digest(
            {
                "operation": "resource.materialization.apply",
                "tenant_id": tenant.tenant_id,
                "actor_id": tenant.actor_id,
                "project_id": project.scope_id,
                "plan_id": plan_id,
                "expected_generation": expected_generation,
            }
        )
        existing = await self.receipts.load_apply_receipt(
            tenant_id=tenant.tenant_id,
            project_id=project.scope_id,
            idempotency_key=key,
        )
        if existing is not None:
            if existing.request_digest != request_digest:
                fail(
                    ResourceApiErrorCode.IDEMPOTENCY_REPLAY,
                    detail="the idempotency key was already used for another apply request",
                    reason="idempotency_key_reused",
                )
            return existing.view()
        plan = await self.open_plan(
            tenant,
            project,
            plan_id=plan_id,
            desired_generation=expected_generation,
        )
        view = await self.apply(tenant, plan, expected_generation=plan.desired_generation)
        receipt = ApplyReceipt(
            tenant_id=tenant.tenant_id,
            project_id=plan.project_id,
            idempotency_key=key,
            request_digest=request_digest,
            plan_id=plan.plan_id,
            outcome=view.outcome,
            expected_generation=view.expected_generation,
            desired_generation=view.desired_generation,
            applied_generation=view.applied_generation,
            blockers=view.blockers,
        )
        try:
            await self.receipts.save_apply_receipt(receipt)
        except StoreConflict as exc:
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_CONFLICT,
                detail="the apply receipt could not be recorded without rewriting history",
                reason=exc.reason,
            )
        return view

    async def apply(
        self,
        tenant: ResolvedTenant,
        plan: StoredPlan,
        *,
        expected_generation: int,
    ) -> MaterializationApplyView:
        if plan.tenant_id != tenant.tenant_id or not plan.project_id.strip():
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_FOUND,
                detail="materialization plan does not exist",
                reason="plan_not_found",
            )
        if expected_generation != plan.desired_generation:
            fail(
                ResourceApiErrorCode.RESOURCE_PLAN_STALE,
                detail="the apply must target the generation this plan was built for",
                reason="expected_generation_mismatch",
                extra={"plan_generation": plan.desired_generation},
            )
        if plan.blockers or not plan.resolution.is_complete:
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_BLOCKED,
                detail="the plan is blocked and cannot be applied",
                reason="plan_blocked",
                extra={"blockers": list(plan.blockers)},
            )
        if self.apply_port is None:
            # Fail closed: a plan may be previewed, but never applied through a
            # missing CAS boundary.
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_UNAVAILABLE,
                detail="no atomic materialization apply port is configured",
                reason="apply_port_unavailable",
            )
        await self.scopes.require_project(
            tenant, plan.project_id, reason="cross_tenant_project_scope"
        )
        observed = await self.store.project_generation(
            tenant_id=tenant.tenant_id, project_id=plan.project_id
        )
        if observed != plan.expected_generation:
            fail(
                ResourceApiErrorCode.RESOURCE_PLAN_STALE,
                detail="the project materialization generation advanced after the plan was built",
                reason="generation_advanced",
                extra={"observed_generation": observed},
            )
        outcome = await apply_resolution(plan.resolution, apply_port=self.apply_port)
        if outcome is MaterializationApplyOutcome.BLOCKED:
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_BLOCKED,
                detail="the resolver refused to apply this plan",
                reason="resolution_incomplete",
            )
        if outcome is MaterializationApplyOutcome.CONFLICT:
            fail(
                ResourceApiErrorCode.RESOURCE_APPLY_CONFLICT,
                detail="the materialization apply lost the generation race",
                reason="cas_conflict",
            )
        replay = outcome is MaterializationApplyOutcome.REPLAY
        return MaterializationApplyView(
            plan_id=plan.plan_id,
            project_id=plan.project_id,
            outcome=outcome.value,
            expected_generation=plan.expected_generation,
            desired_generation=plan.desired_generation,
            applied_generation=plan.desired_generation,
            blockers=(),
            idempotent_replay=replay,
        )

    async def _select(self, tenant: ResolvedTenant, project_id: str) -> _SelectionSnapshot:
        bindings = await self.store.list_project_bindings(
            tenant_id=tenant.tenant_id, project_id=project_id
        )
        visible = await self.store.aggregates(
            tenant_id=tenant.tenant_id,
            resource_ids=sorted({binding.resource_id for binding in bindings}),
        )
        selected: list[PlannedSelection] = []
        scopes: dict[str, ResourceScope] = {}
        skipped: list[str] = []
        for binding in sorted(bindings, key=lambda item: (item.resource_id, item.binding_id)):
            if binding.resource_id not in visible:
                continue
            row = await self.store.get_resource(
                tenant_id=tenant.tenant_id, resource_id=binding.resource_id
            )
            if row is None:
                continue
            scopes[row.resource_id] = row.scope
            version = await self.store.get_version(
                resource_id=row.resource_id, number=binding.version_number
            )
            if version is None:
                skipped.append(f"version_missing:{binding.binding_id}")
            selected.append(
                PlannedSelection(
                    resource_id=row.resource_id,
                    binding_id=binding.binding_id,
                    version_number=binding.version_number,
                    version_id=version.version_id if version is not None else "",
                    target=views.target_path_of(row),
                )
            )
        return _SelectionSnapshot(
            tenant_id=tenant.tenant_id,
            selections=tuple(selected),
            scopes=scopes,
            warnings=tuple(skipped),
        )


class _StoreSource(ResourceMaterializationSourcePort):
    """Adapter from the tenant-scoped store to the resolver's source contract."""

    def __init__(
        self, *, tenant_id: str, store: ResourceStorePort, snapshot: _SelectionSnapshot
    ) -> None:
        self._tenant_id = tenant_id
        self._store = store
        self._snapshot = snapshot

    async def get_resource(self, resource_id: str) -> ContentResource | None:
        row = await self._store.get_resource(
            tenant_id=self._tenant_id, resource_id=resource_id
        )
        if row is None:
            return None
        return ContentResource(
            id=row.resource_id,
            kind=row.kind,
            slug=row.slug,
            title=row.title,
            scope=row.scope,
            metadata=dict(row.metadata),
            active_version=row.active_version,
        )

    async def get_version(self, resource_id: str, number: int) -> ResourceVersion | None:
        stored = await self._store.get_version(resource_id=resource_id, number=number)
        if stored is None:
            return None
        contract = views.to_contract_version(stored)
        return ResourceVersion(
            id=contract.version_id,
            resource_id=contract.resource_id or resource_id,
            number=contract.sequence,
            status=VersionStatus(contract.status),
            payload=contract.payload,
            created_at=contract.created_at,
            created_by=contract.created_by,
        )

    async def list_bindings(self, target: ResourceScope) -> list[ResourceBinding]:
        if target.kind is not ScopeKind.PROJECT:
            return []
        rows = await self._store.list_project_bindings(
            tenant_id=self._tenant_id, project_id=target.scope_id
        )
        out: list[ResourceBinding] = []
        for binding in rows:
            resource = await self._store.get_resource(
                tenant_id=self._tenant_id, resource_id=binding.resource_id
            )
            if resource is None:
                continue
            out.append(
                ResourceBinding(
                    id=binding.binding_id,
                    resource_id=binding.resource_id,
                    target=ResourceScope(
                        ScopeKind.PROJECT, binding.target_id, self._tenant_id
                    ),
                    binding=BindingKind(binding.binding_kind),
                    child_may_edit=binding.child_may_edit,
                    version_number=binding.version_number,
                    project_ids=binding.project_ids,
                    session_id=binding.session_id,
                )
            )
        return out


def _entry_view(operation: object) -> MaterializationEntryView:
    provenance = getattr(operation, "provenance", None)
    metadata = getattr(provenance, "metadata", None) or {}
    return MaterializationEntryView(
        id=f"{operation.kind.value}:{operation.target}",
        resource_id=getattr(provenance, "source_id", "") or "",
        kind=operation.kind.value,
        target=operation.target,
        required=bool(operation.required),
        status="planned",
        digest=_digest(
            {
                "kind": operation.kind.value,
                "target": operation.target,
                "payload": operation.payload,
            }
        ),
        provenance={
            "source_type": getattr(provenance, "source_type", None),
            "source_id": getattr(provenance, "source_id", None),
            "source_revision": getattr(provenance, "source_revision", None),
            "source_generation": getattr(provenance, "source_generation", None),
            "binding_id": metadata.get("binding_id") if hasattr(metadata, "get") else None,
            "version_number": metadata.get("version_number") if hasattr(metadata, "get") else None,
        },
    )


def _plan_view(plan: StoredPlan) -> MaterializationPlanView:
    resolution = plan.resolution
    return MaterializationPlanView(
        id=plan.plan_id,
        project_id=plan.project_id,
        desired_generation=plan.desired_generation,
        expected_generation=plan.expected_generation,
        applied_generation=resolution.context.applied_generation,
        status=plan.status,
        digest=canonical_digest(
            {
                "snapshot_digest": resolution.snapshot_digest,
                "operation_set_digest": resolution.operation_set_digest,
            }
        ),
        idempotency_key=_digest(
            {"plan_id": plan.plan_id, "request_digest": plan.request_digest}
        ),
        snapshot_digest=resolution.snapshot_digest,
        entries=tuple(_entry_view(operation) for operation in resolution.operations),
        blockers=plan.blockers,
        created_at=plan.created_at,
    )


__all__ = ["PlannedSelection", "ResourceMaterializationService", "StoredPlan"]
