"""Auth use cases: login by company id, refresh, profile, password, platform seed."""

from __future__ import annotations

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

ROLE_PLATFORM_ADMIN = "platform.admin"
ROLE_USER = "user"
PLATFORM_TENANT_SLUG = "_platform"


@dataclass
class AuthUserInfo:
    id: uuid.UUID
    login_id: str
    company_name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    role: str
    status: str

    @property
    def display_name(self) -> str:
        return self.contact_person or self.company_name


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
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _user_info(user: User) -> AuthUserInfo:
    return AuthUserInfo(
        id=user.id,
        login_id=user.login_id,
        company_name=user.company_name,
        contact_person=user.contact_person,
        phone=user.phone,
        email=user.email,
        role=user.role,
        status=user.status,
    )


def _assert_login_allowed(user: User) -> None:
    if user.status == "suspended":
        raise AuthError("ACCOUNT_SUSPENDED", "Account is suspended", 403)
    if user.status == "deleted" or user.deleted_at is not None:
        raise AuthError("ACCOUNT_DELETED", "Account is deleted", 403)
    if user.status != "active":
        raise AuthError("ACCOUNT_INACTIVE", "Account is not active", 403)


async def ensure_platform_admin(session: AsyncSession) -> User | None:
    """Create bootstrap platform.admin from env if missing. Returns admin or None if unset."""
    login_id = (settings.platform_admin_id or "").strip()
    password = settings.platform_admin_password or ""
    if not login_id or not password:
        return None

    existing = await session.execute(select(User).where(User.login_id == login_id))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user

    tenant_result = await session.execute(select(Tenant).where(Tenant.slug == PLATFORM_TENANT_SLUG))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=PLATFORM_TENANT_SLUG, display_name="Platform")
        session.add(tenant)
        await session.flush()

    user = User(
        login_id=login_id,
        company_name="Platform",
        contact_person="Platform Admin",
        email=None,
        display_name="Platform Admin",
        password_hash=hash_password(password),
        role=ROLE_PLATFORM_ADMIN,
        status="active",
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await session.execute(select(User).where(User.login_id == login_id))
        return existing.scalar_one_or_none()

    session.add(
        TenantMembership(tenant_id=tenant.id, user_id=user.id, tenant_role="tenant.owner")
    )
    await session.commit()
    await session.refresh(user)
    return user


async def create_company_user(
    session: AsyncSession,
    *,
    login_id: str,
    company_name: str,
    password: str,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> tuple[User, Tenant]:
    """Provision company user + tenant + default main cabinet (admin registration)."""
    normalized_login = login_id.strip().lower()
    email_norm = email.lower().strip() if email else None
    display = (contact_person or company_name).strip()

    user = User(
        login_id=normalized_login,
        company_name=company_name.strip(),
        contact_person=contact_person.strip() if contact_person else None,
        phone=phone.strip() if phone else None,
        email=email_norm,
        display_name=display,
        password_hash=hash_password(password),
        role=ROLE_USER,
        status="active",
    )
    tenant = Tenant(slug=normalized_login, display_name=company_name.strip())
    session.add_all([user, tenant])
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AuthError("LOGIN_OR_SLUG_TAKEN", "login_id or company already exists", 409) from exc

    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)

    cabinet = Cabinet(
        tenant_id=tenant.id,
        slug="main",
        display_name="Main",
        created_by=user.id,
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
    await session.commit()
    await session.refresh(user)
    await session.refresh(tenant)
    return user, tenant


async def login(session: AsyncSession, *, login_id: str, password: str) -> AuthResult:
    result = await session.execute(select(User).where(User.login_id == login_id.strip().lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("INVALID_CREDENTIALS", "Invalid login_id or password", 401)

    _assert_login_allowed(user)

    memberships = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
    )
    rows = memberships.all()
    if not rows:
        raise AuthError("NO_TENANT", "User has no tenant membership", 403)

    _, tenant = rows[0]
    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)

    cabinets_result = await session.execute(
        select(Cabinet.id).where(
            Cabinet.tenant_id == tenant.id,
            Cabinet.status != "archived",
        )
    )
    cabinet_ids = list(cabinets_result.scalars().all())

    tenants = [TenantInfo(id=t.id, slug=t.slug, display_name=t.display_name) for _, t in rows]

    return await _issue_tokens(
        session,
        user=user,
        tenant=tenant,
        cabinet_ids=cabinet_ids,
        tenants=tenants,
    )


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
        raise AuthError("INVALID_REFRESH", "Refresh token invalid or expired", 401)

    user_result = await session.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one()
    _assert_login_allowed(user)

    memberships = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
    )
    rows = memberships.all()
    if not rows:
        raise AuthError("NO_TENANT", "User has no tenant membership", 403)

    _, tenant = rows[0]
    await apply_rls(session, user_id=user.id, tenant_id=tenant.id)
    cabinets_result = await session.execute(
        select(Cabinet.id).where(
            Cabinet.tenant_id == tenant.id,
            Cabinet.status != "archived",
        )
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


async def update_profile(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    fields_set: set[str] | None = None,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    _assert_login_allowed(user)

    set_fields = fields_set or set()
    if "contact_person" in set_fields:
        user.contact_person = contact_person.strip() if contact_person else None
        user.display_name = user.contact_person or user.company_name
    if "phone" in set_fields:
        user.phone = phone.strip() if phone else None
    if "email" in set_fields:
        user.email = email.lower().strip() if email else None

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AuthError("EMAIL_TAKEN", "Email already in use", 409) from exc
    await session.refresh(user)
    return user


async def change_password(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    _assert_login_allowed(user)
    if not verify_password(current_password, user.password_hash):
        raise AuthError("INVALID_PASSWORD", "Current password is incorrect", 400)

    user.password_hash = hash_password(new_password)
    now = datetime.now(UTC)
    tokens = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for token in tokens.scalars().all():
        token.revoked_at = now
    await session.commit()


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
        role=user.role,
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
        user=_user_info(user),
        tenants=tenant_list,
    )
