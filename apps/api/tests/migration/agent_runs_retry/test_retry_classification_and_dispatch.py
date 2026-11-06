"""Failure classification and backoff determinism for Agent Run retries."""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.agent_runs.execution import AgentRunLease, ProviderExecutionResult, ProviderOutcome
from prodavan.application.agent_runs.retry import (
    AgentRunFailureCategory,
    AgentRunFailureClass,
    AgentRunFailureEvidence,
    AgentRunRetryBlocked,
    AgentRunRetryPolicy,
    AgentRunRetryRefused,
    AgentRunRetryScheduleConflict,
    AgentRunRetryScheduler,
    RetryPlanStatus,
    RetryReleaseDecision,
    RetryScheduleIdentity,
    RetryScheduleState,
    RetryScheduleStatus,
    TenantScope,
    TenantScopeViolation,
    WIRING_BLOCKER,
    classify_agent_run_failure,
)

from .acceptance_fixtures import (
    LEASE,
    NOW,
    FakeReleasePort,
    FakeRetryJournal,
    retry_journal_row,
)

IDENTITY = RetryScheduleIdentity(
    tenant_id="tenant-a",
    project_id="project-1",
    run_id="agr_1",
    generation=3,
    attempt=1,
)


def _scheduler(
    journal: FakeRetryJournal | None,
    *,
    policy: AgentRunRetryPolicy | None = None,
    tenants: tuple[str, ...] = ("tenant-a",),
) -> AgentRunRetryScheduler:
    return AgentRunRetryScheduler(
        journal=journal,
        scope=TenantScope(frozenset(tenants)),
        policy=policy or AgentRunRetryPolicy(),
        claim_lease=LEASE,
    )


# --------------------------------------------------------------------------- #
# Classification: the three classes are closed and fail-closed
# --------------------------------------------------------------------------- #


def test_explicit_provider_rejection_statuses_are_retryable() -> None:
    for status in (408, 425, 429, 500, 502, 503, 504):
        classification = classify_agent_run_failure(
            AgentRunFailureEvidence(
                category=AgentRunFailureCategory.TRANSIENT,
                http_status=status,
                request_dispatch_observed=True,
            )
        )
        assert classification.failure_class is AgentRunFailureClass.RETRYABLE, status
        assert classification.error_code.startswith("agent_run.error.")


def test_terminal_errors_are_never_retryable() -> None:
    terminal = [
        AgentRunFailureEvidence(category=AgentRunFailureCategory.AUTHENTICATION, http_status=401),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.AUTHORIZATION, http_status=403),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.INVALID_REQUEST, http_status=422),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.NOT_FOUND, http_status=404),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.CONFLICT, http_status=409),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.PERMANENT),
    ]
    for evidence in terminal:
        classification = classify_agent_run_failure(evidence)
        assert classification.failure_class is AgentRunFailureClass.TERMINAL
        assert classification.retryable is False
        assert classification.reconcile_required is False


def test_unknown_and_ambiguous_outcomes_require_reconciliation_not_retry() -> None:
    ambiguous = [
        AgentRunFailureEvidence(category=AgentRunFailureCategory.UNKNOWN),
        AgentRunFailureEvidence(category=AgentRunFailureCategory.TIMEOUT),
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.CONNECTION,
            request_dispatch_observed=True,
        ),
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.TIMEOUT,
            provider_request_id="req-42",
        ),
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE,
            request_dispatch_observed=True,
        ),
    ]
    for evidence in ambiguous:
        classification = classify_agent_run_failure(evidence)
        assert classification.failure_class is AgentRunFailureClass.RECONCILE_REQUIRED, evidence
        assert classification.retryable is False


def test_only_proven_never_sent_or_explicit_rejection_permits_a_retry() -> None:
    never_sent = classify_agent_run_failure(
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.CONNECTION,
            request_dispatch_observed=False,
        )
    )
    assert never_sent.failure_class is AgentRunFailureClass.RETRYABLE

    accepted_then_timeout = classify_agent_run_failure(
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.TIMEOUT,
            request_dispatch_observed=False,
            provider_request_id="req-9",
        )
    )
    assert accepted_then_timeout.failure_class is AgentRunFailureClass.RECONCILE_REQUIRED


