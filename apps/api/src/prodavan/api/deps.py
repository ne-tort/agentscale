"""FastAPI auth dependencies (L01)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.identity.service import EntitlementService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal, WorkContext
from prodavan.infrastructure.auth.jwt import get_jwt_validator
from prodavan.infrastructure.persistence.database import get_db_session
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

HEADER_CABINET = "X-Cabinet-Id"
HEADER_PROJECT = "X-Project-Id"


async def get_principal(request: Request) -> Principal:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="Bearer token required",
        )
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="Bearer token required",
        )
    return get_jwt_validator().validate(token)


async def get_work_context(
    x_cabinet_id: Annotated[str | None, Header(alias=HEADER_CABINET)] = None,
    x_project_id: Annotated[str | None, Header(alias=HEADER_PROJECT)] = None,
) -> WorkContext:
    return WorkContext(cabinet_id=x_cabinet_id, project_id=x_project_id)


async def get_current_employee(
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EmployeeRow | None:
    return await EntitlementService(session).ensure_active_employee(principal)


async def require_platform_admin(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if not principal.is_platform_admin:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="platform.admin required",
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]
WorkContextDep = Annotated[WorkContext, Depends(get_work_context)]
PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
