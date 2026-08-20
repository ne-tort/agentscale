"""Cabinet use cases (M00)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.services.pack_seeder import SeedError, run_pack_seed
from prodavan.infrastructure.persistence.models.tenants import (
    Cabinet,
    CabinetMembership,
    CabinetProfile,
)
from prodavan.infrastructure.persistence.rls import apply_rls
from prodavan.infrastructure.storage.local_storage import remove_cabinet_storage


class CabinetError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _storage_uri(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> str:
    return f"prodavan://storage/cabinets/{tenant_id}/{cabinet_id}/"


async def create_cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    slug: str,
    display_name: str,
    profile_id: str,
) -> Cabinet:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id)

    profile = await session.get(CabinetProfile, profile_id)
    if profile is None:
        raise CabinetError("PROFILE_NOT_FOUND", "Unknown profile_id", 404)
    if profile.deprecated:
        raise CabinetError("PROFILE_DEPRECATED", "Profile is deprecated", 422)

    cabinet = Cabinet(
        tenant_id=tenant_id,
        slug=slug,
        display_name=display_name,
        profile_id=profile_id,
        profile_version=profile.version,
        status="provisioning",
        created_by=user_id,
    )
    session.add(cabinet)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise CabinetError("SLUG_CONFLICT", "Cabinet slug already exists", 409) from exc

    session.add(
        CabinetMembership(cabinet_id=cabinet.id, user_id=user_id, cabinet_role="cabinet.admin")
    )
    await session.flush()

    try:
        capabilities = await run_pack_seed(session, cabinet=cabinet, profile_id=profile_id)
        cabinet.capabilities = capabilities
        # Provision cabinet-owned DB via SPI (never core Alembic).
        from prodavan.cabinets.events import spi_context
        from prodavan.cabinets.registry import get_module_for_profile

        module = get_module_for_profile(profile_id)
        await module.migrate(
            spi_context(
                tenant_id=tenant_id,
                cabinet_id=cabinet.id,
                user_id=user_id,
            )
        )
        runtime = dict(capabilities.get("runtime") or {})
        runtime["db"] = "cabinet.sqlite"
        runtime["pack_id"] = module.pack_id
        capabilities = {**capabilities, "runtime": runtime}
        cabinet.capabilities = capabilities
        cabinet.status = "active"
        await session.commit()
        await session.refresh(cabinet)
        return cabinet
    except SeedError as exc:
        remove_cabinet_storage(tenant_id, cabinet.id)
        await session.rollback()
        raise CabinetError("SEED_FAILED", f"{exc.step}: {exc.message}", 500) from exc


async def list_cabinets(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str = "active",
) -> list[Cabinet]:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id)
    query = select(Cabinet).where(Cabinet.tenant_id == tenant_id)
    if status != "all":
        query = query.where(Cabinet.status == status)
    result = await session.execute(query.order_by(Cabinet.created_at.desc()))
    return list(result.scalars().all())


async def get_cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
) -> Cabinet:
    await apply_rls(session, user_id=user_id, tenant_id=tenant_id)
    cabinet = await session.get(Cabinet, cabinet_id)
    if cabinet is None or cabinet.tenant_id != tenant_id:
        raise CabinetError("CABINET_NOT_FOUND", "Cabinet not found", 404)
    return cabinet


async def archive_cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
) -> None:
    cabinet = await get_cabinet(session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id)
    cabinet.status = "archived"
    cabinet.archived_at = datetime.now(UTC)
    await session.commit()


async def restore_cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
) -> Cabinet:
    cabinet = await get_cabinet(session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id)
    cabinet.status = "active"
    cabinet.archived_at = None
    await session.commit()
    await session.refresh(cabinet)
    return cabinet


async def switch_cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
) -> tuple[Cabinet, list[uuid.UUID]]:
    cabinet = await get_cabinet(session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id)
    if cabinet.status == "archived":
        raise CabinetError("CABINET_ARCHIVED", "Cannot switch to archived cabinet", 409)

    membership = await session.execute(
        select(CabinetMembership).where(
            CabinetMembership.cabinet_id == cabinet_id,
            CabinetMembership.user_id == user_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise CabinetError("CABINET_ACCESS_DENIED", "No membership for cabinet", 403)

    all_cabinets = await list_cabinets(
        session, tenant_id=tenant_id, user_id=user_id, status="active"
    )
    cabinet_ids = [c.id for c in all_cabinets]
    return cabinet, cabinet_ids
