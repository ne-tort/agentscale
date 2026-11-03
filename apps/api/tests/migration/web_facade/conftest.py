"""Fixtures for the canonical web SSE transport acceptance suite.

These tests are authored under the migration verification freeze and are not
executed while the freeze is in force. They therefore build a *minimal* app: only
the transport router plus the real problem-details handlers, with auth and work
context provided by dependency overrides. No database, no lifespan, no
``async_client`` fixture (that one would drag in the shared Postgres wipe).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from prodavan.api.deps import get_current_employee, get_db_session, get_principal, get_work_context
from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import web_stream
from prodavan.application.web_api.contracts import uuid5_from_run
from prodavan.application.web_facade_adapter.sessions import ResolvedChatSession
from prodavan.application.web_facade_adapter.streams import WebStreamRuntime
from prodavan.domain.identity import Principal, WorkContext
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

PROJECT = "proj-1"
SESSION = "sess-1"
CABINET = "cab-1"
RUN = "run-1"
STREAM_URL = f"/api/v1/web/projects/{PROJECT}/chat/sessions/{SESSION}/stream"


@dataclass(slots=True)
class FakeRunIndex:
    """Trusted index double: membership is explicit, never derived from order."""

    project_id: str = PROJECT
    session_id: str = SESSION
    run_id: str | None = RUN
    sequences: frozenset[int] = field(default_factory=lambda: frozenset({1, 2, 3, 4, 5, 6, 7, 8}))
    extra_runs: dict[str, frozenset[int]] = field(default_factory=dict)

    def has(self, run_id: str, sequence: int) -> bool:
        if self.run_id == run_id and sequence in self.sequences:
            return True
        return sequence in self.extra_runs.get(run_id, frozenset())

    def find_by_event_id(self, event_id: UUID) -> tuple[str, int] | None:
        runs: list[tuple[str, Iterable[int]]] = []
        if self.run_id is not None:
            runs.append((self.run_id, self.sequences))
        runs.extend(self.extra_runs.items())
        for run_id, sequences in runs:
            for sequence in sequences:
                if uuid5_from_run(run_id, sequence) == event_id:
                    return run_id, sequence
        return None


class FakeChatStreamFacade:
    """Canonical stream port double: yields application envelopes."""

    def __init__(
        self,
        envelopes: list[Any] | None = None,
        *,
        failure: BaseException | None = None,
        fail_after: int | None = None,
    ) -> None:
        self.envelopes = envelopes or []
        self.failure = failure
        self.fail_after = fail_after
        self.calls: list[tuple[Any, Any]] = []

    async def stream(self, *, context: Any, command: Any) -> AsyncIterator[Any]:
        self.calls.append((context, command))
        for index, envelope in enumerate(self.envelopes):
            if self.fail_after is not None and index >= self.fail_after:
                assert self.failure is not None
                raise self.failure
            yield envelope
        if self.fail_after is not None and self.fail_after >= len(self.envelopes):
            assert self.failure is not None
            raise self.failure


class FakeSessionResolver:
    """Server-side resolver double; refusals stand in for tenant/project policy."""

    def __init__(
        self,
        *,
        project_id: str = PROJECT,
        session_id: str = SESSION,
        cabinet_id: str = CABINET,
        error: BaseException | None = None,
    ) -> None:
        self.project_id = project_id
        self.session_id = session_id
        self.cabinet_id = cabinet_id
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
        return ResolvedChatSession(
            project_id=self.project_id,
            session_id=self.session_id,
            cabinet_id=self.cabinet_id,
        )


class FakeIndexProvider:
    def __init__(self, index: Any | None = None, *, error: BaseException | None = None) -> None:
        self.index = index if index is not None else FakeRunIndex()
        self.error = error
        self.scopes: list[Any] = []

    async def __call__(self, scope: Any) -> Any:
        self.scopes.append(scope)
        if self.error is not None:
            raise self.error
        return self.index


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


def make_runtime(
    *,
    facade: FakeChatStreamFacade,
    index: Any | None = None,
    resolver: FakeSessionResolver | None = None,
    index_error: BaseException | None = None,
    scopes: Any | None = None,
) -> WebStreamRuntime:
    """Compose the transport runtime exactly as production wiring would."""
    return WebStreamRuntime(
        sessions=resolver or FakeSessionResolver(),
        index_provider=FakeIndexProvider(index, error=index_error),
        chat_stream=facade,
        scopes=scopes if scopes is not None else _default_scopes,
    )


def _default_scopes(pr: Principal, emp: EmployeeRow | None) -> Any:
    from prodavan.application.web_facade_adapter.context import resolve_web_chat_scopes

    return resolve_web_chat_scopes(principal=pr, employee=emp)


def no_stream_scope(pr: Principal, emp: EmployeeRow | None) -> Any:
    """Authenticated caller whose transport capability is withheld."""
    _ = pr, emp
    return frozenset()


@pytest.fixture
def app() -> FastAPI:
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


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def install(app: FastAPI, runtime: WebStreamRuntime) -> None:
    web_stream.install_web_stream_runtime(app, runtime)
