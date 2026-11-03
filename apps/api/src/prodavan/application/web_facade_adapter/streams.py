"""Transport-facing streaming ports for the canonical web facade.

``WebApiFacade.run_chat`` does not expose a separate replay cursor or the
``turn_id`` idempotency key (see ``web-cutover-implementation.md`` blocker 6).
The transport therefore talks to a narrow port that carries the resolved
cursor; a concrete implementation over a canonical/durable facade is a separate
task. Until it exists the default runtime refuses with an explicit blocker code
instead of degrading to the legacy session-local stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from prodavan.application.web_api.dependencies import WebApiAuthContext, WebApiScope
from prodavan.application.web_facade.auth import WebRequestContext
from prodavan.application.web_facade.contracts import ChatRunRequest as FacadeChatRunRequest
from prodavan.application.web_facade.contracts import SseEventEnvelope as ApplicationSseEventEnvelope
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

from .envelopes import RunSequenceIndex, StreamScope
from .service import WebFacadeAdapter
from .sessions import ChatSessionResolver


@dataclass(frozen=True, slots=True)
class ChatStreamCommand:
    """Authorized stream request; ``last_event_id`` is already application-form."""

    project_id: str
    session_id: str
    prompt: str
    model_id: str | None = None
    turn_id: str | None = None
    last_event_id: str | None = None


class WebChatStreamFacade(Protocol):
    def stream(
        self,
        *,
        context: WebRequestContext,
        command: ChatStreamCommand,
    ) -> AsyncIterator[ApplicationSseEventEnvelope]: ...


class AdapterChatStreamFacade:
    """Adapt :class:`WebFacadeAdapter` to the transport port.

    The adapter keeps its own ``AdapterScope.CHAT_RUN`` check, which is separate
    from the transport's ``WebApiScope.CHAT_STREAM`` check and runs on every
    connect, including replay.
    """

    def __init__(self, adapter: WebFacadeAdapter) -> None:
        self._adapter = adapter

    async def stream(
        self,
        *,
        context: WebRequestContext,
        command: ChatStreamCommand,
    ) -> AsyncIterator[ApplicationSseEventEnvelope]:
        request = FacadeChatRunRequest(
            project_id=command.project_id,
            session_id=command.session_id,
            prompt=command.prompt,
            model_id=command.model_id,
        )
        async for envelope in self._adapter.run_chat(
            context=context,
            request=request,
            last_event_id=command.last_event_id,
        ):
            yield envelope


class UnavailableChatStreamFacade:
    """Fail-closed default until a canonical stream facade is wired."""

    async def stream(
        self,
        *,
        context: WebRequestContext,
        command: ChatStreamCommand,
    ) -> AsyncIterator[ApplicationSseEventEnvelope]:
        _ = context, command
        raise AppError(
            code="WEB_CHAT_STREAM_FACADE_UNAVAILABLE",
            title="Chat stream unavailable",
            status=503,
            detail="canonical chat stream facade is not wired",
            extra={"blocker": "web_chat_stream_facade_unwired"},
        )
        yield  # pragma: no cover - makes this an async generator


class RunIndexProvider(Protocol):
    async def __call__(self, scope: StreamScope) -> RunSequenceIndex: ...


class UnavailableRunIndexProvider:
    """Fail-closed default: no durable run/sequence index exists yet."""

    async def __call__(self, scope: StreamScope) -> RunSequenceIndex:
        _ = scope
        raise AppError(
            code="STREAM_REPLAY_INDEX_UNAVAILABLE",
            title="Chat stream unavailable",
            status=503,
            detail="no trusted run/sequence index is available for web chat replay",
            extra={"blocker": "durable_run_sequence_index_missing"},
        )


def _default_stream_scopes(
    principal: Principal,
    employee: EmployeeRow | None,
) -> frozenset[WebApiScope]:
    """Deny-by-default transport scope for an authenticated active employee."""
    from .context import resolve_web_chat_scopes

    return resolve_web_chat_scopes(principal=principal, employee=employee)


@dataclass(frozen=True, slots=True)
class WebStreamRuntime:
    """Composition seam for the SSE transport.

    Production wiring supplies a SQL session resolver plus a durable run index
    and a canonical facade. Tests supply the same contract with in-memory
    doubles; the route itself never constructs transport state.
    """

    sessions: ChatSessionResolver
    index_provider: RunIndexProvider
    chat_stream: WebChatStreamFacade
    # Entitlement-derived transport scopes. The default fails closed for
    # anonymous, disabled or soft-deleted employees.
    scopes: Callable[[Principal, EmployeeRow | None], frozenset[WebApiScope]] = field(
        default=_default_stream_scopes
    )


def require_stream_scope(auth: WebApiAuthContext, scope: WebApiScope) -> None:
    """Map the auth contract's ``PermissionError`` to a stable 403 problem."""
    try:
        auth.require(scope)
    except PermissionError as exc:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail=f"missing web stream scope: {scope.value}",
        ) from exc


__all__ = [
    "AdapterChatStreamFacade",
    "ChatStreamCommand",
    "RunIndexProvider",
    "UnavailableChatStreamFacade",
    "UnavailableRunIndexProvider",
    "WebChatStreamFacade",
    "WebStreamRuntime",
    "require_stream_scope",
]
