"""Server-derived tenant / cabinet / project scope for Content API calls.

Rule (see ``CONTRACT.md`` §2): **tenant and scope come only from the server
principal.** A path or query identifier is a *selector*, never a grant. Every
identifier a caller supplies is compared against the set derived here and a
mismatch is reported as ``404 NOT_FOUND`` so the existence of a foreign tenant's
object is not disclosed.

Nothing in this module trusts request bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.relations.query import RelationsQuery
from prodavan.domain.content.types import ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.content import ContentAssetRow, ContentAliasRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def not_found(detail: str = "resource not found") -> AppError:
    """Uniform 404 so a hidden resource is indistinguishable from a missing one."""
    return AppError(code="NOT_FOUND", title="Not Found", status=404, detail=detail)


def forbidden(detail: str) -> AppError:
    return AppError(code="FORBIDDEN", title="Forbidden", status=403, detail=detail)


@dataclass(frozen=True, slots=True)
class ContentActorScope:
    """What the server knows about the caller. Never built from request bodies."""

    employee_id: str | None
    tenant_ids: frozenset[str] = frozenset()
    role_keys: frozenset[str] = frozenset()
    is_platform_admin: bool = False
    cabinet_id: str | None = None
    project_id: str | None = None
    #: cabinet/project ids the caller is actually attached to (not the header).
    cabinet_ids: frozenset[str] = field(default_factory=frozenset)
    project_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_employee(self) -> bool:
        return self.employee_id is not None

    def require_employee(self) -> str:
        if self.employee_id is None and not self.is_platform_admin:
            raise forbidden("employee required")
        return self.employee_id or ""

    def tenant_or_404(self, company_id: str | None) -> str | None:
        """Validate a caller-selected tenant id against the server tenant set."""
        if company_id is None or company_id == "":
            return None
        if self.is_platform_admin:
            return company_id
        if company_id not in self.tenant_ids:
            raise not_found("company not found")
        return company_id

    def tenant_or_404_for(self, company_id: str | None, *, detail: str) -> str | None:
        try:
            return self.tenant_or_404(company_id)
        except AppError as exc:
            raise not_found(detail) from exc

    def owns_tenant(self, company_id: str | None) -> bool:
        if self.is_platform_admin:
            return True
        return bool(company_id) and company_id in self.tenant_ids

    def require_cabinet(self, cabinet_id: str) -> str:
        if self.is_platform_admin:
            return cabinet_id
        if cabinet_id not in self.cabinet_ids:
            raise not_found("cabinet not found")
        return cabinet_id

    def require_project(self, project_id: str) -> str:
        if self.is_platform_admin:
            return project_id
        if project_id not in self.project_ids:
            raise not_found("project not found")
        return project_id

    def acl_principal_keys(self) -> list[tuple[str, str]]:
        """Exact ``(principal_kind, principal_id)`` pairs this caller matches."""
        keys: list[tuple[str, str]] = []
        if self.employee_id:
            keys.append(("employee", self.employee_id))
        for company_id in sorted(self.tenant_ids):
            keys.append(("company", company_id))
        for cabinet_id in sorted(self.cabinet_ids):
            keys.append(("cabinet", cabinet_id))
        for project_id in sorted(self.project_ids):
            keys.append(("project", project_id))
        for role in sorted(self.role_keys):
            keys.append(("role", role))
        if self.is_platform_admin:
            keys.append(("platform", "platform"))
        return keys


async def resolve_actor_scope(
    session: AsyncSession,
    *,
    principal: Principal,
    employee: EmployeeRow | None,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> ContentActorScope:
    """Derive the caller's scope from persisted relations plus the verified JWT.

    ``cabinet_id``/``project_id`` normally come from ``X-Cabinet-Id`` /
    ``X-Project-Id``. They are accepted only when the caller is attached to that
    cabinet/project; otherwise the header value is dropped (never trusted).
    """
    roles = frozenset(str(role) for role in (principal.roles or ()))
    if employee is None:
        return ContentActorScope(
            employee_id=None,
            role_keys=roles,
            is_platform_admin=principal.is_platform_admin,
            cabinet_id=None,
            project_id=None,
        )

    relations = RelationsQuery(session)
    tenant_ids = frozenset(await relations.company_ids_for_employee(employee.id))

    cookie_cabinet = cabinet_id if cabinet_id else None
    cookie_project = project_id if project_id else None

    cabinet_ids: set[str] = set()
    project_ids: set[str] = set()
    if cookie_cabinet:
        allowed = principal.is_platform_admin or await relations.has_cabinet_assignment(
            cabinet_id=cookie_cabinet, employee_id=employee.id
        )
        if not allowed:
            # A company grant also conveys cabinet visibility.
            for company_id in tenant_ids:
                if await relations.has_cabinet_company_grant(
                    cabinet_id=cookie_cabinet, company_id=company_id
                ):
                    allowed = True
                    break
        if allowed:
            cabinet_ids.add(cookie_cabinet)
    if cookie_project:
        allowed = principal.is_platform_admin or await relations.has_project_assignment(
            project_id=cookie_project, employee_id=employee.id
        )
        if allowed:
            project_ids.add(cookie_project)

    role_keys = set(roles)
    for role in await _membership_roles(session, employee.id):
        if role:
            role_keys.add(str(role))

    return ContentActorScope(
        employee_id=employee.id,
        tenant_ids=tenant_ids,
        role_keys=frozenset(role_keys),
        is_platform_admin=principal.is_platform_admin,
        cabinet_id=cookie_cabinet if cookie_cabinet in cabinet_ids else None,
        project_id=cookie_project if cookie_project in project_ids else None,
        cabinet_ids=frozenset(cabinet_ids),
        project_ids=frozenset(project_ids),
    )


async def _membership_roles(session: AsyncSession, employee_id: str) -> set[str]:
    q = await session.execute(
        select(MembershipRow.role).where(MembershipRow.employee_id == employee_id)
    )
    return {str(role) for role in q.scalars().all() if role}


def asset_tenant_visible(asset: ContentAssetRow, scope: ContentActorScope) -> bool:
    """Tenant boundary for an asset, checked before any ACL evaluation.

    Platform owned assets are published content: any authenticated caller may
    reach the ACL decision. Company owned assets are tenant private, so a caller
    outside the owning tenant must see ``404`` and never ``403``.
    """
    if scope.is_platform_admin:
        return True
    if asset.owner_company_id is None:
        return True
    return asset.owner_company_id in scope.tenant_ids


def alias_tenant_visible(alias: ContentAliasRow, scope: ContentActorScope) -> bool:
    if scope.is_platform_admin:
        return True
    if alias.owner_company_id is None:
        return True
    return alias.owner_company_id in scope.tenant_ids


def asset_owner_scope(*, owner_company_id: str | None) -> str:
    """``owner_scope`` is derived from the validated owner, never from the body."""
    return "company" if owner_company_id else "platform"


def is_company_private(*, visibility: str, owner_company_id: str | None) -> bool:
    return visibility == ContentVisibility.COMPANY and bool(owner_company_id)


async def load_cabinet(session: AsyncSession, cabinet_id: str) -> CabinetInstanceRow | None:
    return await session.get(CabinetInstanceRow, cabinet_id)


async def load_project(session: AsyncSession, project_id: str) -> ProjectRow | None:
    return await session.get(ProjectRow, project_id)


__all__ = [
    "ContentActorScope",
    "resolve_actor_scope",
    "asset_tenant_visible",
    "alias_tenant_visible",
    "asset_owner_scope",
    "is_company_private",
    "not_found",
    "forbidden",
    "load_cabinet",
    "load_project",
]
