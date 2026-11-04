"""MIG-WEB-RUNTIME-01 acceptance: runtime factory composition.

Authored under the migration verification freeze; deliberately not executed.
"""

from __future__ import annotations

import pytest

from prodavan.application.web_facade_adapter.runtime_factory import (
    BLOCKER_CHAT_STREAM,
    BLOCKER_LEGACY_REPLAY,
    BLOCKER_RUN_INDEX,
    BLOCKER_RUN_INDEX_EVIDENCE,
    BLOCKER_RUN_INDEX_SCHEMA,
    BLOCKER_SESSIONS,
    CODE_LEGACY_REPLAY,
    CODE_RUN_INDEX,
    CODE_RUN_INDEX_EVIDENCE,
    CODE_SESSION_RESOLVER,
    DurableRunIndexProvider,
    DurableRunSequenceIndex,
    LegacyChatStreamServices,
    SessionFactoryChatSessionResolver,
    UnavailableChatSessionResolver,
    build_web_stream_runtime,
    runtime_blockers,
)
from prodavan.application.web_facade_adapter.service import WebFacadeAdapter
from prodavan.application.web_facade_adapter.sessions import ResolvedChatSession
from prodavan.application.web_facade_adapter.streams import (
    AdapterChatStreamFacade,
    UnavailableChatStreamFacade,
    UnavailableRunIndexProvider,
)
from prodavan.domain.errors import AppError

from .acceptance_fixtures import (
    CABINET,
    PROJECT,
    RUN,
    SESSION,
    FakeAsyncSession,
    FakeSessionFactory,
    composed_runtime,
    employee,
    principal,
)


def _scope() -> object:
    from prodavan.application.web_facade_adapter.envelopes import StreamScope

    return StreamScope(project_id=PROJECT, session_id=SESSION)


# ---------------------------------------------------------------------------
# Part-by-part composition
# ---------------------------------------------------------------------------


def test_process_factory_composes_all_parts_from_real_dependencies() -> None:
    """Default build wires the resolver, the durable index and the facade."""
    factory = FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[]))
    runtime = build_web_stream_runtime(session_factory=factory)

    assert isinstance(runtime.sessions, SessionFactoryChatSessionResolver)
    assert isinstance(runtime.index_provider, DurableRunIndexProvider)
    assert isinstance(runtime.chat_stream, AdapterChatStreamFacade)
    assert runtime_blockers(runtime) == ()


def test_missing_session_factory_stubs_each_part_individually() -> None:
    """No DB ⇒ one named blocker per part, never a single blanket stub."""
    runtime = build_web_stream_runtime(session_factory=None)

    assert isinstance(runtime.sessions, UnavailableChatSessionResolver)
    assert isinstance(runtime.index_provider, UnavailableRunIndexProvider)
    assert isinstance(runtime.chat_stream, UnavailableChatStreamFacade)
    assert runtime_blockers(runtime) == (
        BLOCKER_SESSIONS,
        BLOCKER_RUN_INDEX,
        BLOCKER_CHAT_STREAM,
    )


def test_injected_part_replaces_only_that_part() -> None:
    """A caller can override the run index and keep the real resolver/facade."""
    factory = FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[]))
    index = UnavailableRunIndexProvider()
    runtime = build_web_stream_runtime(session_factory=factory, index_provider=index)

    assert runtime.index_provider is index
    assert isinstance(runtime.sessions, SessionFactoryChatSessionResolver)
    assert isinstance(runtime.chat_stream, AdapterChatStreamFacade)
    assert runtime_blockers(runtime) == (BLOCKER_RUN_INDEX,)


def test_facade_adapter_requires_chat_run_scope() -> None:
    """The application-level CHAT_RUN check stays enabled in the real facade."""
    from prodavan.application.web_facade_adapter.contracts import AdapterScope

    factory = FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[]))
    runtime = build_web_stream_runtime(session_factory=factory)
    assert isinstance(runtime.chat_stream, AdapterChatStreamFacade)
    adapter = runtime.chat_stream._adapter  # noqa: SLF001 - acceptance asserts composition
    assert isinstance(adapter, WebFacadeAdapter)
    assert tuple(adapter._scopes) == (AdapterScope.CHAT_RUN,)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Durable run/sequence index
# ---------------------------------------------------------------------------


async def test_durable_index_provider_indexes_pairs_from_agent_runs_and_events() -> None:
    factory = FakeSessionFactory(
        FakeAsyncSession(runs=[(RUN, "running")], pairs=[(RUN, 1), (RUN, 2)])
    )
    index = await DurableRunIndexProvider(factory)(_scope())

    assert isinstance(index, DurableRunSequenceIndex)
    assert index.project_id == PROJECT
    assert index.session_id == SESSION
    assert index.run_id == RUN
    assert index.has(RUN, 1) is True
    assert index.has(RUN, 3) is False
    assert index.has("other-run", 1) is False
    # A negative sequence is never a member, whatever the durable rows say.
    assert index.has(RUN, -1) is False


async def test_durable_index_provider_refuses_a_session_without_runs() -> None:
    """No evidence must not become an empty index (that would misattribute every frame)."""
    factory = FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[]))
    with pytest.raises(AppError) as caught:
        await DurableRunIndexProvider(factory)(_scope())

    assert caught.value.status == 503
    assert caught.value.code == CODE_RUN_INDEX_EVIDENCE
    assert caught.value.extra["blocker"] == BLOCKER_RUN_INDEX_EVIDENCE


