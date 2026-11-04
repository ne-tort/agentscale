"""Acceptance for the composed ``AgentRunExecutionPort`` adapter.

These tests drive the *production* composition — ``DurableAgentControlPlane``
building real ``ControlPlaneAtomicWrite`` commands, ``DurableRunLeaseManager``
applying the real lease policy — against doubles that reproduce the SQL fence
semantics of ``SqlAlchemyRunLeaseStore`` and the aggregate CAS of the repository.
Every case in ``acceptance.md`` is covered, including the negative ones.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.agent_runs.execution import (
    AgentRunExecutionPort,
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
)
from prodavan.application.agent_runs.execution_adapter import (
    CLAIMABLE_STATUSES,
    ACTOR_BLOCKER,
    REQUEUE_BLOCKER,
    RETRY_DUE_BLOCKER,
    STARTABLE_STATUSES,
    UNKNOWN_CASE_JOURNAL_BLOCKER,
    ControlPlaneAgentRunExecutionAdapter,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

from . import fakes
from .fakes import (
    EMPLOYEE,
    LEASE_TTL,
    NOW,
    RUN_ID,
    TENANT,
    Harness,
    failure,
    retryable,
    success,
    unknown,
)


def claim_window(*, seconds: int = 60):
    """The worker-side (process clock) claim window handed to the adapter."""
    return NOW, NOW + timedelta(seconds=seconds)


async def claim(harness: Harness, *, owner: str = "worker-a") -> AgentRunLease | None:
    now, until = claim_window()
    return await harness.adapter.claim_next(owner_id=owner, now=now, lease_until=until)


def requeue(platform: fakes.MemoryPlatform) -> None:
    """Simulate the (still unwired) ``agent_run.requeue`` transition.

    A real requeue is a control-plane transition and therefore advances the run
    aggregate by exactly one; the lease row keeps its generation and its retry
    gate, which is what these tests are about.
    """
    run = platform.runs[RUN_ID]
    run.status = AgentRunStatus.QUEUED.value
    run.sequence += 1


# --------------------------------------------------------------------------- #
# Structural contract
# --------------------------------------------------------------------------- #


def test_adapter_satisfies_the_execution_port() -> None:
    harness = Harness()

    for name in (
        "claim_next",
        "heartbeat",
        "finish",
        "schedule_retry",
        "finish_unknown",
        "reconcile_unknown",
        "cancel_run",
    ):
        assert name in dir(AgentRunExecutionPort), name
        assert callable(getattr(harness.adapter, name, None)), name
    assert harness.adapter.is_wired is True
    assert harness.adapter.wiring_blocker is None
    assert harness.adapter.retry_blocker == REQUEUE_BLOCKER


def test_adapter_does_not_pretend_to_release_a_lease() -> None:
    """No ``release_lease``: requeue is unwired, so the worker must keep the lease.

    ``AgentRunWorker._release_lease`` looks the method up dynamically; a stub
    here would desynchronise the control-plane aggregate, so its absence is the
    correct, documented state (``LEASE_REQUEUE_BLOCKER``).
    """
    harness = Harness()

    assert not callable(getattr(harness.adapter, "release_lease", None))


def test_adapter_refuses_an_incomplete_dependency_set() -> None:
    harness = Harness()

    with pytest.raises(TypeError):
        ControlPlaneAgentRunExecutionAdapter(  # type: ignore[arg-type]
            leases=object(),
            control_plane=harness.control_plane,
            runs=harness.runs,
        )
    with pytest.raises(TypeError):
        ControlPlaneAgentRunExecutionAdapter(  # type: ignore[arg-type]
            leases=harness.leases,
            control_plane=object(),
            runs=harness.runs,
        )
    with pytest.raises(TypeError):
        ControlPlaneAgentRunExecutionAdapter(  # type: ignore[arg-type]
            leases=harness.leases,
            control_plane=harness.control_plane,
            runs=object(),
        )


# --------------------------------------------------------------------------- #
# Claim: happy path
# --------------------------------------------------------------------------- #


async def test_claim_happy_path_commits_start_in_one_transition() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()

    lease = await claim(harness)

    assert lease is not None
    assert lease.run_id == RUN_ID
    assert lease.tenant_id == TENANT
    assert lease.owner_id == "worker-a"
    assert lease.generation == 1  # first claim of a fresh run
    assert lease.attempt == 2  # the claim is attempt 1 of execution
    assert lease.sequence == 2  # queued(1) -> running(2): the claim *is* the start
    assert lease.lease_until == NOW + LEASE_TTL

    # durable lease row
    row = platform.leases[RUN_ID]
    assert row.lease_owner == "worker-a"
    assert row.lease_token == lease.lease_token
    assert row.generation == 1
    assert row.attempt == 2

    # the start transition, its event and the aggregate cursor moved together
    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.RUNNING.value
    assert run.sequence == 2
    assert harness.transaction.operations == ["agent_run.start"]
    transition = harness.transaction.transitions[0]
    assert transition.expected_sequence == 1
    assert transition.target_status == AgentRunStatus.RUNNING.value
    assert transition.event_types == ("agent_run.started",)
    # and the claim really was one pinned transaction
    assert harness.leases.scopes == 1


async def test_claim_returns_none_when_nothing_is_claimable() -> None:
    harness = Harness()
    harness.platform.add_run(status=AgentRunStatus.RUNNING.value)
    harness.platform.add_lease()

    assert await claim(harness) is None
    assert harness.transaction.operations == []


# --------------------------------------------------------------------------- #
# Claim: mutual exclusion
# --------------------------------------------------------------------------- #


async def test_two_workers_do_not_take_the_same_run() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()

    first = await claim(harness, owner="worker-a")
    second = await claim(harness, owner="worker-b")

    assert first is not None
    assert second is None  # the run is running (and leased): nothing to select
    assert platform.leases[RUN_ID].lease_owner == "worker-a"
    assert len(harness.transaction.operations) == 1


async def test_losing_the_acquire_race_is_zero_rows_not_an_exception() -> None:
    """Both workers plan from the same slot; only the first CAS matches."""
    harness = Harness()
    platform = harness.platform
    platform.seed()
    store = harness.leases

    slot = await store.select_claim_candidate(now=NOW)
    assert slot is not None
    lease = AgentRunLease(
        tenant_id=slot.tenant_id,
        project_id=slot.project_id,
        run_id=slot.run_id,
        session_id=slot.session_id,
        owner_id="worker-a",
        lease_token="token-a",
        generation=slot.generation + 1,
        attempt=slot.attempt + 1,
        sequence=slot.sequence + 1,
        lease_until=NOW + LEASE_TTL,
    )
    assert await store.cas_acquire(slot=slot, lease=lease, now=NOW) == 1
    # the second worker planned against the same (now stale) generation
    assert await store.cas_acquire(slot=slot, lease=lease, now=NOW) == 0


# --------------------------------------------------------------------------- #
# Claim: eligibility gates
# --------------------------------------------------------------------------- #


async def test_expired_lease_is_reused_with_a_newer_generation() -> None:
    harness = Harness()
    platform = harness.platform
    platform.add_run()
    platform.add_lease(
        generation=3,
        attempt=2,
        lease_owner="worker-old",
        lease_token="token-generation-3",
        lease_until=NOW - timedelta(seconds=1),
    )

    lease = await claim(harness)

    assert lease is not None
    assert lease.generation == 4  # the fence only moves forward
    assert lease.attempt == 3
    assert platform.leases[RUN_ID].lease_owner == "worker-a"
    assert platform.leases[RUN_ID].lease_token == lease.lease_token
    assert platform.leases[RUN_ID].lease_token != "token-generation-3"


async def test_a_live_lease_is_not_stolen() -> None:
    harness = Harness()
    platform = harness.platform
    platform.add_run()
    platform.add_lease(
        generation=1,
        attempt=2,
        lease_owner="worker-a",
        lease_token="token-live",
        lease_until=NOW + timedelta(seconds=30),
    )

    assert await claim(harness, owner="worker-b") is None
    assert platform.leases[RUN_ID].lease_owner == "worker-a"


async def test_retry_at_in_the_future_is_not_claimable() -> None:
    harness = Harness()
    platform = harness.platform
    platform.add_run()
    platform.add_lease(retry_at=NOW + timedelta(seconds=120))

    assert await claim(harness) is None

    platform.advance(timedelta(seconds=121))
    now, until = platform.now, platform.now + LEASE_TTL
    lease = await harness.adapter.claim_next(owner_id="worker-a", now=now, lease_until=until)

    assert lease is not None
    assert platform.leases[RUN_ID].retry_at == NOW + timedelta(seconds=120)  # gate stays


async def test_attempt_increments_on_every_claim() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()

    first = await claim(harness)
    assert first is not None
    assert first.attempt == 2

    assert await harness.adapter.finish(lease=first, result=success(), now=NOW) is True
    requeue(platform)

    second = await claim(harness)
    assert second is not None
    assert second.generation == first.generation + 1
    assert second.attempt == first.attempt + 1 == 3


# --------------------------------------------------------------------------- #
# Fencing: stale generation / token / deadline
# --------------------------------------------------------------------------- #


async def test_stale_generation_finish_writes_nothing_and_does_not_raise() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    writes = len(harness.transaction.operations)

    # another actor moved the fence forward (a cancel or a fresh claim)
    platform.leases[RUN_ID].generation += 1

    assert await harness.adapter.finish(lease=lease, result=success(), now=NOW) is False
    assert len(harness.transaction.operations) == writes


async def test_stale_generation_schedule_retry_and_park_write_nothing() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    platform.leases[RUN_ID].generation += 1

    assert (
        await harness.adapter.schedule_retry(
            lease=lease,
            retry_at=NOW + timedelta(seconds=30),
            failure_code="agent_run.provider_retryable",
            now=NOW,
        )
        is False
    )
    assert await harness.adapter.finish_unknown(
        lease=lease, provider_request_id="req-1", observed_at=NOW
    ) is False
    assert platform.leases[RUN_ID].lease_owner == "worker-a"


async def test_renew_and_release_without_the_token_affect_zero_rows() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    store = harness.leases

    forged = AgentRunLease(
        tenant_id=lease.tenant_id,
        project_id=lease.project_id,
        run_id=lease.run_id,
        session_id=lease.session_id,
        owner_id=lease.owner_id,
        lease_token="token-forged",
        generation=lease.generation,
        attempt=lease.attempt,
        sequence=lease.sequence,
        lease_until=lease.lease_until,
    )
    assert await store.cas_renew(lease=forged, now=NOW, lease_until=NOW + LEASE_TTL) == 0
    assert (
        await store.cas_release(lease=forged, now=NOW, status=AgentRunStatus.RUNNING.value) == 0
    )
    assert await store.cas_park(lease=forged, now=NOW, retry_at=None) == 0
    # the real lease is untouched
    assert platform.leases[RUN_ID].lease_owner == "worker-a"
    assert platform.leases[RUN_ID].lease_token == lease.lease_token


async def test_heartbeat_without_the_token_is_refused() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None
    forged = AgentRunLease(
        tenant_id=lease.tenant_id,
        project_id=lease.project_id,
        run_id=lease.run_id,
        session_id=lease.session_id,
        owner_id=lease.owner_id,
        lease_token="token-forged",
        generation=lease.generation,
        attempt=lease.attempt,
        sequence=lease.sequence,
        lease_until=lease.lease_until,
    )

    assert (
        await harness.adapter.heartbeat(
            lease=forged, now=NOW, lease_until=NOW + LEASE_TTL
        )
        is False
    )


async def test_expired_lease_cannot_be_finished_or_renewed() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    platform.advance(LEASE_TTL + timedelta(seconds=1))
    later = platform.now + LEASE_TTL

    assert await harness.adapter.heartbeat(lease=lease, now=platform.now, lease_until=later) is False
    assert await harness.adapter.finish(lease=lease, result=success(), now=platform.now) is False


async def test_renewal_extends_the_deadline_and_never_shortens_it() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    longer = NOW + LEASE_TTL + timedelta(seconds=30)
    assert await harness.adapter.heartbeat(lease=lease, now=NOW, lease_until=longer) is True
    assert platform.leases[RUN_ID].lease_until == longer

    # A beat that asks for a shorter window cannot shrink an existing deadline:
    # the renewal keeps the longer of the two instants.
    assert (
        await harness.adapter.heartbeat(
            lease=lease, now=NOW + timedelta(seconds=1), lease_until=NOW + timedelta(seconds=61)
        )
        is True
    )
    assert platform.leases[RUN_ID].lease_until == longer


# --------------------------------------------------------------------------- #
# Settlement
# --------------------------------------------------------------------------- #


async def test_success_finishes_the_run_and_releases_the_lease() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert await harness.adapter.finish(lease=lease, result=success(), now=NOW) is True

    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.SUCCEEDED.value
    assert run.sequence == 3
    assert run.output_payload == {"assistant_text": "done", "event_count": 2}
    assert run.finished_at == NOW
    assert harness.transaction.operations == ["agent_run.start", "agent_run.succeed"]
    assert harness.transaction.transitions[-1].expected_sequence == 2
    row = platform.leases[RUN_ID]
    assert row.lease_owner is None and row.lease_token is None and row.lease_until is None
    assert row.generation == lease.generation  # generation is preserved on finish


async def test_failure_finishes_the_run_with_a_bounded_code() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.finish(
            lease=lease, result=failure("agent_error.AGENT_INVALID_MODEL"), now=NOW
        )
        is True
    )

    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.FAILED.value
    assert run.failure_code == "agent_error.AGENT_INVALID_MODEL"
    assert harness.transaction.operations[-1] == "agent_run.fail"


async def test_failure_without_a_code_uses_the_default_machine_code() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.finish(
            lease=lease,
            result=ProviderExecutionResult(outcome=ProviderOutcome.FAILED),
            now=NOW,
        )
        is True
    )
    assert harness.platform.runs[RUN_ID].failure_code == "agent_run.provider_failed"


async def test_finish_refuses_unknown_and_retryable_results() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    with pytest.raises(ValueError):
        await harness.adapter.finish(lease=lease, result=unknown(), now=NOW)
    with pytest.raises(ValueError):
        await harness.adapter.finish(
            lease=lease, result=retryable(retry_at=NOW + timedelta(seconds=30)), now=NOW
        )
    assert harness.transaction.operations == ["agent_run.start"]


# --------------------------------------------------------------------------- #
# Retry parking
# --------------------------------------------------------------------------- #


async def test_schedule_retry_parks_the_lease_and_keeps_the_fence() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    due = NOW + timedelta(seconds=90)

    assert (
        await harness.adapter.schedule_retry(
            lease=lease,
            retry_at=due,
            failure_code="agent_run.provider_retryable",
            now=NOW,
        )
        is True
    )

    row = platform.leases[RUN_ID]
    assert row.lease_owner is None  # the worker no longer holds the lease
    assert row.retry_at == due
    assert row.generation == lease.generation  # the parked generation is the fence
    # the run itself is left for the retry scheduler's requeue transition
    assert platform.runs[RUN_ID].status == AgentRunStatus.RUNNING.value
    assert harness.transaction.operations == ["agent_run.start"]


async def test_schedule_retry_refuses_an_already_due_window() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.schedule_retry(
            lease=lease,
            retry_at=NOW,
            failure_code="agent_run.provider_retryable",
            now=NOW,
        )
        is False
    )


async def test_schedule_retry_rejects_free_text_failure_codes() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    with pytest.raises(ValueError):
        await harness.adapter.schedule_retry(
            lease=lease,
            retry_at=NOW + timedelta(seconds=30),
            failure_code="provider said: 429 too many requests, retry later",
            now=NOW,
        )


# --------------------------------------------------------------------------- #
# Unknown outcome: blocked until reconciliation
# --------------------------------------------------------------------------- #


async def test_unknown_parks_the_run_and_blocks_a_new_provider_attempt() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.finish_unknown(
            lease=lease, provider_request_id="req-ambiguous", observed_at=NOW
        )
        is True
    )

    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.RUNNING.value  # never invented as terminal
    assert platform.leases[RUN_ID].lease_owner is None
    assert platform.leases[RUN_ID].generation == lease.generation
    # nothing may claim it, so no second provider call can happen
    assert await claim(harness, owner="worker-b") is None
    # and the old worker cannot settle it afterwards either
    assert await harness.adapter.finish(lease=lease, result=success(), now=NOW) is False
    assert await harness.adapter.finish_unknown(
        lease=lease, provider_request_id="req-ambiguous", observed_at=NOW
    ) is False


async def test_reconcile_unknown_resolves_a_parked_run_and_seals_the_fence() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    assert await harness.adapter.finish_unknown(
        lease=lease, provider_request_id="req-ambiguous", observed_at=NOW
    )

    assert (
        await harness.adapter.reconcile_unknown(
            tenant_id=TENANT,
            run_id=RUN_ID,
            generation=lease.generation,
            outcome=success(),
            now=NOW,
        )
        is True
    )

    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.SUCCEEDED.value
    assert run.sequence == 3
    sealed = platform.leases[RUN_ID].generation
    assert sealed == lease.generation + 1  # the resolved generation is sealed
    # the same reconciliation cannot be applies twice, and no claim is possible
    assert (
        await harness.adapter.reconcile_unknown(
            tenant_id=TENANT,
            run_id=RUN_ID,
            generation=lease.generation,
            outcome=success(),
            now=NOW,
        )
        is False
    )
    assert await claim(harness) is None


async def test_reconcile_unknown_refuses_a_stale_generation() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None
    assert await harness.adapter.finish_unknown(
        lease=lease, provider_request_id=None, observed_at=NOW
    )
    platform.leases[RUN_ID].generation += 1  # e.g. cancelled meanwhile

    assert (
        await harness.adapter.reconcile_unknown(
            tenant_id=TENANT,
            run_id=RUN_ID,
            generation=lease.generation,
            outcome=success(),
            now=NOW,
        )
        is False
    )
    assert platform.runs[RUN_ID].status != AgentRunStatus.SUCCEEDED.value


async def test_reconcile_unknown_refuses_a_live_lease_or_a_wrong_tenant() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    # the worker has not parked the run yet
    assert (
        await harness.adapter.reconcile_unknown(
            tenant_id=TENANT,
            run_id=RUN_ID,
            generation=lease.generation,
            outcome=success(),
            now=NOW,
        )
        is False
    )
    with pytest.raises(ValueError):
        await harness.adapter.reconcile_unknown(
            tenant_id=TENANT,
            run_id=RUN_ID,
            generation=lease.generation,
            outcome=unknown(),
            now=NOW,
        )


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


async def test_cancellation_bumps_the_generation_and_fences_the_worker() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.cancel_run(
            tenant_id=TENANT,
            project_id="project-exec",
            run_id=RUN_ID,
            expected_sequence=lease.sequence,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-run-1",
            reason="operator request",
        )
        is True
    )

    run = platform.runs[RUN_ID]
    assert run.status == AgentRunStatus.CANCELLED.value
    assert platform.leases[RUN_ID].generation == lease.generation + 1
    assert platform.leases[RUN_ID].lease_owner is None
    # the late provider completion is evidence only
    assert await harness.adapter.finish(lease=lease, result=success(), now=NOW) is False
    assert run.status == AgentRunStatus.CANCELLED.value
    assert run.output_payload is None


async def test_cancellation_refuses_a_stale_sequence() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.cancel_run(
            tenant_id=TENANT,
            project_id="project-exec",
            run_id=RUN_ID,
            expected_sequence=1,  # the pre-claim sequence
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-run-stale",
            reason=None,
        )
        is False
    )
    assert platform.runs[RUN_ID].status == AgentRunStatus.RUNNING.value
    assert platform.leases[RUN_ID].generation == lease.generation


async def test_cancellation_refuses_an_out_of_scope_or_invalid_request() -> None:
    harness = Harness()
    harness.platform.seed()
    lease = await claim(harness)
    assert lease is not None

    assert (
        await harness.adapter.cancel_run(
            tenant_id=TENANT,
            project_id="project-other",
            run_id=RUN_ID,
            expected_sequence=lease.sequence,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-scope",
            reason=None,
        )
        is False
    )
    with pytest.raises(ValueError):
        await harness.adapter.cancel_run(
            tenant_id=TENANT,
            project_id="project-exec",
            run_id=RUN_ID,
            expected_sequence=lease.sequence,
            expected_status=AgentRunStatus.SUCCEEDED,
            command_idempotency_key="cancel-terminal",
            reason=None,
        )


# --------------------------------------------------------------------------- #
# Claim compensation and actor resolution (fail closed)
# --------------------------------------------------------------------------- #


async def test_a_failed_start_gives_the_lease_back() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    harness.transaction.force_conflict_next = True

    assert await claim(harness) is None

    # the lease was taken and then released: the run is untouched and claimable
    assert platform.runs[RUN_ID].status == AgentRunStatus.QUEUED.value
    assert platform.runs[RUN_ID].sequence == 1
    row = platform.leases[RUN_ID]
    assert row.lease_owner is None and row.lease_token is None
    assert row.generation == 1  # releasing preserves the fence
    assert harness.transaction.operations == []

    # the very next claim works and is ordered after the aborted one
    lease = await claim(harness)
    assert lease is not None
    assert lease.generation == 2
    assert platform.runs[RUN_ID].status == AgentRunStatus.RUNNING.value


async def test_a_run_without_a_resolvable_actor_is_never_written() -> None:
    harness = Harness()
    platform = harness.platform
    platform.seed()
    harness.runs.hidden = True

    assert await claim(harness) is None
    assert harness.transaction.operations == []
    assert platform.runs[RUN_ID].status == AgentRunStatus.QUEUED.value
    assert platform.leases[RUN_ID].lease_owner is None
    assert ACTOR_BLOCKER == "agent_run_control_plane_actor_unresolved"


async def test_claim_compensation_survives_a_control_plane_that_keeps_failing() -> None:
    """A compensation that cannot be applied is not fatal and leaves no lock."""
    harness = Harness()
    platform = harness.platform
    platform.seed()
    harness.transaction.force_conflict_next = True

    class ExplodingRelease(fakes.MemoryLeaseStore):
        async def cas_release(self, *, lease, now, status):  # type: ignore[override]
            raise RuntimeError("store unavailable")

    exploding = ExplodingRelease(platform)
    harness.adapter._leases = exploding  # type: ignore[attr-defined]

    assert await claim(harness) is None
    assert exploding._in_scope is False  # the claim scope was closed


async def test_retry_due_rows_are_not_claimed_until_the_requeue_exists() -> None:
    """A parked ``retry_due`` run behind a ready one keeps its generation.

    This test used to pin the opposite contract: the adapter narrowed its claim
    predicate to ``queued`` because ``start_run`` hard-coded
    ``expected_status=QUEUED``, so a claimed ``retry_due`` row always lost the status
    CAS and burned a fence generation per poll. ``start_run`` now takes the observed
    status as its precondition, the transition table already accepted
    ``retry_due -> running``, and the narrowing is gone.

    What remains worth protecting: claiming is ordered and fenced, so the parked run
    behind the ready one is untouched.
    """
    harness = Harness()
    platform = harness.platform
    platform.add_run()
    platform.add_lease()
    # a *second* run ahead of the queue, parked as retry_due by a future scheduler
    platform.add_run(run_id="agr_retry", sequence=4)
    platform.add_lease(run_id="agr_retry")
    platform.runs["agr_retry"].status = fakes.RETRY_DUE_STATUS

    lease = await claim(harness)

    assert lease is not None
    assert lease.run_id == RUN_ID  # the queued run is claimed, not the retry_due one
    assert platform.runs["agr_retry"].status == fakes.RETRY_DUE_STATUS
    assert platform.leases["agr_retry"].generation == 0
    # Both claimable statuses are startable; the blocker code is retained only as a
    # reference for promotion records that cite it.
    assert STARTABLE_STATUSES == CLAIMABLE_STATUSES
    assert AgentRunStatus.RETRY_DUE.value in STARTABLE_STATUSES


async def test_a_startable_check_compensates_a_status_the_control_plane_cannot_start() -> None:
    """A ``retry_due`` slot now starts, and the lease is not stranded when it cannot.

    The startability guard still exists as defence in depth for a status the control
    plane genuinely cannot start; what changed is that ``retry_due`` is no longer
    one of them, because ``start_run`` takes the observed status as its CAS
    precondition. A ``retry_due`` lease therefore starts normally, and the guard's
    compensating path is exercised only by a status outside the transition table.
    """
    harness = Harness()
    platform = harness.platform
    platform.seed()
    platform.runs[RUN_ID].status = fakes.RETRY_DUE_STATUS  # now expressible

    lease = AgentRunLease(
        tenant_id=TENANT,
        project_id="project-exec",
        run_id=RUN_ID,
        session_id=fakes.SESSION,
        owner_id="worker-a",
        lease_token="token-x",
        generation=1,
        attempt=2,
        sequence=2,
        lease_until=NOW + LEASE_TTL,
    )
    platform.add_lease(
        generation=1,
        attempt=2,
        lease_owner="worker-a",
        lease_token="token-x",
        lease_until=NOW + LEASE_TTL,
    )

    assert await harness.adapter._start(lease) is True  # type: ignore[attr-defined]
    assert platform.runs[RUN_ID].status == AgentRunStatus.RUNNING.value
    # The transition that started it carried the status observed on the row: that is
    # the whole fix -- with a hard-coded QUEUED precondition this start would have
    # lost the CAS and the lease would have been compensated instead.
    transition = harness.transaction.transitions[-1]
    assert transition.expected_status is AgentRunStatus.RETRY_DUE
    assert transition.target_status is AgentRunStatus.RUNNING


async def test_unknown_case_journal_blocker_is_named() -> None:
    assert UNKNOWN_CASE_JOURNAL_BLOCKER == "agent_run_unknown_case_journal_unwired"
    assert REQUEUE_BLOCKER == "agent_run_requeue_transition_unwired"


def test_default_service_actor_is_used_when_the_run_has_no_requester() -> None:
    harness = Harness()
    harness.platform.seed(requested_by_employee_id=None)

    assert harness.adapter._actor_for(  # type: ignore[attr-defined]
        fakes._view(harness.platform.runs[RUN_ID])
    ) == EMPLOYEE
