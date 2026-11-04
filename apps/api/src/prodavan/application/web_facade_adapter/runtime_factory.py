"""Composition root for the canonical web chat SSE runtime.

``api/v1/web_stream.py`` owns the route and reads its composition from
``app.state``; the route never builds transport state itself. Without an
explicit install it refuses with ``503 web_chat_stream_facade_unwired`` — which
is exactly what the real process did before this factory existed.

The runtime has three moving parts, and each one is assembled from real
infrastructure independently:

* ``sessions`` — :class:`SqlChatSessionResolver` on a session opened per
  ``resolve`` call, so project access and session ownership are proven
  server-side without borrowing the request's session;
* ``index_provider`` — durable ``(run, sequence)`` evidence read from
  ``agent_runs`` plus ``agent_control_plane_events``;
* ``chat_stream`` — the canonical facade adapter
  (:class:`AdapterChatStreamFacade` over :class:`WebFacadeAdapter`) talking to
  the existing :class:`AgentSessionService` turn stream.

A part whose dependency is genuinely unavailable is replaced by **its own**
fail-closed double and reported as a *named* blocker, while the remaining parts
stay real. A refusal therefore names the missing part (``blocker`` field) and
uses a part-specific code instead of a generic "unavailable". Nothing here
fabricates replay identity: a pair that cannot be proven refuses.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.web_api.contracts import uuid5_from_run
from prodavan.application.web_facade.auth import WebRequestContext
from prodavan.application.web_facade.contracts import ChatRunRequest
from prodavan.domain.agent.control_plane import AgentRunStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

from .contracts import AdapterScope
from .envelopes import RunSequenceIndex, StreamScope
from .service import WebFacadeAdapter
from .sessions import ChatSessionResolver, ResolvedChatSession, SqlChatSessionResolver
from .streams import (
    AdapterChatStreamFacade,
    RunIndexProvider,
    UnavailableChatStreamFacade,
    UnavailableRunIndexProvider,
    WebChatStreamFacade,
    WebStreamRuntime,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]

#: Aggregate type that carries the run-local sequence of a run. Other
#: aggregates (approvals) keep their own sequence, so mixing them would make
#: ``(run_id, sequence)`` ambiguous.
RUN_AGGREGATE_TYPE = "agent_run"

#: Run statuses that may still append events after the index snapshot is taken.
LIVE_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_APPROVAL,
    }
)

#: Default bounds for one durable index snapshot. Truncation is always
#: fail-closed: an event outside the loaded window simply is not a member.
DEFAULT_MAX_RUNS = 50
DEFAULT_MAX_EVENTS = 20_000

# Named blockers. Each one identifies a single missing part or a single
# unproven fact, never a blanket "stream unavailable".
BLOCKER_SESSIONS = "web_stream_session_resolver_unwired"
BLOCKER_RUN_INDEX = "durable_run_sequence_index_missing"
BLOCKER_CHAT_STREAM = "web_chat_stream_facade_unwired"
BLOCKER_RUN_INDEX_SCHEMA = "web_stream_run_index_schema_unavailable"
BLOCKER_RUN_INDEX_EVIDENCE = "web_stream_run_index_evidence_missing"
BLOCKER_LEGACY_REPLAY = "web_stream_legacy_chat_replay_unbound"

CODE_SESSION_RESOLVER = "WEB_CHAT_STREAM_SESSION_RESOLVER_UNAVAILABLE"
CODE_RUN_INDEX = "WEB_CHAT_STREAM_RUN_INDEX_UNAVAILABLE"
CODE_RUN_INDEX_EVIDENCE = "WEB_CHAT_STREAM_RUN_INDEX_EVIDENCE_MISSING"
CODE_LEGACY_REPLAY = "WEB_CHAT_STREAM_REPLAY_UNAVAILABLE"

_UNAVAILABLE_TITLE = "Chat stream unavailable"


def _blocked(*, code: str, blocker: str, detail: str) -> AppError:
    """Stable 503 that names the missing part in ``blocker``."""
    return AppError(
        code=code,
        title=_UNAVAILABLE_TITLE,
        status=503,
        detail=detail,
        extra={"blocker": blocker},
    )


class UnavailableChatSessionResolver:
    """Fail-closed resolver for a deployment without a SQL session factory.

    Substituted **only** for the resolver part; the run index and the chat
    stream facade stay whatever they really are.
    """

    async def resolve(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str,
        session_id: str,
    ) -> ResolvedChatSession:
        _ = principal, employee, project_id, session_id
        raise _blocked(
            code=CODE_SESSION_RESOLVER,
            blocker=BLOCKER_SESSIONS,
            detail="no SQL session factory is available to resolve a chat session",
        )


class SessionFactoryChatSessionResolver:
    """Resolve a chat session on a session opened from the process factory.

    The transport calls the resolver with an authorization contract only — no
    ``AsyncSession`` — so the resolver owns its short-lived unit of work. The
    resolver never mutates: it proves access and reads one row.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def resolve(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str,
        session_id: str,
    ) -> ResolvedChatSession:
        async with self._session_factory() as session:
            return await SqlChatSessionResolver(session).resolve(
                principal=principal,
                employee=employee,
                project_id=project_id,
                session_id=session_id,
            )


