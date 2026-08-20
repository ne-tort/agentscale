"""Project use cases (M01)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.workspace import build_workspace_key
from prodavan.infrastructure.persistence.models.projects import Project
from prodavan.infrastructure.persistence.models.tenants import Cabinet, CabinetMembership, Tenant
from prodavan.infrastructure.persistence.rls import apply_rls
from prodavan.infrastructure.storage.project_storage import (
    init_project_storage,
    project_storage_uri,
    remove_project_storage,
    scan_project_stats,
)


class ProjectError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _new_project_id() -> str:
    return f"proj_{secrets.token_hex(4)}"


async def _ensure_cabinet_access(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
) -> tuple[Cabinet, Tenant]:
    cabinet = await session.get(Cabinet, cabinet_id)
    if cabinet is None or cabinet.tenant_id != tenant_id:
        raise ProjectError("CABINET_NOT_FOUND", "Cabinet not found", 404)
    if cabinet.status == "archived":
        raise ProjectError("CABINET_ARCHIVED", "Cabinet is archived", 409)

    membership = await session.execute(
        select(CabinetMembership).where(
            CabinetMembership.cabinet_id == cabinet_id,
            CabinetMembership.user_id == user_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise ProjectError("CABINET_ACCESS_DENIED", "No membership for cabinet", 403)

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise ProjectError("TENANT_NOT_FOUND", "Tenant not found", 404)
    return cabinet, tenant


async def create_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    slug: str,
    display_name: str,
) -> Project:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id, cabinet_id=cabinet_id)
    _, tenant = await _ensure_cabinet_access(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id
    )

    project_id = _new_project_id()
    workspace_key = build_workspace_key(
        tenant_slug=tenant.slug, cabinet_id=cabinet_id, project_id=project_id
    )
    project = Project(
        id=project_id,
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        slug=slug,
        display_name=display_name,
        workspace_key=workspace_key,
        created_by=user_id,
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ProjectError("SLUG_CONFLICT", "Project slug already exists in cabinet", 409) from exc

    try:
        init_project_storage(
            tenant_id=tenant_id,
            tenant_slug=tenant.slug,
            cabinet_id=cabinet_id,
            project_id=project_id,
            slug=slug,
            display_name=display_name,
            workspace_key=workspace_key,
        )
        await session.commit()
        await session.refresh(project)
        try:
            from prodavan.cabinets.events import emit_platform_event
            from prodavan.infrastructure.persistence.models.tenants import Cabinet

            cab = await session.get(Cabinet, cabinet_id)
            if cab and cab.profile_id:
                await emit_platform_event(
                    profile_id=cab.profile_id,
                    event_type="project.created",
                    tenant_id=tenant_id,
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    actor_user_id=user_id,
                    data={
                        "project_id": project_id,
                        "slug": slug,
                        "workspace_key": workspace_key,
                    },
                )
        except Exception:
            pass
        return project
    except Exception as exc:
        remove_project_storage(tenant_id, cabinet_id, project_id)
        await session.rollback()
        raise ProjectError("STORAGE_FAILED", str(exc), 500) from exc


async def list_projects(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    status: str = "active",
) -> list[Project]:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id, cabinet_id=cabinet_id)
    await _ensure_cabinet_access(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id
    )
    query = select(Project).where(Project.cabinet_id == cabinet_id)
    if status != "all":
        query = query.where(Project.status == status)
    result = await session.execute(query.order_by(Project.created_at.desc()))
    return list(result.scalars().all())


async def get_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
) -> Project:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id, cabinet_id=cabinet_id)
    await _ensure_cabinet_access(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id
    )
    project = await session.get(Project, project_id)
    if project is None or project.cabinet_id != cabinet_id:
        raise ProjectError("PROJECT_NOT_FOUND", "Project not found", 404)
    return project


async def archive_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
) -> None:
    project = await get_project(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    project.status = "archived"
    project.archived_at = datetime.now(UTC)
    await session.commit()


async def restore_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
) -> Project:
    project = await get_project(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    project.status = "active"
    project.archived_at = None
    await session.commit()
    await session.refresh(project)
    return project


async def open_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
) -> tuple[Project, dict]:
    project = await get_project(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    if project.status == "archived":
        raise ProjectError("PROJECT_ARCHIVED", "Cannot open archived project", 409)

    project.last_opened_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(project)

    base = project_storage_uri(tenant_id, cabinet_id, project_id)
    paths = {
        "inbox": f"{base}inbox/",
        "runs": f"{base}runs/",
        "export": f"{base}export/",
        "commerce_db": f"{base}commerce.sqlite",
    }
    return project, paths


def project_stats(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> dict:
    return scan_project_stats(tenant_id, cabinet_id, project_id)