def test_classification_rejects_non_evidence_and_keeps_diagnostics_redacted() -> None:
    with pytest.raises(ValueError):
        classify_agent_run_failure(  # type: ignore[arg-type]
            AgentRunFailureCategory.TRANSIENT
        )
    evidence = AgentRunFailureEvidence(category=AgentRunFailureCategory.TRANSIENT)
    assert "redacted" in evidence.redacted_diagnostic.lower()
    assert not hasattr(evidence, "message")


def test_evidence_rejects_impossible_http_status_and_control_characters() -> None:
    with pytest.raises(ValueError):
        AgentRunFailureEvidence(category=AgentRunFailureCategory.TRANSIENT, http_status=99)
    with pytest.raises(ValueError):
        AgentRunFailureEvidence(category=AgentRunFailureCategory.TRANSIENT, http_status=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentRunFailureEvidence(
            category=AgentRunFailureCategory.TRANSIENT, provider_request_id="req\ninject"
        )


# --------------------------------------------------------------------------- #
# Backoff: deterministic, capped, attempt-bounded, deadline-bounded
# --------------------------------------------------------------------------- #


def test_backoff_is_capped_exponential_and_stable_across_replays() -> None:
    policy = AgentRunRetryPolicy(
        max_attempts=8, initial_delay_seconds=10, max_delay_seconds=80, jitter_basis_points=0
    )
    delays = []
    for attempt in range(1, 6):
        identity = RetryScheduleIdentity("tenant-a", "project-1", "agr_1", 1, attempt)
        plan = policy.plan(
            identity=identity,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
        )
        assert plan.status is RetryPlanStatus.SCHEDULED
        delays.append((plan.retry_at - NOW).total_seconds())
    assert delays == [10.0, 20.0, 40.0, 80.0, 80.0]

    jittered = AgentRunRetryPolicy(
        max_attempts=8, initial_delay_seconds=10, max_delay_seconds=80, jitter_basis_points=3000
    )
    first = jittered.plan(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=NOW,
    )
    replay = jittered.plan(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=NOW,
    )
    assert first == replay
    assert 7 <= (first.retry_at - NOW).total_seconds() <= 13


def test_jitter_differs_between_distinct_attempt_identities() -> None:
    policy = AgentRunRetryPolicy(
        max_attempts=8, initial_delay_seconds=60, max_delay_seconds=600, jitter_basis_points=2500
    )
    instants = {
        policy.plan(
            identity=RetryScheduleIdentity("tenant-a", "project-1", f"agr_{number}", 1, 1),
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
        ).retry_at
        for number in range(12)
    }
    assert len(instants) > 1


def test_provider_retry_hint_is_honoured_only_upward_and_bounded() -> None:
    policy = AgentRunRetryPolicy(
        max_attempts=8, initial_delay_seconds=10, max_delay_seconds=120, jitter_basis_points=0
    )
    evidence = AgentRunFailureEvidence(
        category=AgentRunFailureCategory.RATE_LIMITED, http_status=429
    )
    longer = policy.plan(
        identity=IDENTITY,
        evidence=evidence,
        failed_at=NOW,
        provider_retry_at=NOW + timedelta(seconds=90),
    )
    assert (longer.retry_at - NOW) == timedelta(seconds=90)

    shorter = policy.plan(
        identity=IDENTITY,
        evidence=evidence,
        failed_at=NOW,
        provider_retry_at=NOW + timedelta(seconds=1),
    )
    assert (shorter.retry_at - NOW) == timedelta(seconds=10)

    absurd = policy.plan(
        identity=IDENTITY,
        evidence=evidence,
        failed_at=NOW,
        provider_retry_at=NOW + timedelta(days=30),
    )
    assert (absurd.retry_at - NOW) == timedelta(seconds=120)


def test_attempt_ceiling_exhausts_the_retry_budget() -> None:
    policy = AgentRunRetryPolicy(max_attempts=3, initial_delay_seconds=10, max_delay_seconds=60)
    identity = RetryScheduleIdentity("tenant-a", "project-1", "agr_1", 1, 3)
    plan = policy.plan(
        identity=identity,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=500
        ),
        failed_at=NOW,
    )
    assert plan.status is RetryPlanStatus.EXHAUSTED
    assert plan.retry_at is None and plan.next_attempt is None


