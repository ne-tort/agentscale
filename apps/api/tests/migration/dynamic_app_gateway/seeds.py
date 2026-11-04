"""Seed builders for the legacy gateway port tests.

Rows are written through the **real ORM classes**, so every assertion proves the
adapters agree with the registered models instead of with an invented schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from .sqlite_mirror import (
    APP_ID,
    CABINET_A,
    CAPABILITY_GRANT,
    DEFINITION_A,
    DEFINITION_B,
    DIGEST_A,
    DIGEST_B,
    EMPLOYEE_A,
    GRANT_A,
    GRANT_B,
    INSTANCE_A,
    INSTANCE_B,
    MANIFEST_VERSION,
    PROJECT_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    VERSION_A,
    VERSION_B,
)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
"""A fixed instant; the ports' clock is driven from it, never from the wall clock."""


def manifest(*, app_id: str = APP_ID, version: str = MANIFEST_VERSION) -> dict[str, Any]:
    """A copy of the accepted manifest, re-labelled for another app/version."""

    payload = {
        "protocol": "1.0",
        "appId": app_id,
        "version": version,
        "displayName": "Demo plugin",
        "requestedCapabilities": [{"name": "storage.kv", "reason": "persist drafts"}],
        "entrypoints": {
            "plugin": {
                "methods": [
                    {
                        "method": "kv.put",
                        "capability": "storage.kv",
                        "operation": "write",
                        "resource": "drafts",
                        "domain": "storage",
                    }
                ]
            }
        },
    }
    return payload


def definition(
    *,
    row_id: str = DEFINITION_A,
    tenant_id: str = TENANT_A,
    app_id: str = APP_ID,
    scope_kind: str = "tenant",
    scope_id: str | None = None,
    status: str = "published",
    current_version_id: str | None = VERSION_A,
) -> AppDefinitionRow:
    return AppDefinitionRow(
        id=row_id,
        tenant_id=tenant_id,
        app_id=app_id,
        display_name="Demo plugin",
        scope_kind=scope_kind,
        scope_id=scope_id if scope_id is not None else tenant_id,
        status=status,
        current_version_id=current_version_id,
        created_at=NOW,
        updated_at=NOW,
    )


def version(
    *,
    row_id: str = VERSION_A,
    definition_id: str = DEFINITION_A,
    manifest_version: str = MANIFEST_VERSION,
    digest: str = DIGEST_A,
    status: str = "published",
    payload: dict[str, Any] | None = None,
    requested: list[dict[str, Any]] | None = None,
) -> AppVersionRow:
    body = payload if payload is not None else manifest(version=manifest_version)
    return AppVersionRow(
        id=row_id,
        app_definition_id=definition_id,
        manifest_version=manifest_version,
        artifact_digest=digest,
        manifest=body,
        requested_capabilities=(
            requested
            if requested is not None
            else list(body.get("requestedCapabilities") or [])
        ),
        status=status,
        created_at=NOW,
        published_at=NOW if status == "published" else None,
    )


