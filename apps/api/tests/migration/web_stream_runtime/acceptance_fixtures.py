"""Fixtures for the MIG-WEB-RUNTIME-01 acceptance suite.

Written under the migration verification freeze: the suite is authored, not
executed. Nothing here touches PostgreSQL — the transport runtime is composed
either from in-process doubles (route behaviour) or from a fake session factory
(durable run-index reads), so the fixtures stay runnable on a bare checkout.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from fastapi import FastAPI

from prodavan.api.deps import get_current_employee, get_db_session, get_principal, get_work_context
from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import web_stream
from prodavan.application.web_api.contracts import uuid5_from_run
from prodavan.application.web_facade.contracts import RunEventType, RunStreamEvent, SseEventEnvelope
from prodavan.application.web_facade_adapter.context import resolve_web_chat_scopes
from prodavan.application.web_facade_adapter.sessions import ResolvedChatSession
from prodavan.application.web_facade_adapter.streams import WebStreamRuntime
from prodavan.core.infra.web_stream_resource import WebStreamLifespanManager, WebStreamResource
from prodavan.domain.identity import Principal, WorkContext
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

PROJECT = "proj-1"
SESSION = "sess-1"
CABINET = "cab-1"
RUN = "run-1"
STREAM_URL = f"/api/v1/web/projects/{PROJECT}/chat/sessions/{SESSION}/stream"
STREAM_BODY = {"text": "hello"}


def employee(*, status: str = "active", employee_id: str = "emp-1") -> EmployeeRow:
    return EmployeeRow(
        id=employee_id,
        login="user@company.local",
        email="user@company.local",
        status=status,
        keycloak_sub="kc-1",
    )


def principal() -> Principal:
    return Principal(sub="kc-1", roles=frozenset(), email="user@company.local", username="user")


def default_scopes(pr: Principal, emp: EmployeeRow | None) -> Any:
    return resolve_web_chat_scopes(principal=pr, employee=emp)


# ---------------------------------------------------------------------------
# Transport doubles
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FakeRunIndex:
    """Trusted index double; membership is explicit, never positional."""

    project_id: str = PROJECT
    session_id: str = SESSION
    run_id: str | None = RUN
    sequences: frozenset[int] = field(default_factory=lambda: frozenset({1, 2, 3, 4, 5, 6, 7, 8}))

    def has(self, run_id: str, sequence: int) -> bool:
        return run_id == self.run_id and sequence in self.sequences

    def find_by_event_id(self, event_id: UUID) -> tuple[str, int] | None:
        if self.run_id is None:
            return None
        for sequence in self.sequences:
            if uuid5_from_run(self.run_id, sequence) == event_id:
                return self.run_id, sequence
        return None


class FakeIndexProvider:
    def __init__(self, index: FakeRunIndex | None = None, *, error: BaseException | None = None) -> None:
        self.index = index if index is not None else FakeRunIndex()
        self.error = error
        self.scopes: list[Any] = []

    async def __call__(self, scope: Any) -> Any:
        self.scopes.append(scope)
        if self.error is not None:
            raise self.error
        return self.index


class FakeSessionResolver:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def resolve(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str,
        session_id: str,
    ) -> ResolvedChatSession:
        self.calls.append((project_id, session_id))
        if self.error is not None:
            raise self.error
        return ResolvedChatSession(project_id=PROJECT, session_id=SESSION, cabinet_id=CABINET)


class FakeChatStreamFacade:
    """Canonical stream port double: yields prepared application envelopes."""

    def __init__(self, envelopes: list[SseEventEnvelope] | None = None) -> None:
        self.envelopes = list(envelopes or [started_event()])
        self.calls: list[tuple[Any, Any]] = []

    async def stream(self, *, context: Any, command: Any) -> AsyncIterator[Any]:
        self.calls.append((context, command))
        for envelope in self.envelopes:
            yield envelope


def started_event(*, run_id: str = RUN, seq: int = 1) -> SseEventEnvelope:
    """One canonical application envelope (id is ``run_id:sequence``)."""
    return SseEventEnvelope.from_run_event(
        RunStreamEvent(type=RunEventType.STARTED, run_id=run_id, seq=seq)
    )


def composed_runtime(
    *,
    sessions: Any = None,
    index_provider: Any = None,
    chat_stream: Any = None,
) -> WebStreamRuntime:
    """Fully composed runtime over doubles — the 'all dependencies present' case."""
    return WebStreamRuntime(
        sessions=sessions if sessions is not None else FakeSessionResolver(),
        index_provider=index_provider if index_provider is not None else FakeIndexProvider(),
        chat_stream=chat_stream if chat_stream is not None else FakeChatStreamFacade(),
        scopes=default_scopes,
    )


# ---------------------------------------------------------------------------
# Fake SQL layer (durable run-index reads only; no PostgreSQL)
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows: Iterable[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeAsyncSession:
    """Minimal ``AsyncSession`` stand-in: answers the two provider reads.

    ``DurableRunIndexProvider`` reads runs first and event pairs second; the
    fixture mirrors that order explicitly instead of parsing SQL, so an
    accidental extra read surfaces as a failed assertion rather than silently
    returning the wrong rows.
    """

    def __init__(self, *, runs: list[tuple[Any, ...]], pairs: list[tuple[Any, ...]]) -> None:
        self._pages = [_Rows(runs), _Rows(pairs)]
        self.queries: list[Any] = []

    async def execute(self, statement: Any) -> _Rows:
        self.queries.append(statement)
        if not self._pages:
            raise AssertionError("unexpected extra durable run-index read")
        return self._pages.pop(0)


class FakeSessionFactory:
    """``async_sessionmaker`` stand-in yielding one prepared session."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session
        self.opened = 0

    def __call__(self) -> FakeSessionFactory:
        self.opened += 1
        return self

    async def __aenter__(self) -> FakeAsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# App + lifespan harness
# ---------------------------------------------------------------------------


def build_app() -> FastAPI:
    """Transport router only — no shared DB fixture, no real lifespan."""
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(web_stream.router, prefix="/api/v1")
    application.dependency_overrides[get_principal] = principal
    application.dependency_overrides[get_current_employee] = lambda: employee()
    application.dependency_overrides[get_work_context] = lambda: WorkContext(
        cabinet_id=CABINET, project_id=PROJECT
    )
    application.dependency_overrides[get_db_session] = lambda: None
    return application


def resource(
    *,
    enabled: bool,
    runtime_factory: Any = None,
) -> WebStreamResource:
    """Resource under test; ``runtime_factory`` replaces the real composition."""
    return WebStreamResource(enabled=enabled, runtime_factory=runtime_factory)


def manager_with(res: WebStreamResource) -> WebStreamLifespanManager:
    return WebStreamLifespanManager().register(res)  # type: ignore[return-value]


def request_for(app: FastAPI) -> Any:
    """``Request`` stand-in: ``get_web_stream_runtime`` only needs ``.app.state``."""
    return SimpleNamespace(app=app)


def runtime_state(app: FastAPI) -> Any:
    return getattr(app.state, web_stream.RUNTIME_STATE_ATTR, None)
