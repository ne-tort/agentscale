"""Ports for the canonical Resource API.

Three narrow boundaries keep the application layer free of SQLAlchemy:

* ``ResourceStorePort`` — tenant-scoped persistence of roots, immutable versions
  and bindings, plus the per-resource idempotency fence. Mutating methods return
  ``(record, replayed)``: a replay is recognised inside the store's own
  transaction and returns the *stored* row, never a second row.
* ``ResourceScopeProofPort`` — server-side proof of project/cabinet/module
  ownership. A client-supplied tenant is never trusted; a scope whose proof does
  not name the request tenant is reported as *not found*.
* ``AtomicMaterializationApplyPort`` — reused verbatim from
  ``application.resource_materialization.ports``; the API never writes
  materialization state itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from prodavan.application.migration.resources.lifecycle_contracts import (
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    VersionStatus,
)

from .contracts import (
    MaterializationApplyView,
    NewVersion,
    ResourceAggregate,
    StoredBinding,
    StoredResource,
    StoredVersion,
)


@dataclass(frozen=True, slots=True)
class ApplyReceipt:
    """Durable receipt of one applied materialization plan.

    The receipt is the only memory of an apply: a repeat request with the same
    ``Idempotency-Key`` and the same request digest returns this receipt instead
    of planning again, and the same key with another digest is a conflict. It
    carries no resource payload and no provider output, only identity, generation
    and outcome.
    """

    tenant_id: str
    project_id: str
    idempotency_key: str
    request_digest: str
    plan_id: str
    outcome: str
    expected_generation: int
    desired_generation: int
    applied_generation: int | None
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "idempotency_key", "request_digest", "plan_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not self.outcome.strip():
            raise ValueError("outcome is required")
        if self.expected_generation < 0 or self.desired_generation < 1:
            raise ValueError("receipt generations are invalid")
        if self.desired_generation <= self.expected_generation:
            raise ValueError("an applied generation must advance the observed generation")
        object.__setattr__(self, "blockers", tuple(self.blockers))

    def view(self) -> MaterializationApplyView:
        """Project the receipt back onto the wire contract as an idempotent replay."""
        return MaterializationApplyView(
            plan_id=self.plan_id,
            project_id=self.project_id,
            outcome=self.outcome,
            expected_generation=self.expected_generation,
            desired_generation=self.desired_generation,
            applied_generation=self.applied_generation,
            blockers=self.blockers,
            idempotent_replay=True,
        )


class ResourceApplyReceiptPort(Protocol):
    """Tenant-scoped durable storage for materialization apply receipts.

    Implementations must treat ``(tenant_id, project_id, idempotency_key)`` as
    the primary identity and must never let one tenant observe another tenant's
    receipt. A missing receipt is ``None``; a storage failure is raised, never
    downgraded to ``None``.
    """

    async def load_apply_receipt(
        self, *, tenant_id: str, project_id: str, idempotency_key: str
    ) -> ApplyReceipt | None: ...

    async def save_apply_receipt(self, receipt: ApplyReceipt) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectProof:
    """Server-observed project ownership."""

    project_id: str
    tenant_id: str
    cabinet_id: str

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.tenant_id.strip() or not self.cabinet_id.strip():
            raise ValueError("complete project proof is required")


class ResourceScopeProofPort(Protocol):
    """Read-only server proofs used to resolve and reject scopes.

    ``resources`` has no tenant column, so the owning tenant of a cabinet- or
    project-scoped resource is derived from the scope itself. Owning a resource in
    a cabinet therefore requires being the cabinet's *owner* tenant, while merely
    binding to a cabinet only requires access (``cabinet_tenants``).
    """

    async def project(self, project_id: str) -> ProjectProof | None: ...

    async def cabinet_owner_tenant(self, cabinet_id: str) -> str | None:
        """The single tenant that owns the cabinet, or ``None`` when unowned."""

    async def cabinet_tenants(self, cabinet_id: str) -> frozenset[str]:
        """Every tenant that may legitimately use the cabinet (owner + grantees)."""

    async def module_available_to_tenant(self, module_id: str, tenant_id: str) -> bool: ...

    async def owner_scope_ids(self, tenant_id: str, kind: str) -> frozenset[str]:
        """Ids of scopes of ``kind`` that the tenant may own resources in."""


class ResourceStorePort(Protocol):
    """Tenant-scoped persistence boundary for canonical resources."""

    async def get_resource(self, *, tenant_id: str, resource_id: str) -> StoredResource | None: ...

    async def aggregates(
        self, *, tenant_id: str, resource_ids: Sequence[str]
    ) -> Mapping[str, ResourceAggregate]: ...

    async def list_resources(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind | None = None,
        lifecycle: ResourceLifecycle | None = None,
        limit: int = 50,
        after_id: str | None = None,
    ) -> tuple[StoredResource, ...]: ...

    async def create_resource(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        slug: str,
        title: str,
        description: str,
        scope: ResourceScope,
        metadata: Mapping[str, object],
        created_by: str,
        first_version: NewVersion,
        idempotency_key: str,
        request_digest: str,
    ) -> tuple[StoredResource, bool]:
        """Insert root + first version; ``True`` means the key was replayed."""

    async def list_versions(self, *, resource_id: str) -> tuple[StoredVersion, ...]: ...

    async def get_version(self, *, resource_id: str, number: int) -> StoredVersion | None: ...

    async def append_version(
        self,
        *,
        resource_id: str,
        version: NewVersion,
        created_by: str,
        expected_active_version: int | None,
        idempotency_key: str,
        request_digest: str,
    ) -> tuple[StoredVersion, bool]:
        """Append under a root row lock; a matching key returns the stored row."""

    async def transition_lifecycle(
        self,
        *,
        resource_id: str,
        lifecycle: ResourceLifecycle,
        active_version: int | None,
        version_statuses: Sequence[tuple[str, VersionStatus]],
        expected_active_version: int | None,
    ) -> StoredResource:
        """Compare-and-set the root lifecycle and the version status projection."""

    async def list_bindings(
        self, *, tenant_id: str, resource_id: str | None = None
    ) -> tuple[StoredBinding, ...]: ...

    async def list_project_bindings(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[StoredBinding, ...]: ...

    async def get_binding(
        self, *, tenant_id: str, resource_id: str, binding_id: str
    ) -> StoredBinding | None: ...

    async def create_binding(
        self,
        *,
        tenant_id: str,
        binding: StoredBinding,
        request_digest: str,
    ) -> tuple[StoredBinding, bool]:
        """Insert the edge; an identical edge returns the stored row."""

    async def delete_binding(
        self, *, tenant_id: str, resource_id: str, binding_id: str
    ) -> bool: ...

    async def project_generation(self, *, tenant_id: str, project_id: str) -> int:
        """Highest durable materialization generation observed for the project."""


__all__ = [
    "ApplyReceipt",
    "ProjectProof",
    "ResourceApplyReceiptPort",
    "ResourceScopeProofPort",
    "ResourceStorePort",
]