async def test_durable_index_provider_redacts_a_storage_failure() -> None:
    class ExplodingSession:
        async def execute(self, statement: object) -> object:
            raise RuntimeError("connection refused to postgresql://user:secret@db/app")

    class ExplodingFactory:
        def __call__(self) -> ExplodingFactory:
            return self

        async def __aenter__(self) -> ExplodingSession:
            return ExplodingSession()

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    with pytest.raises(AppError) as caught:
        await DurableRunIndexProvider(ExplodingFactory())(_scope())

    assert caught.value.status == 503
    assert caught.value.code == CODE_RUN_INDEX
    assert caught.value.extra["blocker"] == BLOCKER_RUN_INDEX_SCHEMA
    # The DSN/exception text must never reach the browser payload.
    assert "secret" not in (caught.value.detail or "")
    assert "postgresql" not in (caught.value.detail or "")


def test_durable_index_claims_live_tail_only_for_one_bound_live_run() -> None:
    running = DurableRunSequenceIndex(
        project_id=PROJECT, session_id=SESSION, run_id=RUN, pairs={(RUN, 1)}, live_tail=True
    )
    # The bound run may advance past the durable snapshot while it is live …
    assert running.has(RUN, 7) is True
    # … but no other run ever becomes a member.
    assert running.has("other-run", 7) is False

    finished = DurableRunSequenceIndex(
        project_id=PROJECT, session_id=SESSION, run_id=RUN, pairs={(RUN, 1)}, live_tail=False
    )
    assert finished.has(RUN, 7) is False


def test_durable_index_reverse_lookup_is_deterministic() -> None:
    from prodavan.application.web_api.contracts import uuid5_from_run

    index = DurableRunSequenceIndex(
        project_id=PROJECT, session_id=SESSION, run_id=RUN, pairs={(RUN, 1), (RUN, 2)}
    )
    assert index.find_by_event_id(uuid5_from_run(RUN, 2)) == (RUN, 2)
    # A UUID for a pair that was never indexed is not resolved by arithmetic.
    assert index.find_by_event_id(uuid5_from_run(RUN, 9)) is None


def test_multi_run_session_claims_no_single_run() -> None:
    """Two runs in one session ⇒ no bound run, so a live tail is never assumed."""
    bound, live = DurableRunIndexProvider._bound_run({"run-a": "running", "run-b": "succeeded"})
    assert bound is None
    assert live is False


def test_single_terminal_run_is_not_a_live_tail() -> None:
    bound, live = DurableRunIndexProvider._bound_run({RUN: "succeeded"})
    assert bound == RUN
    assert live is False


# ---------------------------------------------------------------------------
# Session resolver / legacy chat stream
# ---------------------------------------------------------------------------


async def test_session_factory_resolver_opens_its_own_unit_of_work() -> None:
    """The transport passes no session, so the resolver opens one per call."""

    class ExplodingSession(FakeAsyncSession):
        async def execute(self, statement: object) -> object:
            raise RuntimeError("acceptance fixture has no database")

        async def get(self, entity: object, ident: object) -> object:
            raise RuntimeError("acceptance fixture has no database")

    factory = FakeSessionFactory(ExplodingSession(runs=[], pairs=[]))
    resolver = SessionFactoryChatSessionResolver(factory)

    with pytest.raises(RuntimeError):
        await resolver.resolve(
            principal=principal(),
            employee=employee(),
            project_id=PROJECT,
            session_id=SESSION,
        )
    assert factory.opened == 1


async def test_unavailable_resolver_names_its_own_part() -> None:
    with pytest.raises(AppError) as caught:
        await UnavailableChatSessionResolver().resolve(
            principal=principal(),
            employee=employee(),
            project_id=PROJECT,
            session_id=SESSION,
        )

    assert caught.value.status == 503
    assert caught.value.code == CODE_SESSION_RESOLVER
    assert caught.value.extra["blocker"] == BLOCKER_SESSIONS


async def test_legacy_chat_stream_refuses_a_reconnect_cursor() -> None:
    """Legacy rows carry no run identity; a cursor would start a second turn."""
    factory = FakeSessionFactory(FakeAsyncSession(runs=[], pairs=[]))
    services = LegacyChatStreamServices(factory)

    from prodavan.application.web_facade.contracts import ChatRunRequest

    stream = services.chat_run(
        context=None,  # type: ignore[arg-type] - refused before the context is read
        request=ChatRunRequest(project_id=PROJECT, session_id=SESSION, prompt="hi"),
        after_seq=3,
    )
    with pytest.raises(AppError) as caught:
        await stream.__anext__()

    assert caught.value.status == 503
    assert caught.value.code == CODE_LEGACY_REPLAY
    assert caught.value.extra["blocker"] == BLOCKER_LEGACY_REPLAY
    # Nothing was opened: the refusal happens before the turn is started.
    assert factory.opened == 0


def test_resolved_session_contract_matches_factory_expectation() -> None:
    resolved = ResolvedChatSession(project_id=PROJECT, session_id=SESSION, cabinet_id=CABINET)
    assert (resolved.project_id, resolved.session_id, resolved.cabinet_id) == (
        PROJECT,
        SESSION,
        CABINET,
    )


def test_composed_runtime_helper_is_the_all_present_case() -> None:
    runtime = composed_runtime()
    assert runtime_blockers(runtime) == ()