class DurableRunSequenceIndex:
    """Trusted, request-scoped index built from durable run evidence.

    Two sources of membership, both explicit:

    * the durable snapshot of ``(run_id, sequence)`` pairs taken when the
      stream was authorized;
    * the live tail of the single run that the snapshot proved is bound to this
      project/session **and** is still producing events. Such a run
      legitimately advances past the snapshot between connect and delivery, so
      a pair of that run is a member — but only that run, never another one.

    Reverse lookup stays deterministic: a UUID is answered only for a pair that
    was either loaded durably or already observed on this authorized stream. A
    client-supplied cursor is resolved before any event is observed, so it can
    only ever be satisfied by durable evidence.
    """

    __slots__ = (
        "_live_tail",
        "_observed",
        "_pairs",
        "_project_id",
        "_reverse",
        "_run_id",
        "_session_id",
    )

    def __init__(
        self,
        *,
        project_id: str,
        session_id: str,
        run_id: str | None,
        pairs: Iterable[tuple[str, int]],
        live_tail: bool = False,
    ) -> None:
        self._project_id = project_id
        self._session_id = session_id
        self._run_id = run_id
        self._pairs = frozenset(pairs)
        self._live_tail = bool(live_tail) and run_id is not None
        self._reverse: dict[UUID, tuple[str, int]] = {
            uuid5_from_run(sorted_run, sequence): (sorted_run, sequence)
            for sorted_run, sequence in self._pairs
        }
        self._observed: set[tuple[str, int]] = set()

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def live_tail(self) -> bool:
        return self._live_tail

    def has(self, run_id: str, sequence: int) -> bool:
        """True only for a durably indexed pair or the bound run's live tail."""
        if sequence < 0:
            return False
        pair = (run_id, int(sequence))
        if pair in self._pairs:
            return True
        if not self._live_tail or run_id != self._run_id:
            return False
        # Registering the pair is what lets the deterministic reverse lookup
        # answer for an event that has not been persisted yet. It is scoped to
        # this authorized stream and this single bound run.
        self._observed.add(pair)
        return True

    def find_by_event_id(self, event_id: UUID) -> tuple[str, int] | None:
        resolved = self._reverse.get(event_id)
        if resolved is not None:
            return resolved
        for run_id, sequence in tuple(self._observed):
            if uuid5_from_run(run_id, sequence) == event_id:
                return run_id, sequence
        return None


