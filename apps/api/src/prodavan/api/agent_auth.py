"""FastAPI auth for pod agent bridge (L09 service token)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import get_current_employee, get_principal
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.database import get_db_session
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


@dataclass(frozen=True)
class AgentAuth:
    principal: Principal | None
    employee: EmployeeRow | None
    pod_agent: bool


def _pod_agent_token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    expected = settings.pod_agent_bridge_auth_token.strip()
    if not token or not expected:
        return None
    if secrets.compare_digest(token, expected):
        return token
    return None


async def get_agent_auth(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AgentAuth:
    if _pod_agent_token_from_request(request) is not None:
        return AgentAuth(principal=None, employee=None, pod_agent=True)
    try:
        principal = await get_principal(request)
    except AppError:
        raise
    employee = await get_current_employee(principal, session)
    return AgentAuth(principal=principal, employee=employee, pod_agent=False)


AgentAuthDep = Annotated[AgentAuth, Depends(get_agent_auth)]
