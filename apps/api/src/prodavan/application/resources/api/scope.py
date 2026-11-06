"""Server-side scope resolution for the canonical Resource API.

Rules enforced here, in this order:

1. The request tenant is resolved on the server (``ResolvedTenant``) and never
   read from a body, query string or resource metadata.
2. An owner scope must name the request tenant. A ``company`` scope id *is* the
   tenant. ``cabinet``/``project``/``module`` ids are proven through
   ``ResourceScopeProofPort``; a scope owned by another tenant is reported as
   not found (404) so existence never leaks across tenants.
3. A binding target must resolve inside the request tenant as well. A target
   that exists but belongs to another tenant, or that does not exist, is a
   cross-tenant denial and never a silent rebind.
4. ``ScopeKind`` has no ``module`` member, so a module binding is accepted
   administratively but can never be expanded into a materialization snapshot.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from prodavan.application.migration.resources.lifecycle_contracts import (
    ResourceScope,
    ScopeKind,
)

from .contracts import (
    BINDING_KINDS,
    MATERIALIZABLE_TARGET_KINDS,
    OWNER_SCOPE_KINDS,
    TARGET_SCOPE_KINDS,
    ResolvedTenant,
    ScopeRef,
)
from .errors import ResourceApiErrorCode, fail
from .ports import ResourceScopeProofPort


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A target scope that is proven to live inside the request tenant."""

    kind: str
    scope_id: str
    tenant_id: str
    project_ids: tuple[str, ...] = ()

    @property
    def materializable(self) -> bool:
        return self.kind in MATERIALIZABLE_TARGET_KINDS

    def as_resource_scope(self) -> ResourceScope:
        """Canonical scope for materialization; unsupported for ``module``."""
        if not self.materializable:
            fail(
                ResourceApiErrorCode.RESOURCE_NOT_MATERIALIZABLE,
                detail="only project bindings can be expanded into a materialization snapshot",
                reason="binding_target_not_materializable",
            )
        return ResourceScope(ScopeKind.PROJECT, self.scope_id, self.tenant_id)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class ResourceScopeService:
    """Resolve and prove resource owner scopes and binding targets."""

    def __init__(self, proofs: ResourceScopeProofPort | None) -> None:
        self._proofs = proofs

    # -- owner scope ----------------------------------------------------

    async def owner_scope(self, tenant: ResolvedTenant, scope: ScopeRef) -> ResourceScope:
        """Return the canonical owner scope or fail closed."""
        if scope.kind not in OWNER_SCOPE_KINDS:
            fail(
                ResourceApiErrorCode.RESOURCE_SCOPE_UNSUPPORTED,
                detail=f"resource owner scope kind {scope.kind!r} is not supported",
                reason="owner_scope_kind_unsupported",
            )
        if scope.kind == "company":
            if scope.scope_id != tenant.tenant_id:
                # The tenant anchor is the tenant; another company id is foreign.
                fail(
                    ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN,
                    detail="company scope does not belong to the authenticated tenant",
                    reason="cross_tenant_company_scope",
                )
            return ResourceScope(ScopeKind.COMPANY, tenant.tenant_id, tenant.tenant_id)
        if scope.kind == "cabinet":
            await self.require_cabinet(tenant, scope.scope_id, reason="cross_tenant_cabinet_scope")
            return ResourceScope(ScopeKind.CABINET, scope.scope_id, tenant.tenant_id)
        await self.require_project(tenant, scope.scope_id, reason="cross_tenant_project_scope")
        return ResourceScope(ScopeKind.PROJECT, scope.scope_id, tenant.tenant_id)

    async def require_cabinet(self, tenant: ResolvedTenant, cabinet_id: str, *, reason: str) -> None:
        if self._proofs is None:
            fail(
                ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN,
                detail="cabinet scope cannot be proven",
                reason="scope_proof_unavailable",
            )
        tenants = await self._proofs.cabinet_tenants(cabinet_id)
        if tenant.tenant_id not in tenants:
            fail(
                ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN,
                detail="cabinet scope does not belong to the authenticated tenant",
                reason=reason,
            )

    async def require_project(self, tenant: ResolvedTenant, project_id: str, *, reason: str):
        if self._proofs is None:
            fail(
                ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN,
                detail="project scope cannot be proven",
                reason="scope_proof_unavailable",
            )
        proof = await self._proofs.project(project_id)
        if proof is None or proof.tenant_id != tenant.tenant_id:
            fail(
                ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN,
                detail="project scope does not belong to the authenticated tenant",
                reason=reason,
            )
        return proof

    # -- binding target -------------------------------------------------

    async def binding_target(
        self, tenant: ResolvedTenant, target: ScopeRef, *, project_ids: tuple[str, ...]
    ) -> ResolvedTarget:
        """Prove a binding target inside the tenant and normalise project ids."""
        if target.kind not in TARGET_SCOPE_KINDS:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                detail=f"binding target kind {target.kind!r} is not supported",
                reason="binding_target_kind_unsupported",
            )
        if target.kind == "project":
            await self.require_project(
                tenant, target.scope_id, reason="cross_tenant_binding_target"
            )
            explicit = _dedupe(project_ids)
            if explicit and explicit != (target.scope_id,):
                fail(
                    ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                    detail="a project binding must not carry a different explicit project set",
                    reason="project_binding_mismatch",
                )
            return ResolvedTarget("project", target.scope_id, tenant.tenant_id, (target.scope_id,))
        if target.kind == "cabinet":
            await self.require_cabinet(
                tenant, target.scope_id, reason="cross_tenant_binding_target"
            )
            if project_ids:
                # ``Binding.policy_errors`` rejects a project policy on a
                # non-project target, and an implicit project set would silently
                # widen the binding. Cabinet bindings are administrative only;
                # to reach project workspaces, bind each project explicitly.
                fail(
                    ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                    detail="a cabinet binding does not carry a project set",
                    reason="project_policy_on_non_project_binding",
                )
            return ResolvedTarget("cabinet", target.scope_id, tenant.tenant_id, ())
        # module
        if self._proofs is None or not await self._proofs.module_available_to_tenant(
            target.scope_id, tenant.tenant_id
        ):
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_CROSS_TENANT,
                detail="module target is not available to the authenticated tenant",
                reason="cross_tenant_module_target",
            )
        if project_ids:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                detail="a module binding does not carry an explicit project set",
                reason="project_policy_on_non_project_binding",
            )
        return ResolvedTarget("module", target.scope_id, tenant.tenant_id, ())

    @staticmethod
    def binding_kind(value: str) -> str:
        mode = value.strip().lower()
        if mode not in BINDING_KINDS:
            fail(
                ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED,
                detail=f"binding mode {value!r} is not supported",
                reason="binding_kind_unsupported",
            )
        return mode


__all__ = ["ResolvedTarget", "ResourceScopeService"]