class DurableRunIndexProvider:
    """Build the run/sequence index from ``agent_runs`` + control-plane events.

    A session with no durable run yet, or a database that cannot be read, is a
    named refusal — never an empty index. An empty index would silently turn
    every live event into ``STREAM_EVENT_ID_UNRESOLVED`` and hide the real
    cause.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        max_runs: int = DEFAULT_MAX_RUNS,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        self._session_factory = session_factory
        self._max_runs = max(1, int(max_runs))
        self._max_events = max(1, int(max_events))

    async def __call__(self, scope: StreamScope) -> RunSequenceIndex:
        try:
            async with self._session_factory() as session:
                runs = await self._load_runs(session, scope)
                if not runs:
                    raise _blocked(
                        code=CODE_RUN_INDEX_EVIDENCE,
                        blocker=BLOCKER_RUN_INDEX_EVIDENCE,
                        detail="no durable agent run is bound to this project/session yet",
                    )
                pairs = await self._load_pairs(session, scope, tuple(runs))
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            # Never surface SQL/DSN/exception text to the browser.
            logger.warning(
                "web stream run index read failed scope=%s/%s error=%s",
                scope.project_id,
                scope.session_id,
                type(exc).__name__,
            )
            raise _blocked(
                code=CODE_RUN_INDEX,
                blocker=BLOCKER_RUN_INDEX_SCHEMA,
                detail="the durable run/sequence index could not be read",
            ) from exc

        bound_run, live_tail = self._bound_run(runs)
        return DurableRunSequenceIndex(
            project_id=scope.project_id,
            session_id=scope.session_id,
            run_id=bound_run,
            pairs=pairs,
            live_tail=live_tail,
        )

    async def _load_runs(self, session: AsyncSession, scope: StreamScope) -> dict[str, str]:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow

        rows = (
            await session.execute(
                select(AgentRunRow.id, AgentRunRow.status)
                .where(
                    AgentRunRow.project_id == scope.project_id,
                    AgentRunRow.session_id == scope.session_id,
                )
                .order_by(AgentRunRow.created_at.desc(), AgentRunRow.id.desc())
                .limit(self._max_runs + 1)
            )
        ).all()
        if len(rows) > self._max_runs:
            # Truncation is fail-closed: an older run that is not loaded is not
            # a member, so a cursor pointing into it is rejected, not guessed.
            logger.warning(
                "web stream run index truncated scope=%s/%s runs>%s",
                scope.project_id,
                scope.session_id,
                self._max_runs,
            )
            rows = rows[: self._max_runs]
        return {str(run_id): str(status) for run_id, status in rows}

    async def _load_pairs(
        self,
        session: AsyncSession,
        scope: StreamScope,
        run_ids: tuple[str, ...],
    ) -> frozenset[tuple[str, int]]:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneEventRow,
        )

        rows = (
            await session.execute(
                select(AgentControlPlaneEventRow.run_id, AgentControlPlaneEventRow.sequence)
                .where(
                    AgentControlPlaneEventRow.project_id == scope.project_id,
                    AgentControlPlaneEventRow.run_id.in_(run_ids),
                    AgentControlPlaneEventRow.run_id.is_not(None),
                    AgentControlPlaneEventRow.aggregate_type == RUN_AGGREGATE_TYPE,
                )
                .limit(self._max_events)
            )
        ).all()
        if len(rows) >= self._max_events:
            logger.warning(
                "web stream run index truncated scope=%s/%s events>=%s",
                scope.project_id,
                scope.session_id,
                self._max_events,
            )
        return frozenset(
            (str(run_id), int(sequence))
            for run_id, sequence in rows
            if run_id is not None and sequence is not None
        )

    @staticmethod
    def _bound_run(runs: dict[str, str]) -> tuple[str | None, bool]:
        """The single run bound to the stream, and whether it is still live.

        A session with several runs has no unambiguous run, so nothing is
        claimed about a live tail; only durable pairs remain valid.
        """
        if len(runs) != 1:
            return None, False
        ((run_id, status),) = runs.items()
        return run_id, status in LIVE_RUN_STATUSES


def principal_from_facade_context(context: WebRequestContext) -> Principal:
    """Rebuild the domain principal from the proven facade context.

    ``WebRequestContext`` is built by ``build_stream_facade_context`` from an
    already validated token and a server-resolved route session, so this is a
    projection of proven identity — not a new authentication step.
    """
    user = context.user
    if not user.id.strip():
        raise AppError(
            code="UNAUTHENTICATED",
            title="Unauthorized",
            status=401,
            detail="authenticated subject is required",
        )
    return Principal(
        sub=user.id,
        roles=frozenset(user.roles),
        email=user.email,
        username=user.display_name,
    )


class LegacyChatStreamServices:
    """``LegacyServices`` over the existing ``AgentSessionService`` turn stream.

    Only ``chat_run`` is implemented: that is the single port the transport
    runtime consumes, and every other one would be dead code.

    Two deliberate refusals keep the migration's fail-closed rules:

    * a ``after_seq`` cursor is refused. The legacy stream is session-local and
      carries no per-row run identity, so re-attaching with a cursor cannot be
      proven and would otherwise start a *second* detached turn on reconnect;
    * a missing active employee is refused before the turn is started.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def chat_run(
        self,
        *,
        context: WebRequestContext,
        request: ChatRunRequest,
        after_seq: int | None = None,
    ) -> AsyncIterator[object]:
        if after_seq is not None:
            raise _blocked(
                code=CODE_LEGACY_REPLAY,
                blocker=BLOCKER_LEGACY_REPLAY,
                detail=(
                    "the legacy chat stream has no durable run-indexed replay; "
                    "a reconnect cursor cannot be honoured without starting a second turn"
                ),
            )
        principal = principal_from_facade_context(context)
        from prodavan.application.agent.session_service import AgentSessionService
        from prodavan.application.identity.service import EntitlementService

        # The stream owns its unit of work for the whole turn: the tail keeps
        # polling through the same session while the detached run writes on its
        # own connection.
        async with self._session_factory() as session:
            employee = await EntitlementService(session).ensure_active_employee(principal)
            if employee is None:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="an active employee is required for a chat stream",
                )
            service = AgentSessionService(session)
            async for event in service.iter_chat_turn_sse(
                project_id=request.project_id,
                text=request.prompt,
                session_id=request.session_id,
                attachment_refs=None,
                principal=principal,
                employee=employee,
                model=request.model_id,
                turn_id=request.turn_id,
            ):
                yield event
            # The session is closed on every exit path (completion, upstream
            # failure, client disconnect) by the context manager above.


