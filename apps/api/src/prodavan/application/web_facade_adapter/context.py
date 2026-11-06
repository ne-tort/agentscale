"""Facade context construction for the browser SSE transport.

``WebApiAuthContext.to_facade_context()`` intentionally does not carry a
request session. The transport must therefore set the session from the
server-side resolver and re-run the same scope boundary that the facade adapter
enforces, instead of trusting a JWT claim or an event payload.
"""

from __future__ import annotations

from dataclasses import replace

from prodavan.application.web_api.dependencies import WebApiAuthContext, WebApiScope
from prodavan.application.web_facade.auth import ScopeBoundary, ScopeViolation, WebRequestContext
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

from .sessions import ResolvedChatSession


def resolve_web_chat_scopes(*, principal: Principal, employee: EmployeeRow | None) -> frozenset[WebApiScope]:
    """Deny-by-default transport/application scopes for a chat stream.

    Only an authenticated, active employee gets the transport scope. This is the
    existing chat authorization level (project access is checked separately);
    an anonymous or disabled principal gets no scope at all.
    """
    if not principal.sub.strip():
        return frozenset()
    if employee is None:
        return frozenset()
    if employee.status != "active" or employee.deleted_at is not None:
        return frozenset()
    return frozenset({WebApiScope.CHAT_STREAM})


def _forbidden(code: str, detail: str) -> AppError:
    return AppError(code=code, title="Forbidden", status=403, detail=detail)


def build_stream_facade_context(
    *,
    auth: WebApiAuthContext,
    resolved: ResolvedChatSession,
    project_id: str,
    session_id: str,
) -> WebRequestContext:
    """Build a facade context with a proven route session, or refuse.

    Refusals happen before the SSE response is opened, so the browser receives
    a JSON problem instead of a half-open stream.
    """
    if not session_id.strip():
        raise AppError(
            code="SESSION_CONTEXT_MISSING",
            title="Session context required",
            status=400,
            detail="a route session is required for a chat stream",
        )
    if not resolved.session_id.strip() or resolved.project_id != project_id or resolved.session_id != session_id:
        raise _forbidden(
            "SESSION_CONTEXT_MISMATCH",
            "resolved chat session does not match the routed project/session",
        )

    work = auth.work_context
    if work.project_id is not None and work.project_id != project_id:
        raise _forbidden("PROJECT_SCOPE_MISMATCH", "work context project does not match the routed project")
    if work.cabinet_id is not None and work.cabinet_id != resolved.cabinet_id:
        raise _forbidden("CABINET_SCOPE_MISMATCH", "work context cabinet does not match the resolved project cabinet")

    try:
        facade_context = auth.to_facade_context()
    except (PermissionError, ScopeViolation) as exc:
        raise _forbidden("FORBIDDEN", "authenticated context cannot be mapped to a facade context") from exc

    if not facade_context.user.id.strip():
        raise AppError(
            code="UNAUTHENTICATED",
            title="Unauthorized",
            status=401,
            detail="authenticated subject is required",
        )
    if facade_context.session_id is not None and facade_context.session_id != session_id:
        # A facade context that already carries a different session is a
        # conflict, not something to overwrite silently.
        raise _forbidden("SESSION_CONTEXT_MISMATCH", "facade context already carries a different session")
    if facade_context.project_id is not None and facade_context.project_id != project_id:
        raise _forbidden("PROJECT_SCOPE_MISMATCH", "facade context already carries a different project")

    context = replace(
        facade_context,
        project_id=project_id,
        session_id=session_id,
        cabinet_id=resolved.cabinet_id,
    )
    try:
        ScopeBoundary(context).session(project_id, session_id)
    except ScopeViolation as exc:
        raise _forbidden("FORBIDDEN", "route project/session is outside the authenticated scope") from exc
    return context
