"""FastAPI auth dependencies (L01)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.auth.auth_sessions import AuthSessionConflict, record_verified_bearer_session
from prodavan.application.identity.service import EntitlementService
from prodavan.config.settings import settings
from prodavan.core.trace_context import current_trace_id
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal, WorkContext
from prodavan.infrastructure.auth.jwt import get_jwt_validator
from prodavan.infrastructure.persistence.database import get_db_session
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

HEADER_CABINET = "X-Cabinet-Id"
HEADER_PROJECT = "X-Project-Id"

#: Redacted code recorded when the verified session could not be stored. The request itself
#: stays authenticated: the bearer token was verified above, and the row is evidence for
#: *later* callers (a worker, a second replica), not a condition of this one.
AUTH_SESSION_NOT_RECORDED = "auth_session_not_recorded"
AUTH_SESSION_CONFLICT = "auth_session_conflict"


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
    principal = get_jwt_validator().validate(token)
    await _record_auth_session(request, token=token, principal=principal)
    return principal


def _verified_claims(token: str, principal: Principal) -> dict[str, Any]:
    """Claims of a token ``get_jwt_validator().validate`` has already accepted.

    ``_unverified_payload`` is used here for the one thing it is documented for: reading
    ``exp``/scope claims out of the very string that was just verified. The subject is taken
    from the validator's answer, which always wins over the payload.
    """

    from prodavan.application.migration.principal_resolver import _unverified_payload

    claims: dict[str, Any] = dict(_unverified_payload(token))
    claims["sub"] = principal.sub
    return claims


async def _record_auth_session(
    request: Request,
    *,
    token: str,
    principal: Principal,
) -> None:
    """Persist ``correlation_id -> verified subject`` for callers that hold no token.

    This is the writer's one call site (``application/auth/auth_sessions.py::
    AUTH_SESSION_WRITER_CALL_SITE``). It runs **after** ``validate`` above, so the subject it
    stores is the validator's answer, never a decoded payload; the correlation id is the
    request's own trace id, which the caller sent as ``X-Trace-Id`` and which the job it
    starts will carry. With ``AUTH_SESSION_STORE_ENABLED`` off nothing is written and the
    request behaves exactly as it did before this table existed.
    """

    if not settings.auth_session_store_enabled:
        return
    correlation_id = str(getattr(request.state, "trace_id", "") or current_trace_id() or "").strip()
    if not correlation_id:
        # Nothing durable to key the row on; the resolver stays request-scoped for this call.
        logger.info("%s reason=correlation_id_missing", AUTH_SESSION_NOT_RECORDED)
        return
    try:
        await record_verified_bearer_session(
            token=token,
            correlation_id=correlation_id,
            claims=_verified_claims(token, principal),
        )
    except AuthSessionConflict:
        # The id is already bound to a different verified credential. Not overwritten (a job
        # may already carry it); the event is recorded by code and the request continues.
        logger.warning("%s correlation=%s", AUTH_SESSION_CONFLICT, correlation_id)
    except Exception as exc:  # noqa: BLE001 - an auxiliary write must not fail an auth call
        logger.warning(
            "%s correlation=%s error=%s",
            AUTH_SESSION_NOT_RECORDED,
            correlation_id,
            type(exc).__name__,
        )


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
