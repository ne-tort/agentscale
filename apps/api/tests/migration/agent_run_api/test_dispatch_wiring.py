"""Readiness-gate and post-commit dispatch contracts; authored, not executed.

Covered here:

* the feature flag stays off by default and refuses before any probe;
* a missing ORM table is reported against real ``Base.metadata`` with a 503;
* an unavailable table is reported as a database-readiness 503;
* all gates passing flips ``writes_enabled`` and binds the durable consumer;
* mutation refuses to commit an intent no consumer could claim;
* the post-commit trigger is bounded, after-commit only, and degrades to a
  reason code instead of raising.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from prodavan.api.migration_agent_runs import dependencies
from prodavan.api.migration_agent_runs.dependencies import (
    BLOCKER_DATABASE_NOT_READY,
    BLOCKER_DISPATCHER_UNAVAILABLE,
    BLOCKER_FEATURE_FLAG_DISABLED,
    BLOCKER_MODELS_NOT_REGISTERED,
    DISPATCH_MAX_ATTEMPTS,
    DISPATCH_REASON_FAILED,
    DISPATCH_REASON_NO_INTENTS,
    DISPATCH_REASON_TIMEOUT,
    FEATURE_FLAG,
    INTENT_QUEUE_NAME,
    MAX_DISPATCH_BATCH,
    AgentRunBackend,
    dispatch_owner_id,
    get_agent_run_backend,
)
from prodavan.application.agent_runs import DispatchBatchReport
from prodavan.domain.errors import AppError


class RecordingSession:
    """Minimal AsyncSession stand-in: records probes, never touches a database."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.statements: list[str] = []
        self.rolled_back = False
        self._error = error

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(str(statement))
        if self._error is not None:
            raise self._error
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeRepository:
    """Repository double: satisfies the port shape without writing anything."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self, *, write):  # pragma: no cover - the gate never commits
        self.commits += 1
        raise AssertionError("the readiness gate must not commit")


class RecordingDispatcher:
    """DispatchRunner double that records the bounded claim it was given."""

    def __init__(
        self,
        *,
        report: DispatchBatchReport | None = None,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self._report = report if report is not None else DispatchBatchReport(claimed=1, retried=1)
        self._error = error
        self._delay = delay

    async def dispatch_batch(self, *, owner_id: str, limit: int, repair_expired: int = 0):
        self.calls.append((owner_id, limit))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._report


def _backend(
    *,
    dispatcher=None,
    writes_enabled: bool = True,
    session: RecordingSession | None = None,
) -> AgentRunBackend:
    return AgentRunBackend(
        transaction=object(),
        session=session or RecordingSession(),
        writes_enabled=writes_enabled,
        dispatcher=dispatcher,
    )


def test_feature_flag_off_refuses_before_any_probe(monkeypatch) -> None:
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    session = RecordingSession()

    with pytest.raises(AppError) as error:
        asyncio.run(get_agent_run_backend(session=session))

    assert error.value.status == 503
    assert error.value.code == "AGENT_RUN_API_DISABLED"
    assert error.value.extra["blocker"] == BLOCKER_FEATURE_FLAG_DISABLED
    assert error.value.extra["feature_flag"] == FEATURE_FLAG
    # Nothing was queried and no repository/consumer was composed.
    assert session.statements == []
    assert not session.rolled_back


def test_flag_on_with_missing_table_returns_503_models_blocker(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG, "1")
    monkeypatch.setattr(
        dependencies,
        "missing_model_tables",
        lambda _tables: ("agent_control_plane_events", "agent_control_plane_enqueue_intents"),
    )
    session = RecordingSession()

    with pytest.raises(AppError) as error:
        asyncio.run(get_agent_run_backend(session=session))

    assert error.value.status == 503
    assert error.value.code == "AGENT_RUN_PERSISTENCE_NOT_READY"
    assert error.value.extra["blocker"] == BLOCKER_MODELS_NOT_REGISTERED
    assert "agent_control_plane_events" in (error.value.detail or "")
    assert error.value.extra["missing_tables"] == [
        "agent_control_plane_events",
        "agent_control_plane_enqueue_intents",
    ]
    # The metadata gate runs before any database probe.
    assert session.statements == []


def test_flag_on_with_unavailable_table_returns_503_database_blocker(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG, "1")
    monkeypatch.setattr(dependencies, "missing_model_tables", lambda _tables: ())
    session = RecordingSession(
        error=OperationalError("SELECT 1 FROM \"agent_runs\" LIMIT 0", {}, RuntimeError("no table"))
    )

    with pytest.raises(AppError) as error:
        asyncio.run(get_agent_run_backend(session=session))

    assert error.value.status == 503
    assert error.value.code == "AGENT_RUN_PERSISTENCE_NOT_READY"
    assert error.value.extra["blocker"] == BLOCKER_DATABASE_NOT_READY
    assert session.rolled_back is True
    assert session.statements and session.statements[0] == "SELECT 1"


def test_all_gates_passing_enables_writes_and_binds_the_consumer(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG, "1")
    monkeypatch.setattr(dependencies, "missing_model_tables", lambda _tables: ())
    dispatcher = RecordingDispatcher()
    repository = FakeRepository()
    monkeypatch.setattr(dependencies, "_repository_factory", lambda: repository)
    monkeypatch.setattr(dependencies, "_dispatcher_factory", lambda: dispatcher)
    session = RecordingSession()

    backend = asyncio.run(get_agent_run_backend(session=session))

    assert backend.writes_enabled is True
    assert backend.transaction is repository
    assert backend.dispatcher is dispatcher
    backend.require_writes()  # the gate is open, so no refusal
    assert backend.control_plane(tenant_id="tenant-a").is_wired is True
    probed = {statement for statement in session.statements}
    assert "SELECT 1" in probed
    assert any('"agent_control_plane_enqueue_intents"' in statement for statement in probed)
    assert any('"agent_runs"' in statement for statement in probed)


def test_dispatcher_composition_failure_keeps_reads_but_refuses_writes(monkeypatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG, "1")
    monkeypatch.setattr(dependencies, "missing_model_tables", lambda _tables: ())
    monkeypatch.setattr(dependencies, "_repository_factory", FakeRepository)

    def _boom():
        raise RuntimeError("consumer composition failed")

    monkeypatch.setattr(dependencies, "_dispatcher_factory", _boom)

    backend = asyncio.run(get_agent_run_backend(session=RecordingSession()))

    assert backend.writes_enabled is True
    assert backend.dispatcher is None
    with pytest.raises(AppError) as error:
        backend.require_writes()
    assert error.value.status == 503
    assert error.value.code == "AGENT_RUN_PERSISTENCE_NOT_READY"
    assert error.value.extra["blocker"] == BLOCKER_DISPATCHER_UNAVAILABLE


def test_backend_without_the_flag_refuses_mutation_with_a_clear_code() -> None:
    backend = _backend(dispatcher=RecordingDispatcher(), writes_enabled=False)

    with pytest.raises(AppError) as error:
        backend.require_writes()

    assert error.value.status == 503
    assert error.value.code == "AGENT_RUN_API_DISABLED"
    assert error.value.extra["blocker"] == BLOCKER_FEATURE_FLAG_DISABLED
    # A disabled backend never wires the control-plane transaction either.
    assert backend.control_plane(tenant_id="tenant-a").is_wired is False


def test_post_commit_trigger_claims_a_bounded_batch_for_the_exact_command(monkeypatch) -> None:
    dispatcher = RecordingDispatcher(
        report=DispatchBatchReport(claimed=2, retried=2, repaired=1)
    )
    backend = _backend(dispatcher=dispatcher)

    report = asyncio.run(
        backend.dispatch_after_commit(trace_id="agent-command:deadbeef", intent_count=2)
    )

    assert dispatcher.calls == [(dispatch_owner_id("agent-command:deadbeef"), 2)]
    assert dispatcher.calls[0][0].startswith("agent-run-api:")
    assert len(dispatcher.calls[0][0]) <= 128
    assert report.triggered is True
    assert (report.claimed, report.retried, report.delivered) == (2, 2, 0)
    assert report.reason is None  # the round had work, so nothing was deferred

    large = RecordingDispatcher(report=DispatchBatchReport(claimed=MAX_DISPATCH_BATCH))
    asyncio.run(
        _backend(dispatcher=large).dispatch_after_commit(trace_id="t", intent_count=10_000)
    )
    assert large.calls == [(dispatch_owner_id("t"), MAX_DISPATCH_BATCH)]


def test_post_commit_trigger_is_skipped_when_no_intent_was_written() -> None:
    dispatcher = RecordingDispatcher()
    backend = _backend(dispatcher=dispatcher)

    report = asyncio.run(backend.dispatch_after_commit(trace_id="t", intent_count=0))

    assert dispatcher.calls == []
    assert report.triggered is False
    assert report.reason == DISPATCH_REASON_NO_INTENTS


def test_post_commit_trigger_failure_is_reported_and_never_raised() -> None:
    dispatcher = RecordingDispatcher(error=RuntimeError("database is down"))
    backend = _backend(dispatcher=dispatcher)

    report = asyncio.run(backend.dispatch_after_commit(trace_id="t", intent_count=1))

    assert report.triggered is True
    assert report.reason == DISPATCH_REASON_FAILED


def test_post_commit_trigger_timeout_is_reported_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "DISPATCH_TRIGGER_TIMEOUT_SECONDS", 0.01)
    dispatcher = RecordingDispatcher(delay=0.2)
    backend = _backend(dispatcher=dispatcher)

    report = asyncio.run(backend.dispatch_after_commit(trace_id="t", intent_count=1))

    assert report.triggered is True
    assert report.reason == DISPATCH_REASON_TIMEOUT
    assert dispatcher.calls == [(dispatch_owner_id("t"), 1)]


def test_disabled_or_unconsumed_backends_never_trigger_dispatch() -> None:
    dispatcher = RecordingDispatcher()

    disabled = asyncio.run(
        _backend(dispatcher=dispatcher, writes_enabled=False).dispatch_after_commit(
            trace_id="t", intent_count=1
        )
    )
    unconsumed = asyncio.run(
        _backend(dispatcher=None).dispatch_after_commit(trace_id="t", intent_count=1)
    )

    assert disabled.triggered is False
    assert disabled.reason == BLOCKER_FEATURE_FLAG_DISABLED
    assert unconsumed.triggered is False
    assert unconsumed.reason == BLOCKER_DISPATCHER_UNAVAILABLE
    assert dispatcher.calls == []


def test_intent_queue_and_retry_ceiling_are_explicit() -> None:
    # The consumer must poll the queue the control plane writes to, otherwise the
    # handoff would be claimed by nobody.
    assert INTENT_QUEUE_NAME == "agent-control-plane"
    # 288 attempts x 300 s cap is about one day of durable retries; the exact
    # number lives in one place so the API and the consumer cannot disagree.
    assert DISPATCH_MAX_ATTEMPTS >= 8