class _Unset:
    """Sentinel distinguishing "not supplied" from an explicit ``None``."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


UNSET = _Unset()


def _resolve_optional_session_factory() -> SessionFactory | None:
    """Process session factory, or ``None`` when the database layer is absent."""
    try:
        from prodavan.infrastructure.persistence.database import get_session_factory

        return get_session_factory()
    except Exception as exc:  # noqa: BLE001 - absence is a named blocker, not a crash
        logger.warning("web stream runtime: session factory unavailable error=%s", type(exc).__name__)
        return None


def build_web_stream_runtime(
    *,
    session_factory: Any = UNSET,
    index_provider: Any = UNSET,
    chat_stream: Any = UNSET,
    sessions: Any = UNSET,
    scopes: Callable[[Principal, EmployeeRow | None], Any] = UNSET,
    max_runs: int = DEFAULT_MAX_RUNS,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> WebStreamRuntime:
    """Assemble :class:`WebStreamRuntime` part by part.

    Every argument is optional: the default builds the real runtime from the
    process session factory, while a test (or a deployment missing one part)
    replaces exactly that part. A part without its dependency becomes that
    part's fail-closed double, never a stubbed runtime.
    """
    factory: SessionFactory | None
    if session_factory is UNSET:
        factory = _resolve_optional_session_factory()
    else:
        factory = session_factory

    resolved_sessions: ChatSessionResolver
    if sessions is not UNSET:
        resolved_sessions = sessions
    elif factory is not None:
        resolved_sessions = SessionFactoryChatSessionResolver(factory)
    else:
        resolved_sessions = UnavailableChatSessionResolver()

    resolved_index: RunIndexProvider
    if index_provider is not UNSET:
        resolved_index = index_provider
    elif factory is not None:
        resolved_index = DurableRunIndexProvider(
            factory,
            max_runs=max_runs,
            max_events=max_events,
        )
    else:
        resolved_index = UnavailableRunIndexProvider()

    resolved_chat_stream: WebChatStreamFacade
    if chat_stream is not UNSET:
        resolved_chat_stream = chat_stream
    elif factory is not None:
        resolved_chat_stream = AdapterChatStreamFacade(
            WebFacadeAdapter(
                LegacyChatStreamServices(factory),
                # The transport requires both scopes: WebApiScope.CHAT_STREAM
                # at the route and AdapterScope.CHAT_RUN here, on every connect.
                scopes=(AdapterScope.CHAT_RUN,),
            )
        )
    else:
        resolved_chat_stream = UnavailableChatStreamFacade()

    if scopes is UNSET:
        return WebStreamRuntime(
            sessions=resolved_sessions,
            index_provider=resolved_index,
            chat_stream=resolved_chat_stream,
        )
    return WebStreamRuntime(
        sessions=resolved_sessions,
        index_provider=resolved_index,
        chat_stream=resolved_chat_stream,
        scopes=scopes,
    )


def runtime_blockers(runtime: WebStreamRuntime) -> tuple[str, ...]:
    """Named blockers for the parts that are fail-closed doubles.

    Used for startup logging/health only: the route keeps refusing per part at
    call time, so a runtime with one stub still serves the other parts' real
    refusals rather than a blanket error.
    """
    blockers: list[str] = []
    if isinstance(runtime.sessions, UnavailableChatSessionResolver):
        blockers.append(BLOCKER_SESSIONS)
    if isinstance(runtime.index_provider, UnavailableRunIndexProvider):
        blockers.append(BLOCKER_RUN_INDEX)
    if isinstance(runtime.chat_stream, UnavailableChatStreamFacade):
        blockers.append(BLOCKER_CHAT_STREAM)
    return tuple(blockers)


__all__ = [
    "BLOCKER_CHAT_STREAM",
    "BLOCKER_LEGACY_REPLAY",
    "BLOCKER_RUN_INDEX",
    "BLOCKER_RUN_INDEX_EVIDENCE",
    "BLOCKER_RUN_INDEX_SCHEMA",
    "BLOCKER_SESSIONS",
    "CODE_LEGACY_REPLAY",
    "CODE_RUN_INDEX",
    "CODE_RUN_INDEX_EVIDENCE",
    "CODE_SESSION_RESOLVER",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_RUNS",
    "DurableRunIndexProvider",
    "DurableRunSequenceIndex",
    "LegacyChatStreamServices",
    "RUN_AGGREGATE_TYPE",
    "SessionFactory",
    "SessionFactoryChatSessionResolver",
    "UNSET",
    "UnavailableChatSessionResolver",
    "build_web_stream_runtime",
    "principal_from_facade_context",
    "runtime_blockers",
]