def test_deadline_that_cannot_be_met_exhausts_instead_of_scheduling() -> None:
    policy = AgentRunRetryPolicy(max_attempts=5, initial_delay_seconds=60, max_delay_seconds=600)
    plan = policy.plan(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=500
        ),
        failed_at=NOW,
        deadline=NOW + timedelta(seconds=30),
    )
    assert plan.status is RetryPlanStatus.EXHAUSTED
    assert plan.reason_code == "agent_run.retry.deadline_exceeded"


def test_unknown_outcome_plan_is_reconcile_required_without_a_retry_time() -> None:
    policy = AgentRunRetryPolicy()
    plan = policy.plan(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(category=AgentRunFailureCategory.UNKNOWN),
        failed_at=NOW,
    )
    assert plan.status is RetryPlanStatus.RECONCILE_REQUIRED
    assert plan.retry_at is None


def test_policy_rejects_incoherent_bounds() -> None:
    with pytest.raises(ValueError):
        AgentRunRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        AgentRunRetryPolicy(initial_delay_seconds=0)
    with pytest.raises(ValueError):
        AgentRunRetryPolicy(initial_delay_seconds=100, max_delay_seconds=10)
    with pytest.raises(ValueError):
        AgentRunRetryPolicy(jitter_basis_points=10_001)


# --------------------------------------------------------------------------- #
# Scheduling: unknown is never scheduled, terminal is never scheduled
# --------------------------------------------------------------------------- #


async def test_unknown_outcome_is_refused_and_writes_nothing() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)

    with pytest.raises(AgentRunRetryRefused) as refusal:
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(category=AgentRunFailureCategory.UNKNOWN),
            failed_at=NOW,
        )

    assert refusal.value.reason_code == "agent_run.retry.reconcile_required"
    assert journal.rows == {}
    assert journal.insert_calls == 0


async def test_ambiguous_timeout_with_provider_request_id_is_refused() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)

    with pytest.raises(AgentRunRetryRefused):
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.TIMEOUT,
                provider_request_id="req-7",
                request_dispatch_observed=True,
            ),
            failed_at=NOW,
        )
    assert journal.rows == {}


async def test_terminal_error_is_refused_and_writes_nothing() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)

    with pytest.raises(AgentRunRetryRefused) as refusal:
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.AUTHENTICATION, http_status=401
            ),
            failed_at=NOW,
        )

    assert refusal.value.reason_code == "agent_run.retry.terminal"
    assert journal.rows == {}
    assert journal.insert_calls == 0


async def test_retryable_failure_writes_one_pending_row_with_durable_retry_at() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)

    outcome = await scheduler.schedule_retry(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=NOW,
    )

    assert outcome.status is RetryScheduleStatus.SCHEDULED
    row = journal.only_row()
    assert row.state is RetryScheduleState.PENDING
    assert row.retry_at > NOW
    assert row.failure_code == "agent_run.error.provider_unavailable"
    assert row.schedule_key == IDENTITY.key
    assert row.tenant_id == "tenant-a"
    assert outcome.record.retry_at == row.retry_at


async def test_replaying_the_same_decision_is_a_duplicate_not_a_second_row() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)
    evidence = AgentRunFailureEvidence(
        category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
    )

    first = await scheduler.schedule_retry(identity=IDENTITY, evidence=evidence, failed_at=NOW)
    second = await scheduler.schedule_retry(identity=IDENTITY, evidence=evidence, failed_at=NOW)

    assert first.status is RetryScheduleStatus.SCHEDULED
    assert second.status is RetryScheduleStatus.DUPLICATE
    assert second.record.retry_at == first.record.retry_at
    assert len(journal.rows) == 1
    assert journal.insert_calls == 2


