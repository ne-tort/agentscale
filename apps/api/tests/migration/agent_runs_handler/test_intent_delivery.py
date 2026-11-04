"""Acceptance: one ``agent_run.created`` intent really executes one turn.

The whole production chain is driven here — ``EnqueueIntentDispatcher`` policy,
the new ``AgentRunIntentHandler``, the frozen ``ControlPlaneAgentRunExecutionAdapter``
and ``AgentRunWorker`` — against doubled infrastructure only.  Each case states
the behaviour that was missing while the handler was not wired: an intent that is
claimed is executed, and it is marked delivered exactly once.
"""

from __future__ import annotations

import json

from prodavan.application.agent_runs.dispatcher import (
    EnqueueIntentDispatcher,
    IntentDispatchAction,
    IntentDispatchReason,
    IntentKind,
    IntentScopeViolation,
    build_execution_handlers,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

from .acceptance_fixtures import (
    PROJECT,
    RUN,
    SECRET_MARKER,
    TENANT,
    FixedResolver,
    MemoryIntentStore,
    build_harness,
    capture_handler_logs,
    make_intent,
    make_target,
)


def dispatcher_for(
    consumer: MemoryIntentStore,
    harness,
    *,
    resolver: FixedResolver | None = None,
    max_attempts: int = 8,
    handlers=None,
) -> EnqueueIntentDispatcher:
    from prodavan.application.agent_runs.handler import build_run_intent_handlers

    return EnqueueIntentDispatcher(
        consumer=consumer,
        resolver=resolver or FixedResolver(make_target()),
        execution=None,
        handlers=handlers
        if handlers is not None
        else build_run_intent_handlers(pipeline=harness.pipeline, clock=harness.clock),
        max_attempts=max_attempts,
    )


# --------------------------------------------------------------------------- #
# The delivery that was missing
# --------------------------------------------------------------------------- #


async def test_created_intent_executes_one_turn_and_is_delivered() -> None:
    harness = build_harness()
    intent = make_intent()
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(consumer, harness)

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    # The dispatcher claimed and delivered one intent; nothing was retried.
    assert report.claimed == 1
    assert report.delivered == 1
    assert report.retried == 0
    assert report.rejected == 0
    assert report.quarantined == 0

    # ... and the run really advanced: queued (1) -> running (2) -> succeeded (3).
    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
    assert harness.run.sequence == 3
    assert harness.run.output_payload is not None
    assert harness.run.output_payload["assistant_text"] == "done"
    assert harness.event_types == ["agent_run.started", "agent_run.succeeded"]
    assert harness.transitions == ["agent_run.start", "agent_run.succeed"]

    # One turn, through the real worker/runner path, with a deterministic turn id.
    assert harness.runner.calls == 1
    assert harness.runner.requests[0].turn_id == f"run:{RUN}:1"
    assert harness.runner.requests[0].generation == 1
    assert harness.runner.requests[0].attempt == 2

    # The provider attempt journal saw the start and the terminal outcome.
    assert harness.attempt_port.start_calls == 1
    assert [state.value for _, state in harness.attempt_port.finishes] == ["succeeded"]

    # Settlement dropped the run lease, so the run cannot look held.
    assert harness.lease.lease_owner is None
    assert harness.lease.lease_token is None

    # The intent row is durably delivered with a redacted reason code only.
    assert consumer.state == "delivered"
    assert consumer.delivered_at is not None
    assert consumer.retry_at is None
    assert consumer.records[-1].action is IntentDispatchAction.DELIVERED
    assert consumer.records[-1].reason is IntentDispatchReason.DELIVERED
    assert consumer.audit_records == [
        "action=deliver reason=intent_delivered kind=agent_run.created attempts=1"
    ]

    # Observability: counters only, no payload.
    assert harness.handler.report()["delivered"] == 1
    assert harness.handler.report()["ticks"] == 1
    assert harness.handler.report()["blocked"] is False


async def test_handler_claims_the_run_address_on_the_intent_not_a_neighbour() -> None:
    """A second queued run must not be claimed by this intent's delivery."""
    harness = build_harness()
    # A decoy run exists and is arguably "more eligible" by nothing at all: the
    # addressed store is what makes it unclaimable for this dispatch.
    harness.seed_run(run_id="agr_handler_decoy")
    intent = make_intent()
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(consumer, harness)

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.delivered == 1
    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
    decoy = harness.platform.runs["agr_handler_decoy"]
    assert decoy.status == AgentRunStatus.QUEUED.value
    assert decoy.sequence == 1
    assert harness.platform.leases["agr_handler_decoy"].lease_owner is None

    # The adapter received a store narrowed to exactly the intent's run.
    assert harness.narrowed_stores, "the addressed lease store was never handed to the adapter"
    scope = harness.narrowed_stores[0].scope
    assert (scope.run_id, scope.tenant_id, scope.project_id) == (RUN, TENANT, PROJECT)
    assert scope.expected_sequence == 1
    assert scope.expected_status is AgentRunStatus.QUEUED


async def test_registry_registers_only_executable_kinds() -> None:
    from prodavan.application.agent_runs import (
        EXECUTABLE_INTENT_KINDS,
        build_run_intent_handlers,
    )

    harness = build_harness()
    registry = build_run_intent_handlers(pipeline=harness.pipeline)

    assert set(registry) == {IntentKind.RUN_CREATED}
    assert set(registry) == set(EXECUTABLE_INTENT_KINDS)
    # The generic registry is deliberately *not* what a host should use: with no
    # addressable claim it registers nothing, which is the gap this task closes.
    assert set(build_execution_handlers(None)) == set()


async def test_handler_refuses_a_kind_it_does_not_execute() -> None:
    """A kind registered by mistake is refused terminally, never retried."""
    from prodavan.application.agent_runs.dispatcher import DispatchContext

    harness = build_harness()
    context = DispatchContext(
        intent=make_intent(event_type="agent_run.succeeded"),
        target=make_target(
            run_status=AgentRunStatus.RUNNING, aggregate_status="running", aggregate_sequence=2
        ),
        kind=IntentKind.RUN_SUCCEEDED,
        execution=None,
    )

    outcome = await harness.handler(context)

    assert outcome.action is IntentDispatchAction.REJECTED
    assert outcome.reason is IntentDispatchReason.UNKNOWN_KIND
    assert harness.runner.calls == 0
    assert harness.transitions == []
    assert harness.run.status == AgentRunStatus.QUEUED.value


async def test_delivery_logs_contain_no_secret_and_no_prompt() -> None:
    harness = build_harness(input_payload={"text": f"please summarise {SECRET_MARKER}"})
    intent = make_intent(payload_extra={"prompt": SECRET_MARKER, "api_key": SECRET_MARKER})
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(consumer, harness)

    with capture_handler_logs() as logs:
        await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert harness.run.status == AgentRunStatus.SUCCEEDED.value
    assert SECRET_MARKER not in logs.text
    assert SECRET_MARKER not in json.dumps(harness.handler.report())
    assert SECRET_MARKER not in " ".join(consumer.audit_records)
    assert "intent-lease-token" not in json.dumps(harness.handler.report())


async def test_scope_violation_is_refused_terminally_without_execution() -> None:
    harness = build_harness()
    intent = make_intent()
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(
        consumer,
        harness,
        resolver=FixedResolver(error=IntentScopeViolation(IntentDispatchReason.TENANT_MISMATCH)),
    )

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.rejected == 1
    assert report.delivered == 0
    assert harness.runner.calls == 0
    assert harness.run.status == AgentRunStatus.QUEUED.value
    assert consumer.state == "failed"
    assert consumer.audit_records[-1].startswith("action=reject reason=intent_tenant_mismatch")


async def test_superseded_intent_is_refused_terminally_without_execution() -> None:
    """A stale aggregate version must never re-apply an old transition."""
    harness = build_harness()
    intent = make_intent(aggregate_version=1, status="queued")
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(
        consumer,
        harness,
        resolver=FixedResolver(
            make_target(aggregate_sequence=4, aggregate_status="succeeded", run_status=AgentRunStatus.SUCCEEDED)
        ),
    )

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.rejected == 1
    assert harness.runner.calls == 0
    assert harness.run.status == AgentRunStatus.QUEUED.value
    assert consumer.audit_records[-1].startswith("action=reject reason=intent_state_superseded")


async def test_unknown_intent_kind_is_refused_terminally_by_the_dispatcher() -> None:
    harness = build_harness()
    intent = make_intent(event_type="agent_run.teleported")
    consumer = MemoryIntentStore(intents=[intent])
    dispatcher = dispatcher_for(consumer, harness)

    report = await dispatcher.dispatch_batch(owner_id="worker-a", limit=5)

    assert report.claimed == 1
    assert report.rejected == 1
    assert report.retried == 0
    assert harness.runner.calls == 0
    assert consumer.state == "failed"
    assert consumer.audit_records[-1].startswith("action=reject reason=intent_kind_unknown")
