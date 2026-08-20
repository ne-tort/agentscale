"""Auth use cases: register, login, refresh."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import create_access_token
from prodavan.infrastructure.auth.password import hash_password, verify_password
from prodavan.infrastructure.persistence.models.tenants import (
    Cabinet,
    CabinetMembership,
    RefreshToken,
    Tenant,
    TenantMembership,
    User,
)
from prodavan.infrastructure.persistence.rls import apply_rls


@dataclass
class AuthUserInfo:
    id: uuid.UUID
    email: str
    display_name: str


@dataclass
class TenantInfo:
    id: uuid.UUID
    slug: str
    display_name: str


@dataclass
class AuthResult:
    access_token: str
    refresh_token: str
    expires_in: int
    user: AuthUserInfo
    tenants: list[TenantInfo]


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    tenant_slug: str,
    tenant_display_name: str,
) -> AuthResult:
    user = User(
        email=email.lower(),
        display_name=display_name,
        password_hash=hash_password(password),
    )
    tenant = Tenant(slug=tenant_slug, display_name=tenant_display_name)
    session.add_all([user, tenant])
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AuthError("EMAIL_OR_SLUG_TAKEN", "Email or tenant slug already exists") from exc

    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)

    cabinet = Cabinet(
        tenant_id=tenant.id,
        slug="main",
        display_name="Main",
    )
    session.add(cabinet)
    session.add(
        TenantMembership(tenant_id=tenant.id, user_id=user.id, tenant_role="tenant.owner")
    )
    await session.flush()

    session.add(
        CabinetMembership(
            cabinet_id=cabinet.id,
            user_id=user.id,
            cabinet_role="cabinet.admin",
        )
    )
    await session.flush()

    return await _issue_tokens(
        session,
        user=user,
        tenant=tenant,
        cabinet_ids=[cabinet.id],
    )


async def login(session: AsyncSession, *, email: str, password: str) -> AuthResult:
    result = await session.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("INVALID_CREDENTIALS", "Invalid email or password")

    memberships = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
    )
    rows = memberships.all()
    if not rows:
        raise AuthError("NO_TENANT", "User has no tenant membership")

    _, tenant = rows[0]
    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)

    cabinets_result = await session.execute(
        select(Cabinet.id).where(Cabinet.tenant_id == tenant.id)
    )
    cabinet_ids = list(cabinets_result.scalars().all())

    tenants = [TenantInfo(id=t.id, slug=t.slug, display_name=t.display_name) for _, t in rows]

    tokens = await _issue_tokens(
        session,
        user=user,
        tenant=tenant,
        cabinet_ids=cabinet_ids,
        tenants=tenants,
    )
    return tokens


async def refresh(session: AsyncSession, *, refresh_token: str) -> AuthResult:
    token_hash = _hash_token(refresh_token)
    now = datetime.now(UTC)
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    stored = result.scalar_one_or_none()
    if stored is None:
        raise AuthError("INVALID_REFRESH", "Refresh token invalid or expired")

    user_result = await session.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one()
    memberships = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
    )
    rows = memberships.all()
    if not rows:
        raise AuthError("NO_TENANT", "User has no tenant membership")

    _, tenant = rows[0]
    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)
    cabinets_result = await session.execute(
        select(Cabinet.id).where(Cabinet.tenant_id == tenant.id)
    )
    cabinet_ids = list(cabinets_result.scalars().all())

    stored.revoked_at = now
    return await _issue_tokens(
        session,
        user=user,
        tenant=tenant,
        cabinet_ids=cabinet_ids,
        tenants=[TenantInfo(id=t.id, slug=t.slug, display_name=t.display_name) for _, t in rows],
    )


async def _issue_tokens(
    session: AsyncSession,
    *,
    user: User,
    tenant: Tenant,
    cabinet_ids: list[uuid.UUID],
    tenants: list[TenantInfo] | None = None,
) -> AuthResult:
    access_token = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        cabinet_ids=cabinet_ids,
    )
    refresh_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh_token),
            expires_at=expires_at,
        )
    )
    await session.commit()

    tenant_list = tenants or [
        TenantInfo(id=tenant.id, slug=tenant.slug, display_name=tenant.display_name)
    ]
    return AuthResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_seconds,
        user=AuthUserInfo(id=user.id, email=user.email, display_name=user.display_name),
        tenants=tenant_list,
    )
