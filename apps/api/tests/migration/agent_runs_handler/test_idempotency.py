"""Acceptance: a re-delivered intent never executes the turn a second time.

Three independent layers keep this true in production and each is exercised here:
the consumer's reservation (``ALREADY_DELIVERED`` / ``IN_FLIGHT_UNKNOWN``), the
addressed claim (a run that already started or finished is not addressable), and
the run's own settlement (the worker writes at most one terminal transition).
"""

from __future__ import annotations

from datetime import timedelta

from prodavan.application.agent_runs.dispatcher import (
    DispatchContext,
    EnqueueIntentDispatcher,
    IntentDispatchAction,
    IntentDispatchReason,
    IntentKind,
    ReservationStatus,
)
from prodavan.application.agent_runs.handler import EXECUTABLE_INTENT_KINDS
from prodavan.domain.agent.control_plane import AgentRunStatus

from .acceptance_fixtures import (
    FixedResolver,
    MemoryIntentStore,
    build_harness,
    make_intent,
    make_target,
)


def dispatcher_for(consumer: MemoryIntentStore, harness, *, target=None) -> EnqueueIntentDispatcher:
    return EnqueueIntentDispatcher(
        consumer=consumer,
        resolver=FixedResolver(target or make_target()),
        execution=None,
        handlers={kind: harness.handler for kind in EXECUTABLE_INTENT_KINDS},
    )


def context(*, intent, target) -> DispatchContext:
    return DispatchContext(intent=intent, target=target, kind=IntentKind.RUN_CREATED, execution=None)


async def test_duplicate_delivery_of_a_delivered_intent_does_not_execute() -> None:
    harness = build_harness()
    intent = make_intent()
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(consumer, harness)

    first = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)
    assert first.delivered == 1
    assert harness.runner.calls == 1
    executed_sequence = harness.run.sequence

    # A broker replay hands the same intent over again: the reservation answers
    # ALREADY_DELIVERED and the dispatcher reports delivery without a handler run.
    consumer.redeliver(intent)
    second = await dispatcher.dispatch_batch(owner_id="worker-b", limit=5)

    assert second.claimed == 1
    assert second.delivered == 1
    assert second.retried == 0
    assert second.rejected == 0
    assert second.quarantined == 0
    assert consumer.reserve_calls == 2
    assert consumer.records[-1].reason is IntentDispatchReason.REPLAY_SUPPRESSED
    assert consumer.audit_records[-1] == (
        "action=deliver reason=intent_delivery_replay_suppressed kind=agent_run.created attempts=1"
    )

    # Nothing ran twice and the run state is exactly where it was.
    assert harness.runner.calls == 1
    assert harness.run.sequence == executed_sequence
    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
    assert harness.event_types == ["agent_run.started", "agent_run.succeeded"]
    assert harness.transitions == ["agent_run.start", "agent_run.succeed"]


async def test_duplicate_delivery_of_an_in_flight_intent_is_quarantined() -> None:
    """A row that is mid-dispatch under another claim has an unknown outcome."""
    harness = build_harness()
    intent = make_intent()
    consumer = MemoryIntentStore(intents=[intent], in_flight=True)
    dispatcher = dispatcher_for(consumer, harness)

    report = await dispatcher.dispatch_batch(owner_id="worker-b", limit=5)

    assert report.quarantined == 1
    assert report.delivered == 0
    assert harness.runner.calls == 0
    assert harness.run.status == AgentRunStatus.QUEUED.value
    assert consumer.records[-1].action is IntentDispatchAction.QUARANTINED
    assert consumer.records[-1].reason is IntentDispatchReason.OUTCOME_UNKNOWN
    assert ReservationStatus.IN_FLIGHT_UNKNOWN.value == "in_flight_unknown"


async def test_replay_against_a_finished_run_is_delivered_without_executing() -> None:
    """The handler itself is replay-safe, not only the reservation.

    This is the case where a re-claim reaches a handler after the run already
    finished (for example when the outcome write was lost).  The run is terminal,
    so nothing is claimable: the handoff is reported delivered as a replay and the
    provider is never called again.
    """
    harness = build_harness(status=AgentRunStatus.SUCCEEDED.value, sequence=5)
    harness.run.output_payload = {"assistant_text": "already answered", "event_count": 1}

    outcome = await harness.handler(
        context(
            intent=make_intent(status="succeeded", aggregate_version=5),
            target=make_target(
                run_status=AgentRunStatus.SUCCEEDED,
                aggregate_status="succeeded",
                aggregate_sequence=5,
            ),
        )
    )

    assert outcome.action is IntentDispatchAction.DELIVERED
    assert outcome.reason is IntentDispatchReason.REPLAY_SUPPRESSED
    assert harness.runner.calls == 0
    assert harness.transitions == []
    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
    assert harness.run.sequence == 5
    assert harness.handler.report()["replays"] == 1


async def test_run_already_owned_by_another_claim_is_quarantined() -> None:
    """Executing a run somebody else is driving would run the same turn twice."""
    harness = build_harness(status=AgentRunStatus.RUNNING.value, sequence=2)
    lease = harness.lease
    lease.lease_owner = "worker-b"
    lease.lease_token = "other-token"
    lease.lease_until = harness.clock.now + timedelta(seconds=45)

    outcome = await harness.handler(
        context(
            intent=make_intent(status="running", aggregate_version=2),
            target=make_target(
                run_status=AgentRunStatus.RUNNING,
                aggregate_status="running",
                aggregate_sequence=2,
            ),
        )
    )

    assert outcome.action is IntentDispatchAction.QUARANTINED
    assert outcome.reason is IntentDispatchReason.OUTCOME_UNKNOWN
    assert harness.runner.calls == 0
    assert harness.transitions == []
    assert harness.run.status == AgentRunStatus.RUNNING.value
    assert harness.run.sequence == 2
    # The other claim's lease was not touched.
    assert lease.lease_owner == "worker-b"
    assert lease.lease_token == "other-token"
    assert harness.attempt_port.start_calls == 0


async def test_settlement_runs_once_per_claim() -> None:
    """One delivery = one claim = one attempt = one terminal transition."""
    harness = build_harness()
    consumer = MemoryIntentStore(intents=[make_intent()])
    dispatcher = dispatcher_for(consumer, harness)

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.delivered == 1
    assert harness.runner.calls == 1
    assert harness.attempt_port.start_calls == 1
    assert len(harness.attempt_port.finishes) == 1
    assert harness.run.sequence == 3
    assert harness.transitions == ["agent_run.start", "agent_run.succeed"]
