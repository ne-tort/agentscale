"""Admin use cases: company users CRUD and platform stats."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.services.auth_service import (
    ROLE_PLATFORM_ADMIN,
    ROLE_USER,
    AuthError,
    create_company_user,
)
from prodavan.infrastructure.auth.password import hash_password
from prodavan.infrastructure.persistence.models.tenants import (
    RefreshToken,
    Tenant,
    TenantMembership,
    User,
)


@dataclass
class AdminUserRow:
    user: User
    tenant_id: uuid.UUID | None
    tenant_slug: str | None


async def list_users(session: AsyncSession, *, include_deleted: bool = False) -> list[AdminUserRow]:
    query = (
        select(User, Tenant.id, Tenant.slug)
        .outerjoin(TenantMembership, TenantMembership.user_id == User.id)
        .outerjoin(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(User.role == ROLE_USER)
        .order_by(User.created_at.desc())
    )
    if not include_deleted:
        query = query.where(User.status != "deleted")
    rows = await session.execute(query)
    return [
        AdminUserRow(user=user, tenant_id=tenant_id, tenant_slug=tenant_slug)
        for user, tenant_id, tenant_slug in rows.all()
    ]


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> AdminUserRow:
    result = await session.execute(
        select(User, Tenant.id, Tenant.slug)
        .outerjoin(TenantMembership, TenantMembership.user_id == User.id)
        .outerjoin(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(User.id == user_id, User.role == ROLE_USER)
    )
    row = result.first()
    if row is None:
        raise AuthError("USER_NOT_FOUND", "User not found", 404)
    user, tenant_id, tenant_slug = row
    return AdminUserRow(user=user, tenant_id=tenant_id, tenant_slug=tenant_slug)


async def create_user(
    session: AsyncSession,
    *,
    login_id: str,
    company_name: str,
    password: str,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> AdminUserRow:
    user, tenant = await create_company_user(
        session,
        login_id=login_id,
        company_name=company_name,
        password=password,
        contact_person=contact_person,
        phone=phone,
        email=email,
    )
    return AdminUserRow(user=user, tenant_id=tenant.id, tenant_slug=tenant.slug)


async def update_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None = None,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    password: str | None = None,
    fields_set: set[str] | None = None,
) -> AdminUserRow:
    row = await get_user(session, user_id)
    user = row.user
    set_fields = fields_set or set()

    if status is not None:
        if status not in {"active", "suspended"}:
            raise AuthError("INVALID_STATUS", "status must be active or suspended", 422)
        if user.status == "deleted":
            raise AuthError("ACCOUNT_DELETED", "Cannot update deleted user", 409)
        user.status = status

    if "contact_person" in set_fields:
        user.contact_person = contact_person.strip() if contact_person else None
        user.display_name = user.contact_person or user.company_name
    if "phone" in set_fields:
        user.phone = phone.strip() if phone else None
    if "email" in set_fields:
        user.email = email.lower().strip() if email else None
    if password is not None:
        user.password_hash = hash_password(password)
        now = datetime.now(UTC)
        tokens = await session.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        for token in tokens.scalars().all():
            token.revoked_at = now

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AuthError("EMAIL_TAKEN", "Email already in use", 409) from exc
    await session.refresh(user)
    return await get_user(session, user_id)


async def soft_delete_user(session: AsyncSession, *, user_id: uuid.UUID) -> AdminUserRow:
    row = await get_user(session, user_id)
    user = row.user
    if user.role == ROLE_PLATFORM_ADMIN:
        raise AuthError("FORBIDDEN", "Cannot delete platform admin", 403)
    now = datetime.now(UTC)
    user.status = "deleted"
    user.deleted_at = now
    tokens = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for token in tokens.scalars().all():
        token.revoked_at = now
    await session.commit()
    await session.refresh(user)
    return await get_user(session, user_id)


@dataclass
class AdminStats:
    users_total: int
    users_by_status: dict[str, int]
    tenants_total: int
    cabinets_total: int
    projects_total: int
    runs_total: int


async def get_stats(session: AsyncSession) -> AdminStats:
    status_rows = await session.execute(
        select(User.status, func.count())
        .where(User.role == ROLE_USER)
        .group_by(User.status)
    )
    users_by_status = {status: count for status, count in status_rows.all()}

    totals = (
        await session.execute(text("SELECT * FROM tenants.platform_stats()"))
    ).one()
    users_total, tenants_total, cabinets_total, projects_total = totals

    return AdminStats(
        users_total=int(users_total or 0),
        users_by_status=users_by_status,
        tenants_total=int(tenants_total or 0),
        cabinets_total=int(cabinets_total or 0),
        projects_total=int(projects_total or 0),
        runs_total=0,
    )
