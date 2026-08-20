"""FastAPI dependencies."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.auth.jwt import TokenError, decode_access_token
from prodavan.infrastructure.persistence.database import get_db_session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    cabinet_ids: list[uuid.UUID]
    role: str = "user"
    active_cabinet_id: uuid.UUID | None = None
    active_project_id: str | None = None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform.admin"


async def get_session() -> AsyncSession:
    async for session in get_db_session():
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Missing bearer token"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": str(exc)},
        ) from exc

    active_raw = payload.get("active_cabinet_id")
    return CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=uuid.UUID(payload["tenant_id"]),
        cabinet_ids=[uuid.UUID(cid) for cid in payload.get("cabinet_ids", [])],
        role=str(payload.get("role") or "user"),
        active_cabinet_id=uuid.UUID(active_raw) if active_raw else None,
        active_project_id=payload.get("active_project_id"),
    )


async def require_platform_admin(
    current: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current.is_platform_admin:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "platform.admin role required"},
        )
    return current


@dataclass
class CabinetContext:
    user: CurrentUser
    cabinet_id: uuid.UUID


async def get_cabinet_context(
    current: CurrentUser = Depends(get_current_user),
    x_cabinet_id: str | None = Header(default=None, alias="X-Cabinet-Id"),
) -> CabinetContext:
    cabinet_raw = x_cabinet_id or (
        str(current.active_cabinet_id) if current.active_cabinet_id else None
    )
    if cabinet_raw is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_ACTIVE_CABINET", "message": "Active cabinet required"},
        )
    try:
        cabinet_id = uuid.UUID(cabinet_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CABINET_ID", "message": "Invalid cabinet id"},
        ) from exc
    if cabinet_id not in current.cabinet_ids:
        raise HTTPException(
            status_code=403,
            detail={"code": "CABINET_ACCESS_DENIED", "message": "Cabinet not in token"},
        )
    return CabinetContext(user=current, cabinet_id=cabinet_id)


@dataclass
class CabinetSession:
    ctx: CabinetContext
    session: AsyncSession


async def get_cabinet_session(
    ctx: CabinetContext = Depends(get_cabinet_context),
    session: AsyncSession = Depends(get_session),
) -> CabinetSession:
    """Session with tenant + cabinet RLS."""
    from prodavan.infrastructure.persistence.rls import apply_rls

    await apply_rls(
        session,
        user_id=ctx.user.user_id,
        tenant_id=ctx.user.tenant_id,
        cabinet_id=ctx.cabinet_id,
    )
    return CabinetSession(ctx=ctx, session=session)


async def get_authenticated_session(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    """Session with RLS applied for the authenticated tenant."""
    from prodavan.infrastructure.persistence.rls import apply_rls

    await apply_rls(
        session,
        user_id=current.user_id,
        tenant_id=current.tenant_id,
    )
    return session