async def test_changed_decision_for_one_attempt_is_a_conflict() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(
        journal,
        policy=AgentRunRetryPolicy(
            max_attempts=5, initial_delay_seconds=10, max_delay_seconds=600, jitter_basis_points=0
        ),
    )

    await scheduler.schedule_retry(
        identity=IDENTITY,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=NOW,
    )
    with pytest.raises(AgentRunRetryScheduleConflict):
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
            provider_retry_at=NOW + timedelta(seconds=300),
        )
    assert len(journal.rows) == 1


async def test_exhausted_budget_is_refused_without_a_write() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal, policy=AgentRunRetryPolicy(max_attempts=2))
    last_attempt = RetryScheduleIdentity("tenant-a", "project-1", "agr_1", 1, 2)

    with pytest.raises(AgentRunRetryRefused) as refusal:
        await scheduler.schedule_retry(
            identity=last_attempt,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=500
            ),
            failed_at=NOW,
        )
    assert refusal.value.reason_code == "agent_run.retry.exhausted"
    assert journal.rows == {}


async def test_unwired_scheduler_fails_closed_instead_of_degrading() -> None:
    scheduler = _scheduler(None)
    assert scheduler.is_wired is False
    assert scheduler.wiring_blocker == WIRING_BLOCKER

    with pytest.raises(AgentRunRetryBlocked) as blocked:
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
        )
    assert blocked.value.blocker == WIRING_BLOCKER


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


async def test_foreign_tenant_is_rejected_before_any_journal_call() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal, tenants=("tenant-a",))
    foreign = RetryScheduleIdentity("tenant-b", "project-1", "agr_1", 1, 1)

    with pytest.raises(TenantScopeViolation):
        await scheduler.schedule_retry(
            identity=foreign,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
        )
    assert journal.insert_calls == 0
    assert journal.rows == {}


async def test_journal_returning_another_tenant_is_rejected() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)

    async def cross_tenant_insert(*, record):
        from dataclasses import replace

        return replace(record, tenant_id="tenant-b"), True

    journal.insert_schedule_once = cross_tenant_insert  # type: ignore[assignment]

    with pytest.raises(TenantScopeViolation):
        await scheduler.schedule_retry(
            identity=IDENTITY,
            evidence=AgentRunFailureEvidence(
                category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
            ),
            failed_at=NOW,
        )


async def test_dispatch_scans_only_the_requested_tenant() -> None:
    journal = FakeRetryJournal()
    retry_journal_row(journal, tenant_id="tenant-a", run_id="agr_a")
    retry_journal_row(journal, tenant_id="tenant-b", run_id="agr_b")
    scheduler = _scheduler(journal, tenants=("tenant-a", "tenant-b"))
    release = FakeReleasePort()

    report = await scheduler.dispatch_due(tenant_id="tenant-a", now=NOW + timedelta(minutes=5), release=release)

    assert report.claimed == 1
    assert len(release.calls) == 1
    touched = {row.run_id: row.state for row in journal.rows.values()}
    assert touched["agr_a"] is RetryScheduleState.RELEASED
    assert touched["agr_b"] is RetryScheduleState.PENDING
    assert journal.claim_calls == [("tenant-a", 50)]

    with pytest.raises(TenantScopeViolation):
        await scheduler.dispatch_due(
            tenant_id="tenant-c", now=NOW + timedelta(minutes=5), release=release
        )