def grant(
    *,
    row_id: str = GRANT_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    scope_kind: str = "tenant",
    scope_id: str = TENANT_A,
    entries: list[dict[str, Any]] | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> AppGrantRow:
    return AppGrantRow(
        id=row_id,
        app_definition_id=definition_id,
        app_version_id=version_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        grants=entries if entries is not None else [dict(CAPABILITY_GRANT)],
        issued_at=issued_at or NOW - timedelta(days=1),
        expires_at=expires_at,
    )


def instance(
    *,
    row_id: str = INSTANCE_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    scope_kind: str = "tenant",
    scope_id: str = TENANT_A,
    status: str = "published",
) -> AppInstanceRow:
    return AppInstanceRow(
        id=row_id,
        app_definition_id=definition_id,
        app_version_id=version_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


async def seed_baseline(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT_A,
    definition_id: str = DEFINITION_A,
    version_id: str = VERSION_A,
    instance_id: str = INSTANCE_A,
    grant_id: str = GRANT_A,
    digest: str = DIGEST_A,
) -> None:
    """One published definition/version/instance with one unexpired capability grant."""

    async with session_factory() as session:
        session.add(
            definition(
                row_id=definition_id,
                tenant_id=tenant_id,
                status="published",
                current_version_id=version_id,
            )
        )
        await session.flush()
        session.add(version(row_id=version_id, definition_id=definition_id, digest=digest))
        await session.flush()
        session.add(
            grant(
                row_id=grant_id,
                definition_id=definition_id,
                version_id=version_id,
                scope_id=tenant_id,
            )
        )
        session.add(
            instance(
                row_id=instance_id,
                definition_id=definition_id,
                version_id=version_id,
                scope_id=tenant_id,
            )
        )
        await session.commit()


async def seed_second_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A complete, otherwise identical installation owned by the *other* tenant."""

    await seed_baseline(
        session_factory,
        tenant_id=TENANT_B,
        definition_id=DEFINITION_B,
        version_id=VERSION_B,
        instance_id=INSTANCE_B,
        grant_id=GRANT_B,
        digest=DIGEST_B,
    )


# ---------------------------------------------------------------------------
# Identity / scope rows: what makes a subject "able to act" in a tenant
# ---------------------------------------------------------------------------


def company(
    *,
    company_id: str = TENANT_A,
    status: str = "active",
    deleted_at: datetime | None = None,
) -> CompanyRow:
    return CompanyRow(
        id=company_id,
        name=f"Company {company_id}",
        login_slug=f"slug-{company_id[-8:]}",
        status=status,
        deleted_at=deleted_at,
    )


def employee(
    *,
    employee_id: str = EMPLOYEE_A,
    subject: str = SUBJECT_A,
    status: str = "active",
    deleted_at: datetime | None = None,
) -> EmployeeRow:
    return EmployeeRow(
        id=employee_id,
        keycloak_sub=subject,
        login=f"{employee_id}@agentscale.local",
        email=f"{employee_id}@agentscale.local",
        status=status,
        deleted_at=deleted_at,
    )


def membership(
    *,
    employee_id: str = EMPLOYEE_A,
    company_id: str = TENANT_A,
    role: str = "member",
) -> MembershipRow:
    return MembershipRow(
        id=f"mem_{company_id[-6:]}{employee_id[-6:]}",
        employee_id=employee_id,
        company_id=company_id,
        role=role,
    )


def cabinet(
    *,
    cabinet_id: str = CABINET_A,
    status: str = "active",
    owner_scope: str = "platform",
    owner_company_id: str | None = None,
    company_grant_scope: str = "selected",
) -> CabinetInstanceRow:
    return CabinetInstanceRow(
        id=cabinet_id,
        name=f"Cabinet {cabinet_id}",
        schema_name=f"cab_in_{cabinet_id[-8:]}",
        status=status,
        owner_scope=owner_scope,
        owner_company_id=owner_company_id,
        company_grant_scope=company_grant_scope,
    )


def cabinet_grant(
    *,
    cabinet_id: str = CABINET_A,
    company_id: str = TENANT_A,
    status: str = "active",
) -> CabinetCompanyGrantRow:
    return CabinetCompanyGrantRow(
        id=f"ccg_{cabinet_id[-6:]}{company_id[-6:]}",
        cabinet_id=cabinet_id,
        company_id=company_id,
        status=status,
    )


def cabinet_assignment(
    *,
    cabinet_id: str = CABINET_A,
    employee_id: str = EMPLOYEE_A,
    status: str = "active",
) -> CabinetEmployeeAssignmentRow:
    return CabinetEmployeeAssignmentRow(
        id=f"cas_{cabinet_id[-6:]}{employee_id[-6:]}",
        cabinet_id=cabinet_id,
        employee_id=employee_id,
        status=status,
    )


def project(
    *,
    project_id: str = PROJECT_A,
    company_id: str = TENANT_A,
    cabinet_id: str = CABINET_A,
    status: str = "active",
) -> ProjectRow:
    return ProjectRow(
        id=project_id,
        company_id=company_id,
        cabinet_id=cabinet_id,
        name=f"Project {project_id}",
        slug=project_id,
        status=status,
        visibility_mode="cabinet_shared",
        workspace_key=f"ws-{project_id}",
        container_ref=f"container-{project_id}",
    )


async def seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    company_id: str = TENANT_A,
    employee_id: str = EMPLOYEE_A,
    subject: str = SUBJECT_A,
    cabinet_id: str = CABINET_A,
    project_id: str = PROJECT_A,
) -> None:
    """One active tenant with one active member who may use one granted cabinet/project."""

    async with session_factory() as session:
        session.add(company(company_id=company_id))
        session.add(employee(employee_id=employee_id, subject=subject))
        await session.flush()
        session.add(membership(employee_id=employee_id, company_id=company_id))
        session.add(cabinet(cabinet_id=cabinet_id))
        await session.flush()
        session.add(cabinet_grant(cabinet_id=cabinet_id, company_id=company_id))
        session.add(cabinet_assignment(cabinet_id=cabinet_id, employee_id=employee_id))
        session.add(project(project_id=project_id, company_id=company_id, cabinet_id=cabinet_id))
        await session.commit()


async def seed_gateway_with_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The full happy path: a published plugin granted to an active member's tenant."""

    await seed_identity(session_factory)
    await seed_baseline(session_factory)


__all__ = [
    "NOW",
    "cabinet",
    "cabinet_assignment",
    "cabinet_grant",
    "company",
    "definition",
    "employee",
    "grant",
    "instance",
    "manifest",
    "membership",
    "project",
    "seed_baseline",
    "seed_gateway_with_identity",
    "seed_identity",
    "seed_second_tenant",
    "version",
]
