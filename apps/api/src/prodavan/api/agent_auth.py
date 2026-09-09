"""FastAPI auth for pod agent bridge (Pod Identity Bridge JWT primary)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import get_current_employee, get_principal
from prodavan.application.pod_identity.bridge import (
    PodBridgeClaims,
    peek_pod_bridge_token,
    verify_pod_bridge_token,
)
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
    bridge: PodBridgeClaims | None = None


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    return token or None


def _pod_agent_shared_token_from_request(request: Request) -> str | None:
    token = _bearer_token(request)
    if not token:
        return None
    expected = settings.pod_agent_bridge_auth_token.strip()
    if not token or not expected:
        return None
    if secrets.compare_digest(token, expected):
        return token
    return None


# Back-compat for unit tests
def _pod_agent_token_from_request(request: Request) -> str | None:
    return _pod_agent_shared_token_from_request(request)


async def resolve_pod_bridge_from_request(request: Request) -> PodBridgeClaims | None:
    token = _bearer_token(request)
    if not token:
        return None
    peeked = peek_pod_bridge_token(token)
    if not peeked or peeked.get("typ") != "pod_bridge":
        return None
    return await verify_pod_bridge_token(token)


def require_bridge_project(bridge: PodBridgeClaims, project_id: str) -> None:
    bridge.require_project(project_id)


def require_bridge_pod(bridge: PodBridgeClaims, pod_id: str) -> None:
    if bridge.pod_id != pod_id:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="pod bridge pod_id mismatch",
        )


async def get_agent_auth(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AgentAuth:
    # Prefer scoped Bridge JWT (cluster shared Bearer is rejected for pod surface).
    bridge = await resolve_pod_bridge_from_request(request)
    if bridge is not None:
        return AgentAuth(principal=None, employee=None, pod_agent=True, bridge=bridge)
    if _pod_agent_shared_token_from_request(request) is not None:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="shared pod token disabled; use Pod Identity Bridge JWT",
        )
    try:
        principal = await get_principal(request)
    except AppError:
        raise
    employee = await get_current_employee(principal, session)
    return AgentAuth(principal=principal, employee=employee, pod_agent=False, bridge=None)


async def get_pod_bridge_auth(request: Request) -> PodBridgeClaims:
    """Require Pod Identity Bridge JWT (shared cluster Bearer is rejected)."""
    token = _bearer_token(request)
    if not token:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="pod bridge bearer required",
        )
    if _pod_agent_shared_token_from_request(request) is not None:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="shared pod token cannot access tenant infra / module surface",
        )
    return await verify_pod_bridge_token(token)


AgentAuthDep = Annotated[AgentAuth, Depends(get_agent_auth)]
PodBridgeDep = Annotated[PodBridgeClaims, Depends(get_pod_bridge_auth)]