async def test_dispatch_rejects_a_claim_from_another_tenant() -> None:
    journal = FakeRetryJournal()
    retry_journal_row(journal, tenant_id="tenant-a", run_id="agr_a")
    retry_journal_row(journal, tenant_id="tenant-b", run_id="agr_b")
    scheduler = _scheduler(journal, tenants=("tenant-a", "tenant-b"))
    release = FakeReleasePort()

    original_claim_due = journal.claim_due

    async def leaking_claim_due(*, tenant_id, now, lease_until, limit):
        local = await original_claim_due(
            tenant_id=tenant_id, now=now, lease_until=lease_until, limit=limit
        )
        foreign = await original_claim_due(
            tenant_id="tenant-b", now=now, lease_until=lease_until, limit=limit
        )
        return tuple(local) + tuple(foreign)

    journal.claim_due = leaking_claim_due  # type: ignore[assignment]

    with pytest.raises(TenantScopeViolation):
        await scheduler.dispatch_due(
            tenant_id="tenant-a", now=NOW + timedelta(minutes=5), release=release
        )
    # The batch is validated before any run is requeued.
    assert release.calls == []


# --------------------------------------------------------------------------- #
# Dispatch: expired backoff is picked up; crashes do not lose work
# --------------------------------------------------------------------------- #


async def test_backoff_that_has_not_expired_is_not_claimed() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort()

    report = await scheduler.dispatch_due(
        tenant_id="tenant-a", now=row.retry_at - timedelta(seconds=1), release=release
    )

    assert report.claimed == 0
    assert release.calls == []
    assert journal.only_row().state is RetryScheduleState.PENDING


async def test_expired_backoff_is_claimed_and_released_exactly_once() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort()

    report = await scheduler.dispatch_due(tenant_id="tenant-a", now=row.retry_at, release=release)

    assert (report.claimed, report.released, report.cancelled, report.deferred) == (1, 1, 0, 0)
    assert journal.only_row().state is RetryScheduleState.RELEASED

    again = await scheduler.dispatch_due(
        tenant_id="tenant-a", now=row.retry_at + timedelta(hours=1), release=release
    )
    assert again.claimed == 0
    assert journal.release_calls == 1


async def test_claim_lease_expiry_makes_a_lost_retry_due_again() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort()

    journal.crash_after_claim = True
    with pytest.raises(RuntimeError):
        await scheduler.dispatch_due(tenant_id="tenant-a", now=row.retry_at, release=release)
    # The crash happened between the claim and its follow-up write.
    assert journal.only_row().state is RetryScheduleState.CLAIMED
    claimed_at = journal.only_row().lease_until
    assert claimed_at is not None

    recovered = await scheduler.dispatch_due(
        tenant_id="tenant-a",
        now=claimed_at + timedelta(seconds=1),
        release=release,
    )

    assert recovered.claimed == 1
    assert recovered.released == 1
    assert journal.only_row().state is RetryScheduleState.RELEASED
    assert journal.only_row().claim_generation == 2


async def test_deferred_claim_stays_claimed_until_its_lease_expires() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort(RetryReleaseDecision.NOT_ELIGIBLE)

    report = await scheduler.dispatch_due(tenant_id="tenant-a", now=row.retry_at, release=release)

    assert (report.claimed, report.released, report.deferred) == (1, 0, 1)
    assert journal.only_row().state is RetryScheduleState.CLAIMED

    release.decision = RetryReleaseDecision.RELEASED
    lease_until = journal.only_row().lease_until
    assert lease_until is not None
    retried = await scheduler.dispatch_due(
        tenant_id="tenant-a", now=lease_until, release=release
    )
    assert retried.released == 1


async def test_terminal_run_cancels_the_schedule_instead_of_requeueing() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort(RetryReleaseDecision.RUN_TERMINAL)

    report = await scheduler.dispatch_due(tenant_id="tenant-a", now=row.retry_at, release=release)

    assert (report.claimed, report.released, report.cancelled) == (1, 0, 1)
    cancelled = journal.only_row()
    assert cancelled.state is RetryScheduleState.CANCELLED
    assert cancelled.cancelled_reason == "agent_run.retry.run_terminal"


