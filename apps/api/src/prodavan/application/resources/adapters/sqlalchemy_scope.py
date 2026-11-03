"""Server-side scope proofs over the real tenant/cabinet/project/module rows.

The ``resources`` table stores a polymorphic ``(scope_kind, scope_id)`` pair and
no tenant column, so the owning tenant is always *derived*:

* ``company`` — the scope id **is** the tenant id;
* ``cabinet`` — the cabinet's owner tenant (``cabinet_instances.owner_company_id``,
  falling back to ``cabinet_instances.company_id``). Owning a resource inside a
  cabinet requires ownership, while merely *binding* to a cabinet only requires
  access, which is why :meth:`cabinet_tenants` is a separate set;
* ``project`` — ``projects.company_id``.

Every method returns ``None``/``frozenset()`` for an unknown or foreign object, so
the application layer can answer ``404`` without leaking existence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.resources.api.ports import ProjectProof
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCompanyGrantRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

_ACTIVE = "active"
_PLATFORM_OWNER = "platform"
_ALL_GRANT_SCOPE = "all"


@dataclass(slots=True)
class SqlAlchemyResourceScopeProof:
    """Read-only proofs for one request session."""

    session: AsyncSession

    # -- project --------------------------------------------------------

    async def project(self, project_id: str) -> ProjectProof | None:
        row = await self.session.get(ProjectRow, project_id)
        if row is None or not row.company_id:
            return None
        return ProjectProof(
            project_id=str(row.id),
            tenant_id=str(row.company_id),
            cabinet_id=str(row.cabinet_id),
        )

    # -- cabinet --------------------------------------------------------

    async def _cabinet_row(self, cabinet_id: str) -> CabinetInstanceRow | None:
        return await self.session.get(CabinetInstanceRow, cabinet_id)

    async def cabinet_owner_tenant(self, cabinet_id: str) -> str | None:
        row = await self._cabinet_row(cabinet_id)
        if row is None:
            return None
        owner = row.owner_company_id or row.company_id
        return str(owner) if owner else None

    async def cabinet_tenants(self, cabinet_id: str) -> frozenset[str]:
        row = await self._cabinet_row(cabinet_id)
        if row is None:
            return frozenset()
        tenants: set[str] = set()
        for candidate in (row.owner_company_id, row.company_id):
            if candidate:
                tenants.add(str(candidate))
        grants = await self.session.execute(
            select(CabinetCompanyGrantRow.company_id).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.status == _ACTIVE,
            )
        )
        tenants.update(str(value) for value in grants.scalars().all() if value)
        return frozenset(tenants)

    # -- module ---------------------------------------------------------

    async def module_available_to_tenant(self, module_id: str, tenant_id: str) -> bool:
        row = await self.session.get(ModuleRow, module_id)
        if row is None:
            return False
        if row.owner_company_id and str(row.owner_company_id) == tenant_id:
            return True
        if (row.owner_scope or _PLATFORM_OWNER) != _PLATFORM_OWNER:
            # Company-owned module: only its owner may bind resources to it.
            return False
        if (row.company_grant_scope or "").strip() == _ALL_GRANT_SCOPE:
            return True
        granted = await self.session.scalar(
            select(ModuleCompanyGrantRow.id)
            .where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.company_id == tenant_id,
                ModuleCompanyGrantRow.status == _ACTIVE,
            )
            .limit(1)
        )
        return granted is not None

    # -- owner scopes ---------------------------------------------------

    async def owner_scope_ids(self, tenant_id: str, kind: str) -> frozenset[str]:
        """Ids of ``kind`` scopes whose *owner* is ``tenant_id``."""
        if not tenant_id.strip():
            return frozenset()
        if kind == "company":
            return frozenset({tenant_id})
        if kind == "project":
            rows = await self.session.execute(
                select(ProjectRow.id).where(ProjectRow.company_id == tenant_id)
            )
            return frozenset(str(value) for value in rows.scalars().all() if value)
        if kind == "cabinet":
            rows = await self.session.execute(
                select(CabinetInstanceRow.id).where(
                    or_(
                        CabinetInstanceRow.owner_company_id == tenant_id,
                        and_(
                            CabinetInstanceRow.owner_company_id.is_(None),
                            CabinetInstanceRow.company_id == tenant_id,
                        ),
                    )
                )
            )
            return frozenset(str(value) for value in rows.scalars().all() if value)
        return frozenset()

    async def owner_scope_pairs(self, tenant_id: str) -> tuple[tuple[str, str], ...]:
        """Every ``(scope_kind, scope_id)`` pair the tenant may own resources in."""
        pairs: list[tuple[str, str]] = [("company", tenant_id)]
        for kind in ("cabinet", "project"):
            for scope_id in sorted(await self.owner_scope_ids(tenant_id, kind)):
                pairs.append((kind, scope_id))
        return tuple(pairs)

    async def tenant_of(self, kind: str, scope_id: str) -> str | None:
        """Owning tenant of an already-known scope, or ``None`` when unowned.

        Used to rebuild the tenant field of a stored resource after a write; the
        caller has already proved that this session may write the scope.
        """
        if kind == "company":
            return scope_id
        if kind == "project":
            proof = await self.project(scope_id)
            return proof.tenant_id if proof is not None else None
        if kind == "cabinet":
            return await self.cabinet_owner_tenant(scope_id)
        return None


__all__ = ["SqlAlchemyResourceScopeProof"]
