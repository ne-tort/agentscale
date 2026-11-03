"""Canonical browser SSE transport for the web chat facade.

This route is the first place where the canonical ``web_facade`` application
contract is put on the wire. It deliberately does **not** reuse the legacy
``/projects/{project_id}/chat/stream`` handler:

* the request scope is the route (``project_id`` + ``session_id``), resolved on
  the server before any stream is opened — never a JWT claim, never an event
  payload;
* two independent scopes are required: transport ``WebApiScope.CHAT_STREAM``
  here and application ``AdapterScope.CHAT_RUN`` inside the facade adapter, on
  every connect including replay;
* the application envelope (``run_id:sequence``) and the wire envelope (UUIDv5)
  stay separate; a UUID is produced and accepted only through a trusted
  ``RunSequenceIndex`` that binds ``(project, session, run, sequence)``;
* the replay cursor advances only after a frame was actually delivered;
* a malformed upstream is a JSON ``502 UPSTREAM_STREAM_MALFORMED`` before
  headers, and a redacted terminal ``error`` frame after them.

Composition is external: ``install_web_stream_runtime`` supplies the resolver,
run index and facade. Without it the route refuses with an explicit blocker code
rather than degrading to the legacy session-local stream.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from prodavan.api.deps import (
    PrincipalDep,
    WorkContextDep,
    get_current_employee,
)
from prodavan.application.web_api.dependencies import WebApiAuthContext, WebApiScope
from prodavan.application.web_facade.auth import ScopeViolation, WebRequestContext
from prodavan.application.web_facade_adapter.context import build_stream_facade_context
from prodavan.application.web_facade_adapter.envelopes import (
    EncodedWireEvent,
    StreamCursorError,
    StreamIdentityError,
    StreamScope,
    WireEventEncoder,
    parse_transport_cursor,
    resolve_transport_cursor,
)
from prodavan.application.web_facade_adapter.framing import (
    CursorLedger,
    StreamStats,
    stream_frames,
    terminal_error_frame,
)
from prodavan.application.web_facade_adapter.streams import (
    ChatStreamCommand,
    WebStreamRuntime,
    require_stream_scope,
)
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# Keepalive cadence: L4 relays silently cut idle streams, and without any frame
# the browser cannot tell "thinking" from "wedged". An SSE comment is not a
# replayable event and never advances the cursor.
KEEPALIVE_SECONDS = 15.0

STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # A buffering reverse proxy in front of the API must not hold an event stream.
    "X-Accel-Buffering": "no",
}

RUNTIME_STATE_ATTR = "web_stream_runtime"

EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


class WebChatStreamBody(BaseModel):
    """Stream start body; the authoritative scope ids are the route."""

    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=500_000)
    turn_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    # Accepted for compatibility with clients that repeat the route ids, but
    # only to prove agreement: a mismatch is refused, never widened.
    project_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_non_blank_prompt(self) -> WebChatStreamBody:
        if not self.text.strip():
            raise ValueError("text must not be blank")
        return self


def install_web_stream_runtime(app: Any, runtime: WebStreamRuntime) -> None:
    """Composition hook: wire the resolver, run index and chat facade."""
    setattr(app.state, RUNTIME_STATE_ATTR, runtime)


def get_web_stream_runtime(request: Request) -> WebStreamRuntime | None:
    return getattr(request.app.state, RUNTIME_STATE_ATTR, None)


def _require_runtime(request: Request) -> WebStreamRuntime:
    runtime = get_web_stream_runtime(request)
    if runtime is None:
        raise AppError(
            code="WEB_CHAT_STREAM_FACADE_UNAVAILABLE",
            title="Chat stream unavailable",
            status=503,
            detail="canonical web chat stream runtime is not wired",
            extra={"blocker": "web_chat_stream_facade_unwired"},
        )
    return runtime


def _auth_context(
    *,
    principal: Any,
    employee: EmployeeRow | None,
    work_context: Any,
    scopes: frozenset[WebApiScope],
) -> WebApiAuthContext:
    return WebApiAuthContext(
        principal=principal,
        employee_id=employee.id if employee is not None else "",
        scopes=scopes,
        work_context=work_context,
    )


async def _encode_application_stream(
    source: AsyncIterator[Any],
    *,
    encoder: WireEventEncoder,
    scope: StreamScope,
) -> AsyncIterator[EncodedWireEvent]:
    """Application envelopes -> wire frames, refusing any unproven identity."""
    async for envelope in source:
        yield encoder.encode(envelope, scope=scope)


async def _prepend(first: EncodedWireEvent, rest: AsyncIterator[EncodedWireEvent]) -> AsyncIterator[EncodedWireEvent]:
    yield first
    async for frame in rest:
        yield frame


def _as_problem(exc: BaseException) -> AppError:
    """Map a pre-header failure to a stable JSON problem; never leak text."""
    if isinstance(exc, StreamCursorError):
        return AppError(
            code=exc.code,
            title="Invalid stream cursor",
            status=exc.status,
            detail=exc.detail,
        )
    if isinstance(exc, StreamIdentityError):
        return AppError(
            code=exc.code,
            title="Chat stream unavailable" if exc.status >= 500 else "Chat stream rejected",
            status=exc.status,
            detail=exc.detail,
        )
    if isinstance(exc, ScopeViolation):
        return AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="project/session is outside the authenticated scope",
        )
    if isinstance(exc, ValueError):
        # The adapter's legacy cursor parser raises before the first frame.
        return AppError(
            code="INVALID_LAST_EVENT_ID",
            title="Invalid stream cursor",
            status=400,
            detail="Last-Event-ID is not a valid stream cursor",
        )
    raise exc


def _assert_body_scope(body: WebChatStreamBody, *, project_id: str, session_id: str) -> None:
    if body.project_id is not None and body.project_id != project_id:
        raise AppError(
            code="SCOPE_MISMATCH",
            title="Scope mismatch",
            status=400,
            detail="body project_id does not match the routed project",
        )
    if body.session_id is not None and body.session_id != session_id:
        raise AppError(
            code="SCOPE_MISMATCH",
            title="Scope mismatch",
            status=400,
            detail="body session_id does not match the routed session",
        )


@router.post(
    "/web/projects/{project_id}/chat/sessions/{session_id}/stream",
    response_class=StreamingResponse,
    responses={
        400: {"description": "Invalid or ambiguous Last-Event-ID"},
        403: {"description": "Missing stream scope or foreign project/session"},
        404: {"description": "Session not found in the routed project"},
        409: {"description": "Cursor run cannot be reattached"},
        502: {"description": "Upstream stream is malformed before the first frame"},
        503: {"description": "Canonical stream facade or run index is not wired"},
    },
)
async def web_chat_stream(
    project_id: str,
    session_id: str,
    body: WebChatStreamBody,
    request: Request,
    principal: PrincipalDep,
    work_context: WorkContextDep,
    employee: EmployeeDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Response:
    """Start or resume one chat turn and stream canonical wire envelopes."""
    runtime = _require_runtime(request)

    auth = _auth_context(
        principal=principal,
        employee=employee,
        work_context=work_context,
        scopes=runtime.scopes(principal, employee),
    )
    # Check 1/2: transport scope, deny-by-default and before any resolution.
    require_stream_scope(auth, WebApiScope.CHAT_STREAM)
    _assert_body_scope(body, project_id=project_id, session_id=session_id)

    # Server-side session resolution: the routed session must belong to the
    # routed project inside the authenticated tenant.
    resolved = await runtime.sessions.resolve(
        principal=principal,
        employee=employee,
        project_id=project_id,
        session_id=session_id,
    )
    context: WebRequestContext = build_stream_facade_context(
        auth=auth,
        resolved=resolved,
        project_id=project_id,
        session_id=session_id,
    )

    scope = StreamScope(project_id=project_id, session_id=session_id)
    index = await runtime.index_provider(scope)

    # Cursor syntax is validated first (400); a UUID resolves only through the
    # trusted index; a bare sequence is refused when the index is multi-run.
    try:
        parsed_cursor = parse_transport_cursor(last_event_id)
        cursor = resolve_transport_cursor(parsed_cursor, index=index, scope=scope)
    except (StreamCursorError, StreamIdentityError) as exc:
        raise _as_problem(exc) from exc

    command = ChatStreamCommand(
        project_id=project_id,
        session_id=session_id,
        prompt=body.text,
        model_id=body.model,
        turn_id=body.turn_id,
        # Downstream (application) cursor form. A resolved UUID is converted
        # here, once, through the index — never parsed as a sequence.
        last_event_id=cursor.application_event_id if cursor is not None else None,
    )

    ledger = CursorLedger(position=cursor)
    stats = StreamStats()
    source = _encode_application_stream(
        runtime.chat_stream.stream(context=context, command=command),
        encoder=WireEventEncoder(index),
        scope=scope,
    )

    # Pull the first frame before opening the response: a malformed upstream
    # must surface as a JSON 502, not as a half-open event stream. The adapter's
    # AdapterScope.CHAT_RUN check also runs here, before any header.
    try:
        first = await source.__anext__()
    except StopAsyncIteration as exc:
        raise _as_problem(
            StreamIdentityError(
                "UPSTREAM_STREAM_MALFORMED",
                detail="the upstream chat stream produced no replayable event",
            )
        ) from exc
    except (StreamCursorError, StreamIdentityError, ScopeViolation, ValueError) as exc:
        raise _as_problem(exc) from exc

    async def generate() -> AsyncIterator[str]:
        async for chunk in stream_frames(
            scope=scope,
            source=_prepend(first, source),
            ledger=ledger,
            keepalive_seconds=KEEPALIVE_SECONDS,
            failure_frame=lambda code, status: terminal_error_frame(
                code=code, status=status, project_id=project_id, session_id=session_id
            ),
            stats=stats,
        ):
            yield chunk
        if stats.failure_code:
            logger.warning(
                "web stream terminated scope=%s/%s code=%s delivered=%s",
                project_id,
                session_id,
                stats.failure_code,
                stats.delivered,
            )
        else:
            logger.info(
                "web stream finished scope=%s/%s delivered=%s terminated=%s cursor=%s",
                project_id,
                session_id,
                stats.delivered,
                stats.terminated,
                ledger.last_event_id,
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers=dict(STREAM_HEADERS),
    )