async def test_lost_release_race_is_counted_as_deferred_not_released() -> None:
    journal = FakeRetryJournal()
    row = retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)
    release = FakeReleasePort()

    async def always_lose(*, claim, released_at):
        return False

    journal.record_release = always_lose  # type: ignore[assignment]

    report = await scheduler.dispatch_due(tenant_id="tenant-a", now=row.retry_at, release=release)

    assert (report.claimed, report.released, report.deferred) == (1, 0, 1)


async def test_journal_returning_more_claims_than_requested_fails_closed() -> None:
    journal = FakeRetryJournal()
    for attempt in range(1, 4):
        retry_journal_row(journal, attempt=attempt, delay_seconds=60)
    journal.due_claim_limit_override = 9
    scheduler = _scheduler(journal)

    with pytest.raises(AgentRunRetryBlocked):
        await scheduler.dispatch_due(
            tenant_id="tenant-a", now=NOW + timedelta(minutes=5), release=FakeReleasePort(), limit=2
        )


async def test_cancelling_a_run_removes_its_pending_schedules() -> None:
    journal = FakeRetryJournal()
    retry_journal_row(journal, attempt=1, delay_seconds=60)
    retry_journal_row(journal, attempt=2, delay_seconds=60)
    retry_journal_row(journal, run_id="agr_2", delay_seconds=60)
    scheduler = _scheduler(journal)

    cancelled = await scheduler.cancel_for_run(
        tenant_id="tenant-a",
        run_id="agr_1",
        reason_code="agent_run.retry.quarantined",
        cancelled_at=NOW + timedelta(minutes=1),
    )

    assert cancelled == 2
    assert all(
        row.state is RetryScheduleState.CANCELLED
        for row in journal.rows.values()
        if row.run_id == "agr_1"
    )
    assert next(row for row in journal.rows.values() if row.run_id == "agr_2").state is (
        RetryScheduleState.PENDING
    )


async def test_cancel_reason_must_be_a_redacted_retry_code() -> None:
    journal = FakeRetryJournal()
    retry_journal_row(journal, delay_seconds=60)
    scheduler = _scheduler(journal)

    with pytest.raises(ValueError):
        await scheduler.cancel_for_run(
            tenant_id="tenant-a",
            run_id="agr_1",
            reason_code="operator saw a secret token",
            cancelled_at=NOW,
        )
    assert journal.only_row().state is RetryScheduleState.PENDING


# --------------------------------------------------------------------------- #
# Worker-result entry point re-asserts the execution-port contract
# --------------------------------------------------------------------------- #


def _lease(*, attempt: int = 1, generation: int = 3) -> AgentRunLease:
    return AgentRunLease(
        tenant_id="tenant-a",
        project_id="project-1",
        run_id="agr_1",
        session_id=None,
        owner_id="worker-1",
        lease_token="lease-token-1",
        generation=generation,
        attempt=attempt,
        sequence=7,
        lease_until=NOW + timedelta(minutes=5),
    )


async def test_unknown_worker_result_cannot_be_scheduled() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)
    result = ProviderExecutionResult(
        outcome=ProviderOutcome.UNKNOWN, provider_request_id="req-1"
    )

    with pytest.raises(ValueError):
        await scheduler.schedule_from_result(
            lease=_lease(),
            result=result,
            evidence=AgentRunFailureEvidence(category=AgentRunFailureCategory.UNKNOWN),
            failed_at=NOW,
        )
    assert journal.rows == {}


async def test_retryable_worker_result_honours_the_provider_retry_time() -> None:
    journal = FakeRetryJournal()
    scheduler = _scheduler(journal)
    provider_retry_at = NOW + timedelta(seconds=120)
    result = ProviderExecutionResult(
        outcome=ProviderOutcome.RETRYABLE_FAILURE,
        failure_code="agent_run.error.provider_unavailable",
        provider_request_id="req-1",
        retry_at=provider_retry_at,
    )

    outcome = await scheduler.schedule_from_result(
        lease=_lease(),
        result=result,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=NOW,
    )

    assert outcome.record.run_attempt == 1
    assert outcome.record.run_generation == 3
    assert outcome.record.retry_at == provider_retry_at
