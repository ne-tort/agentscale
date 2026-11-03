"""Lease tier acceptance: generation fencing, deadlines, CAS semantics.

Authored during the migration verification freeze — not executed here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.agent_runs.lease import (
    CLAIMABLE_STATUSES,
    LEASE_REQUEUE_BLOCKER,
    CasUpdate,
    DurableRunLeaseManager,
    LeasePolicy,
    LeaseRefusal,
    LeaseRefused,
    LeaseSlot,
    LeaseVerdict,
    inspect_lease,
    is_claimable,
    new_lease_token,
    plan_acquisition,
    plan_release,
    plan_renewal,
    verify_lease,
)
from prodavan.domain.agent.control_plane import AgentRunStatus

from .fakes import LEASE_TTL, NOW, RUN_ID, make_lease

DEADLINE = NOW + LEASE_TTL


def queued_slot(**overrides) -> LeaseSlot:
    values = {
        "run_id": RUN_ID,
        "tenant_id": "tenant-worker",
        "project_id": "project-worker",
        "status": AgentRunStatus.QUEUED.value,
        "sequence": 1,
        "attempt": 0,
        "generation": 0,
        "session_id": "session-worker",
    }
    values.update(overrides)
    return LeaseSlot(**values)


def running_slot(**overrides) -> LeaseSlot:
    values = {
        "run_id": RUN_ID,
        "tenant_id": "tenant-worker",
        "project_id": "project-worker",
        "status": AgentRunStatus.RUNNING.value,
        "sequence": 2,
        "attempt": 1,
        "generation": 1,
        "session_id": "session-worker",
        "lease_owner": "worker-a",
        "lease_token": "token-generation-1",
        "lease_until": DEADLINE,
    }
    values.update(overrides)
    return LeaseSlot(**values)


# --- claim eligibility -----------------------------------------------------


def test_claim_only_accepts_queued_and_retry_due() -> None:
    assert CLAIMABLE_STATUSES == {"queued", "retry_due"}
    for status in (
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    ):
        slot = queued_slot(status=status.value)
        assert not is_claimable(slot, now=NOW)
        with pytest.raises(LeaseRefused) as refused:
            plan_acquisition(slot, owner_id="worker-a", now=NOW, lease_until=DEADLINE)
        assert refused.value.reason is LeaseRefusal.NOT_CLAIMABLE


def test_retry_due_is_not_claimable_before_its_retry_time() -> None:
    slot = queued_slot(status="retry_due", retry_at=NOW + timedelta(minutes=1))
    assert not is_claimable(slot, now=NOW)
    later = NOW + timedelta(minutes=2)
    assert is_claimable(slot, now=later)


def test_live_lease_blocks_a_second_claim_and_expiry_opens_it() -> None:
    slot = running_slot()
    assert not is_claimable(slot, now=NOW)
    assert is_claimable(slot, now=DEADLINE)


# --- generation -----------------------------------------------------------


def test_claim_increments_generation_and_books_the_start_sequence() -> None:
    acquisition = plan_acquisition(queued_slot(), owner_id="worker-a", now=NOW, lease_until=DEADLINE)
    assert acquisition.lease.generation == 1
    assert acquisition.observed_generation == 0
    assert acquisition.lease.sequence == 2  # claim commits agent_run.start
    assert acquisition.observed_sequence == 1
    assert acquisition.lease.attempt == 1
    assert acquisition.lease.lease_until == DEADLINE


def test_restart_never_resets_the_generation() -> None:
    """A worker restart after release is ordered *after* everything it did."""
    acquisition = plan_acquisition(
        queued_slot(generation=7, attempt=3, sequence=9),
        owner_id="worker-a",
        now=NOW,
        lease_until=DEADLINE,
    )
    assert acquisition.lease.generation == 8
    assert acquisition.lease.attempt == 4
    assert acquisition.lease.sequence == 10


def test_claim_tokens_are_unguessable_and_unique() -> None:
    tokens = {new_lease_token() for _ in range(64)}
    assert len(tokens) == 64
    assert all(len(token) >= 32 for token in tokens)


def test_deadline_after_now_is_required() -> None:
    with pytest.raises(LeaseRefused) as refused:
        plan_acquisition(queued_slot(), owner_id="worker-a", now=NOW, lease_until=NOW)
    assert refused.value.reason is LeaseRefusal.DEADLINE_INVALID


def test_lease_policy_bounds_are_enforced() -> None:
    assert LeasePolicy().deadline(NOW) == NOW + timedelta(seconds=60)
    with pytest.raises(ValueError):
        LeasePolicy(ttl=timedelta(seconds=1))
    with pytest.raises(ValueError):
        LeasePolicy(ttl=timedelta(days=1))


# --- fence verification ---------------------------------------------------


def test_verify_lease_refuses_a_stale_generation() -> None:
    lease = make_lease(generation=1, until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        verify_lease(running_slot(generation=2, lease_token="token-generation-2"), lease=lease, now=NOW)
    assert refused.value.reason is LeaseRefusal.GENERATION_STALE


def test_verify_lease_refuses_a_mismatched_token_or_owner() -> None:
    lease = make_lease(until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        verify_lease(running_slot(lease_token="other"), lease=lease, now=NOW)
    assert refused.value.reason is LeaseRefusal.TOKEN_MISMATCH

    with pytest.raises(LeaseRefused) as refused:
        verify_lease(running_slot(lease_owner="worker-b"), lease=lease, now=NOW)
    assert refused.value.reason is LeaseRefusal.OWNER_MISMATCH


def test_verify_lease_refuses_after_the_deadline() -> None:
    lease = make_lease(until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        verify_lease(running_slot(), lease=lease, now=DEADLINE)
    assert refused.value.reason is LeaseRefusal.EXPIRED


def test_verify_lease_refuses_a_terminal_run() -> None:
    lease = make_lease(until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        verify_lease(running_slot(status=AgentRunStatus.CANCELLED.value), lease=lease, now=NOW)
    assert refused.value.reason is LeaseRefusal.STATUS_CONFLICT


def test_verify_lease_refuses_a_missing_row() -> None:
    lease = make_lease(until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        verify_lease(None, lease=lease, now=NOW)
    assert refused.value.reason is LeaseRefusal.NOT_LEASED


# --- heartbeat / renewal --------------------------------------------------


def test_renewal_extends_the_deadline_without_changing_the_generation() -> None:
    lease = make_lease(until=DEADLINE)
    check = plan_renewal(
        running_slot(), lease=lease, now=NOW + timedelta(seconds=30), lease_until=DEADLINE + LEASE_TTL
    )
    assert check.holds
    assert check.renewed_until == DEADLINE + LEASE_TTL
    assert lease.generation == 1


def test_renewal_after_expiry_is_refused_not_resurrected() -> None:
    lease = make_lease(until=DEADLINE)
    with pytest.raises(LeaseRefused) as refused:
        plan_renewal(
            running_slot(),
            lease=lease,
            now=DEADLINE + timedelta(seconds=1),
            lease_until=DEADLINE + LEASE_TTL,
        )
    assert refused.value.reason is LeaseRefusal.EXPIRED


def test_renewal_never_shortens_the_current_deadline() -> None:
    lease = make_lease(until=DEADLINE)
    check = plan_renewal(
        running_slot(), lease=lease, now=NOW, lease_until=NOW + timedelta(seconds=10)
    )
    assert check.renewed_until == DEADLINE


def test_inspect_reports_supersession_and_cancellation() -> None:
    lease = make_lease(until=DEADLINE)
    superseded = inspect_lease(
        running_slot(generation=2, lease_token="token-generation-2"), lease=lease, now=NOW
    )
    assert superseded.verdict is LeaseVerdict.SUPERSEDED
    assert superseded.cancelled

    cancelled = inspect_lease(
        running_slot(status=AgentRunStatus.CANCELLED.value), lease=lease, now=NOW
    )
    assert cancelled.verdict is LeaseVerdict.TERMINAL
    assert cancelled.cancelled

    expired = inspect_lease(running_slot(), lease=lease, now=DEADLINE)
    assert expired.verdict is LeaseVerdict.EXPIRED
    assert not expired.holds


# --- release + CAS --------------------------------------------------------


def test_release_keeps_the_generation_and_needs_an_event() -> None:
    lease = make_lease(until=DEADLINE)
    release = plan_release(running_slot(), lease=lease, now=NOW)
    assert release.status == AgentRunStatus.QUEUED.value
    assert release.requires_event is True
    assert lease.generation == 1
    assert LEASE_REQUEUE_BLOCKER == "agent_run_requeue_transition_unwired"


def test_zero_row_cas_is_a_value_not_an_exception() -> None:
    lost = CasUpdate(0)
    assert lost.lost_race and not lost.applied
    assert CasUpdate(1).applied
    with pytest.raises(ValueError):
        CasUpdate(2)
    with pytest.raises(ValueError):
        CasUpdate(-1)


def test_lease_slot_rejects_unknown_statuses_and_partial_leases() -> None:
    with pytest.raises(LeaseRefused):
        queued_slot(status="teleported")
    with pytest.raises(ValueError):
        queued_slot(lease_owner="worker-a")  # token/deadline missing


# --- manager over a store -------------------------------------------------


class _Store:
    """Minimal RunLeaseStore double: one row, honest CAS row counts."""

    def __init__(self, slot: LeaseSlot) -> None:
        self.slot = slot
        self.cas_results: list[int] = []
        self.acquire_calls = 0
        self.renew_calls = 0
        self.release_calls = 0

    async def database_now(self):
        return NOW

    async def select_claim_candidate(self, *, now):
        return self.slot if is_claimable(self.slot, now=now) else None

    async def cas_acquire(self, *, slot, lease, now):
        self.acquire_calls += 1
        result = self.cas_results.pop(0) if self.cas_results else 1
        if result == 1:
            self.slot = LeaseSlot(
                run_id=slot.run_id,
                tenant_id=slot.tenant_id,
                project_id=slot.project_id,
                status=AgentRunStatus.RUNNING.value,
                sequence=lease.sequence,
                attempt=lease.attempt,
                generation=lease.generation,
                session_id=slot.session_id,
                lease_owner=lease.owner_id,
                lease_token=lease.lease_token,
                lease_until=lease.lease_until,
            )
        return result

    async def cas_renew(self, *, lease, now, lease_until):
        self.renew_calls += 1
        if self.slot.generation != lease.generation or self.slot.lease_token != lease.lease_token:
            return 0
        self.slot = LeaseSlot(
            run_id=self.slot.run_id,
            tenant_id=self.slot.tenant_id,
            project_id=self.slot.project_id,
            status=self.slot.status,
            sequence=self.slot.sequence,
            attempt=self.slot.attempt,
            generation=self.slot.generation,
            session_id=self.slot.session_id,
            lease_owner=self.slot.lease_owner,
            lease_token=self.slot.lease_token,
            lease_until=lease_until,
        )
        return 1

    async def cas_release(self, *, lease, now, status):
        self.release_calls += 1
        if self.slot.generation != lease.generation or self.slot.lease_token != lease.lease_token:
            return 0
        self.slot = LeaseSlot(
            run_id=self.slot.run_id,
            tenant_id=self.slot.tenant_id,
            project_id=self.slot.project_id,
            status=status,
            sequence=self.slot.sequence,
            attempt=self.slot.attempt,
            generation=self.slot.generation,
            session_id=self.slot.session_id,
        )
        return 1

    async def read_slot(self, *, run_id):
        return self.slot


async def test_manager_returns_none_when_it_loses_the_claim_race() -> None:
    store = _Store(queued_slot())
    store.cas_results = [0]
    manager = DurableRunLeaseManager(store=store)
    assert await manager.acquire_next(owner_id="worker-a") is None
    assert store.acquire_calls == 1


async def test_manager_claim_then_release_then_claim_keeps_generation_monotonic() -> None:
    store = _Store(queued_slot())
    manager = DurableRunLeaseManager(store=store)

    first = await manager.acquire_next(owner_id="worker-a")
    assert first is not None and first.generation == 1

    released = await manager.release(lease=first)
    assert released.applied

    second = await manager.acquire_next(owner_id="worker-b")
    assert second is not None
    assert second.generation == 2  # never reset to 1
    assert second.lease_token != first.lease_token
    assert second.sequence == first.sequence + 1


async def test_manager_renew_after_expiry_reports_expired_and_writes_nothing() -> None:
    store = _Store(queued_slot())
    manager = DurableRunLeaseManager(store=store)
    lease = await manager.acquire_next(owner_id="worker-a")
    assert lease is not None

    # The database clock moved past the stored deadline: the heartbeat must be
    # refused (a renewed lease cannot resurrect an expired one).
    store.slot = LeaseSlot(
        run_id=store.slot.run_id,
        tenant_id=store.slot.tenant_id,
        project_id=store.slot.project_id,
        status=store.slot.status,
        sequence=store.slot.sequence,
        attempt=store.slot.attempt,
        generation=store.slot.generation,
        session_id=store.slot.session_id,
        lease_owner=store.slot.lease_owner,
        lease_token=store.slot.lease_token,
        lease_until=NOW - timedelta(seconds=1),
    )
    check = await manager.renew(lease=lease)
    assert check.verdict is LeaseVerdict.EXPIRED
    assert check.reason is LeaseRefusal.EXPIRED
    assert store.renew_calls == 0  # no write is attempted for an expired lease
