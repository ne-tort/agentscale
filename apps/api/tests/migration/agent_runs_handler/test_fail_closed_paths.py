"""Acceptance: every way the handler must refuse instead of pretending to deliver.

Each case asserts two things at once: the intent is *not* reported delivered, and
the run state is left exactly as it was.  The negative paths are the reason the
handler exists at all — a handler that degraded to "best effort" here would
consume a handoff without executing anything.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.agent_runs import handler as handler_module
from prodavan.application.agent_runs.dispatcher import (
    DispatchContext,
    IntentDispatchAction,
    IntentDispatchReason,
    IntentKind,
)
from prodavan.application.agent_runs.handler import (
    BLOCKER_COMPOSITION_MISSING,
    BLOCKER_EXECUTION_FACTORY_FAILED,
    BLOCKER_EXECUTION_FACTORY_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_LEASE_STORE_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
    IntentExecutionPipeline,
    build_run_intent_handler,
    build_terminal_refusal_handlers,
)
from prodavan.application.agent_runs.worker import WorkerTick, WorkerTickStatus
from prodavan.domain.agent.control_plane import AgentRunStatus

from .acceptance_fixtures import (
    NOW,
    RUN,
    FixedResolver,
    MemoryIntentStore,
    build_harness,
    make_intent,
    make_target,
)


def context(*, target=None, **intent_kwargs) -> DispatchContext:
    return DispatchContext(
        intent=make_intent(**intent_kwargs),
        target=target or make_target(),
        kind=IntentKind.RUN_CREATED,
        execution=None,
    )


# --------------------------------------------------------------------------- #
# Named blockers: a missing dependency never becomes a delivery
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "blocker"),
    [
        ("lease_store", BLOCKER_LEASE_STORE_UNWIRED),
        ("execution_factory", BLOCKER_EXECUTION_FACTORY_UNWIRED),
        ("runs", BLOCKER_RUN_SOURCE_UNWIRED),
        ("journal", BLOCKER_JOURNAL_UNWIRED),
        ("runner", BLOCKER_TURN_RUNNER_UNWIRED),
    ],
)
async def test_missing_dependency_is_a_named_blocker_and_not_a_delivery(
    member: str, blocker: str
) -> None:
    harness = build_harness()
    pipeline = replace(harness.pipeline, **{member: None})
    handler = build_run_intent_handler(pipeline=pipeline, clock=harness.clock)

    outcome = await handler(context())

    assert outcome.action is IntentDispatchAction.RETRY
    assert outcome.reason is IntentDispatchReason.EXECUTION_UNWIRED
    assert outcome.retry_at is not None
    assert outcome.retry_at > NOW
    assert handler.report()["last_blocker"] == blocker
    assert blocker in handler.blockers
    assert handler.report()["delivered"] == 0

    # Nothing executed and nothing was written: the run is untouched.
    assert harness.runner.calls == 0
    assert harness.transitions == []
    assert harness.run.status == AgentRunStatus.QUEUED.value
    assert harness.lease.lease_owner is None
    assert harness.attempt_port.start_calls == 0


async def test_missing_pipeline_reports_a_named_blocker() -> None:
    harness = build_harness()
    handler = build_run_intent_handler(pipeline=None, clock=harness.clock)

    outcome = await handler(context())

    assert outcome.action is IntentDispatchAction.RETRY
    assert outcome.reason is IntentDispatchReason.EXECUTION_UNWIRED
    assert handler.blockers == (BLOCKER_COMPOSITION_MISSING,)
    assert handler.report()["blocked"] is True
    assert harness.runner.calls == 0


def test_partially_assembled_pipeline_reports_every_missing_member() -> None:
    pipeline = IntentExecutionPipeline()

    assert pipeline.wired is False
    assert pipeline.blockers == (
        BLOCKER_LEASE_STORE_UNWIRED,
        BLOCKER_EXECUTION_FACTORY_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
        BLOCKER_JOURNAL_UNWIRED,
        BLOCKER_TURN_RUNNER_UNWIRED,
    )


async def test_execution_factory_failure_is_a_named_blocker() -> None:
    harness = build_harness()

    def broken_factory(_store):
        raise RuntimeError("control plane not composed")

    handler = build_run_intent_handler(
        pipeline=replace(harness.pipeline, execution_factory=broken_factory),
        clock=harness.clock,
    )

    outcome = await handler(context())

    assert outcome.action is IntentDispatchAction.RETRY
    assert outcome.reason is IntentDispatchReason.EXECUTION_UNWIRED
    assert handler.report()["last_blocker"] == BLOCKER_EXECUTION_FACTORY_FAILED
    assert harness.runner.calls == 0
    assert harness.transitions == []


async def test_execution_factory_returning_no_port_is_a_named_blocker() -> None:
    harness = build_harness()
    handler = build_run_intent_handler(
        pipeline=replace(harness.pipeline, execution_factory=lambda _store: None),
        clock=harness.clock,
    )

    outcome = await handler(context())

    assert outcome.action is IntentDispatchAction.RETRY
    assert handler.report()["last_blocker"] == BLOCKER_EXECUTION_FACTORY_UNWIRED
    assert harness.runner.calls == 0


# --------------------------------------------------------------------------- #
# Bounded retries: the handler owns the poison-pill guard for its own decisions
# --------------------------------------------------------------------------- #


async def test_retry_is_bounded_and_due_in_the_future() -> None:
    harness = build_harness()
    handler = build_run_intent_handler(
        pipeline=replace(harness.pipeline, runner=None),
        clock=harness.clock,
        retry_backoff_seconds=5.0,
        max_retry_backoff_seconds=300.0,
        max_attempts=8,
    )

    first = await handler(context(attempts=1))
    third = await handler(context(attempts=3))

    assert first.action is IntentDispatchAction.RETRY
    assert first.retry_at == NOW + timedelta(seconds=5)
    assert third.retry_at == NOW + timedelta(seconds=20)


async def test_exhausted_attempts_become_a_terminal_refusal() -> None:
    harness = build_harness()
    handler = build_run_intent_handler(
        pipeline=replace(harness.pipeline, runner=None),
        clock=harness.clock,
        max_attempts=8,
    )

    outcome = await handler(context(attempts=8))

    assert outcome.action is IntentDispatchAction.REJECTED
    assert outcome.reason is IntentDispatchReason.ATTEMPTS_EXHAUSTED
    assert outcome.retry_at is None
    assert harness.runner.calls == 0


async def test_dispatcher_records_the_blocked_retry_durably() -> None:
    """End to end: a blocked handler leaves a retryable row, never a delivered one."""
    from prodavan.application.agent_runs.dispatcher import EnqueueIntentDispatcher

    harness = build_harness()
    handler = build_run_intent_handler(
        pipeline=replace(harness.pipeline, runner=None), clock=harness.clock
    )
    consumer = MemoryIntentStore(intents=[make_intent()])
    dispatcher = EnqueueIntentDispatcher(
        consumer=consumer,
        resolver=FixedResolver(make_target()),
        execution=None,
        handlers={IntentKind.RUN_CREATED: handler},
    )

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.claimed == 1
    assert report.delivered == 0
    assert report.retried == 1
    assert consumer.delivered_at is None
    assert consumer.state == "pending"
    assert consumer.retry_at is not None
    assert consumer.audit_records[-1].startswith(
        "action=retry reason=agent_run_durable_claim_and_lease_unwired"
    )
    assert harness.run.status == AgentRunStatus.QUEUED.value
    assert harness.runner.calls == 0


# --------------------------------------------------------------------------- #
# Scope refusals: deterministic, so terminal
# --------------------------------------------------------------------------- #


async def test_run_invisible_in_tenant_scope_is_refused_terminally() -> None:
    harness = build_harness()
    harness.runs.hidden = True

    outcome = await harness.handler(context())

    assert outcome.action is IntentDispatchAction.REJECTED
    assert outcome.reason is IntentDispatchReason.SCOPE_UNRESOLVED
    assert harness.runner.calls == 0
    assert harness.transitions == []
    assert harness.run.status == AgentRunStatus.QUEUED.value
    # The claim was compensated: the lease it took was handed back, not kept.
    assert harness.lease.lease_owner is None


async def test_run_projection_read_failure_is_a_bounded_retry() -> None:
    harness = build_harness()
    harness.runs.fail_with = RuntimeError("read failed")

    outcome = await harness.handler(context())

    assert outcome.action is IntentDispatchAction.RETRY
    assert outcome.reason is IntentDispatchReason.HANDLER_FAILED
    assert harness.runner.calls == 0
    assert harness.transitions == []


# --------------------------------------------------------------------------- #
# Stale fence: refuse, never corrupt
# --------------------------------------------------------------------------- #


async def test_stale_generation_mid_turn_is_quarantined_without_corruption() -> None:
    """A superseded fence discards the result; the run keeps its own truth."""

    async def fence_out(_request, _cancel) -> None:
        # Another actor (a cancellation, or a newer claim) moves the generation
        # forward while this turn is still running.
        harness.lease.generation += 1
        harness.lease.clear()

    harness = build_harness()
    harness.runner.on_execute = fence_out
    harness.handler = build_run_intent_handler(pipeline=harness.pipeline, clock=harness.clock)

    outcome = await harness.handler(context())

    assert outcome.action is IntentDispatchAction.QUARANTINED
    assert outcome.reason is IntentDispatchReason.OUTCOME_UNKNOWN
    # The turn did run, but its result was evidence only: no success was written.
    assert harness.runner.calls == 1
    assert harness.run.status == AgentRunStatus.RUNNING.value
    assert harness.run.sequence == 2
    assert harness.run.output_payload is None
    assert harness.transitions == ["agent_run.start"]
    assert [state.value for _, state in harness.attempt_port.finishes] == ["discarded_stale"]
    assert harness.handler.report()["quarantined"] == 1


# --------------------------------------------------------------------------- #
# Terminal refusal handler for kinds nothing executes
# --------------------------------------------------------------------------- #


async def test_terminal_refusal_handler_pins_the_reason_without_execution() -> None:
    harness = build_harness()
    registry = build_terminal_refusal_handlers(
        [IntentKind.APPROVAL_CREATED, IntentKind.RUN_SUCCEEDED],
        reason=IntentDispatchReason.HANDLER_NOT_WIRED,
    )

    assert set(registry) == {IntentKind.APPROVAL_CREATED, IntentKind.RUN_SUCCEEDED}
    for kind, handler in registry.items():
        outcome = await handler(
            DispatchContext(
                intent=make_intent(event_type=kind.value),
                target=make_target(),
                kind=kind,
                execution=None,
            )
        )
        assert outcome.action is IntentDispatchAction.REJECTED
        assert outcome.reason is IntentDispatchReason.HANDLER_NOT_WIRED
    assert harness.runner.calls == 0


def test_terminal_refusal_registry_refuses_the_executable_kind() -> None:
    with pytest.raises(ValueError):
        build_terminal_refusal_handlers([IntentKind.RUN_CREATED])


# --------------------------------------------------------------------------- #
# Decision table: worker tick -> dispatch outcome
# --------------------------------------------------------------------------- #


class _ScriptedTickWorker:
    """Stand-in for ``AgentRunWorker`` returning one scripted tick."""

    tick_status: WorkerTickStatus = WorkerTickStatus.IDLE

    def __init__(self, **_kwargs) -> None:
        pass

    async def tick(self) -> WorkerTick:
        if self.tick_status is WorkerTickStatus.IDLE:
            return WorkerTick(WorkerTickStatus.IDLE)
        return WorkerTick(
            self.tick_status, run_id=RUN, generation=1, wrote_run_state=True
        )


@pytest.mark.parametrize(
    ("tick_status", "action", "reason"),
    [
        (WorkerTickStatus.SUCCEEDED, IntentDispatchAction.DELIVERED, IntentDispatchReason.DELIVERED),
        (WorkerTickStatus.FAILED, IntentDispatchAction.DELIVERED, IntentDispatchReason.DELIVERED),
        (
            WorkerTickStatus.RETRY_SCHEDULED,
            IntentDispatchAction.DELIVERED,
            IntentDispatchReason.DELIVERED,
        ),
        (
            WorkerTickStatus.UNKNOWN_RECORDED,
            IntentDispatchAction.DELIVERED,
            IntentDispatchReason.DELIVERED,
        ),
        (
            WorkerTickStatus.DISCARDED_STALE,
            IntentDispatchAction.QUARANTINED,
            IntentDispatchReason.OUTCOME_UNKNOWN,
        ),
        (
            WorkerTickStatus.REFUSED,
            IntentDispatchAction.REJECTED,
            IntentDispatchReason.SCOPE_UNRESOLVED,
        ),
        (
            WorkerTickStatus.RELEASED_ON_SHUTDOWN,
            IntentDispatchAction.RETRY,
            IntentDispatchReason.HANDLER_FAILED,
        ),
        (
            WorkerTickStatus.ERROR,
            IntentDispatchAction.RETRY,
            IntentDispatchReason.HANDLER_FAILED,
        ),
        (WorkerTickStatus.IDLE, IntentDispatchAction.RETRY, IntentDispatchReason.HANDLER_FAILED),
    ],
)
async def test_worker_tick_maps_to_one_dispatch_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tick_status: WorkerTickStatus,
    action: IntentDispatchAction,
    reason: IntentDispatchReason,
) -> None:
    _ScriptedTickWorker.tick_status = tick_status
    monkeypatch.setattr(handler_module, "AgentRunWorker", _ScriptedTickWorker)

    harness = build_harness()
    outcome = await harness.handler(context())

    assert outcome.action is action
    assert outcome.reason is reason
    if action is IntentDispatchAction.DELIVERED:
        assert harness.handler.report()["delivered"] == 1
    if action is IntentDispatchAction.RETRY:
        assert outcome.retry_at is not None
        assert harness.handler.report()["retried"] == 1
    if action is IntentDispatchAction.QUARANTINED:
        assert harness.handler.report()["quarantined"] == 1
    if action is IntentDispatchAction.REJECTED:
        assert harness.handler.report()["rejected"] == 1


async def test_owner_id_is_derived_from_the_consumer_owner_not_its_token() -> None:
    """The run lease belongs to an identity; the intent's token never reaches it."""
    harness = build_harness()
    observed: dict[str, object] = {}

    async def capture(_request, _cancel) -> None:
        observed["lease_owner"] = harness.lease.lease_owner
        observed["lease_token"] = harness.lease.lease_token

    harness.runner.on_execute = capture

    handler = build_run_intent_handler(
        pipeline=harness.pipeline,
        owner_prefix="agent-run-intent",
        clock=harness.clock,
    )

    outcome = await handler(
        context(lease_owner="agent-run-api:trace-handler", lease_token="intent-lease-token")
    )

    assert outcome.action is IntentDispatchAction.DELIVERED
    assert observed["lease_owner"] == "agent-run-intent:agent-run-api:trace-handler"
    assert observed["lease_token"] != "intent-lease-token"
    assert isinstance(observed["lease_token"], str) and observed["lease_token"]
    # Settlement dropped the lease again.
    assert harness.lease.lease_owner is None
    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
