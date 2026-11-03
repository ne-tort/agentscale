"""Unknown provider outcome reconciliation: no blind retry, no double transition."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from prodavan.application.agent_runs.reconcile import (
    AgentRunReconcileBlocked,
    AgentRunReconcilePolicy,
    AgentRunUnknownOutcomeReconciler,
    CaseDecision,
    CaseResolution,
    ProviderEvidenceSource,
    ProviderObservationState,
    ProviderOutcomeObservation,
    ReconciledRunStatus,
    ReconciliationOutcome,
    RunTerminalTransitionStatus,
    UnknownOutcomeState,
    WIRING_BLOCKER,
    build_unknown_outcome_case,
)
from prodavan.application.agent_runs.retry import TenantScope, TenantScopeViolation

from .acceptance_fixtures import (
    DIGEST_A,
    DIGEST_B,
    LEASE,
    NOW,
    FakeProbe,
    FakeTransitions,
    FakeUnknownJournal,
    failed_observation,
    not_applied_observation,
    still_unknown_observation,
    succeeded_observation,
    unknown_case,
)

LATER = NOW + timedelta(minutes=10)


def _reconciler(
    journal: FakeUnknownJournal | None,
    probe: FakeProbe | None,
    transitions: FakeTransitions | None,
    *,
    policy: AgentRunReconcilePolicy | None = None,
    tenants: tuple[str, ...] = ("tenant-a",),
) -> AgentRunUnknownOutcomeReconciler:
    return AgentRunUnknownOutcomeReconciler(
        journal=journal,
        probe=probe,
        transitions=transitions,
        scope=TenantScope(frozenset(tenants)),
        policy=policy or AgentRunReconcilePolicy(initial_backoff_seconds=30, max_backoff_seconds=120),
        claim_lease=LEASE,
    )


def _seeded_case(journal: FakeUnknownJournal, **kwargs):
    case = unknown_case(**kwargs)
    journal.cases[case.reconcile_key] = case
    return case


# --------------------------------------------------------------------------- #
# Conclusive outcomes
# --------------------------------------------------------------------------- #


async def test_proven_success_moves_the_run_to_succeeded_with_evidence() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "running"
    reconciler = _reconciler(journal, FakeProbe([succeeded_observation()]), transitions)

    result = await reconciler.reconcile_case(
        tenant_id="tenant-a",
        run_id=case.run_id,
        generation=case.run_generation,
        attempt=case.run_attempt,
        now=LATER,
    )

    assert result.outcome is ReconciliationOutcome.SUCCEEDED
    assert len(transitions.commands) == 1
    command = transitions.commands[0]
    assert command.status is ReconciledRunStatus.SUCCEEDED
    assert command.evidence_id == "provider-attempt-1"
    assert command.evidence_digest == DIGEST_A
    assert command.output_digest == DIGEST_B
    assert command.failure_code is None
    assert command.run_generation == case.run_generation
    assert journal.cases[case.reconcile_key].state is UnknownOutcomeState.RESOLVED
    assert len(journal.audit) == 1
    audit = journal.audit[0]
    assert audit.decision is CaseDecision.SUCCEEDED
    assert audit.resolution is CaseResolution.SUCCEEDED
    payload = audit.redacted
    assert set(payload) == {
        "audit_id",
        "tenant_id",
        "project_id",
        "run_id",
        "generation",
        "attempt",
        "decision",
        "resolution",
        "reason_code",
        "evidence_id",
        "evidence_digest",
        "recorded_at",
    }


async def test_proven_failure_moves_the_run_to_failed_with_a_redacted_code() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "running"
    reconciler = _reconciler(journal, FakeProbe([failed_observation()]), transitions)

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert (result.claimed, result.failed) == (1, 1)
    command = transitions.commands[0]
    assert command.status is ReconciledRunStatus.FAILED
    assert command.failure_code == "agent_run.error.provider_unavailable"
    assert command.output_digest is None
    assert journal.cases[case.reconcile_key].resolved_status is CaseResolution.FAILED


async def test_not_applied_is_retry_eligible_and_never_transitions_the_run() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(journal, FakeProbe([not_applied_observation()]), transitions)

    result = await reconciler.reconcile_case(
        tenant_id="tenant-a",
        run_id=case.run_id,
        generation=case.run_generation,
        attempt=case.run_attempt,
        now=LATER,
    )

    assert result.outcome is ReconciliationOutcome.RETRY_ELIGIBLE
    assert transitions.commands == []
    stored = journal.cases[case.reconcile_key]
    assert stored.resolved_status is CaseResolution.RETRY_ELIGIBLE
    assert stored.decided_status is CaseDecision.NOT_APPLIED
    # The retry scheduler — not the reconciler — owns the follow-up schedule.
    assert journal.audit[0].decision is CaseDecision.NOT_APPLIED


# --------------------------------------------------------------------------- #
# Repeat reconciliation must not duplicate a terminal transition
# --------------------------------------------------------------------------- #


async def test_second_reconcile_of_a_resolved_case_creates_no_second_transition() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "running"
    probe = FakeProbe([succeeded_observation(number=1), succeeded_observation(number=2)])
    reconciler = _reconciler(journal, probe, transitions)

    first = await reconciler.reconcile_case(
        tenant_id="tenant-a",
        run_id=case.run_id,
        generation=case.run_generation,
        attempt=case.run_attempt,
        now=LATER,
    )
    second = await reconciler.reconcile_case(
        tenant_id="tenant-a",
        run_id=case.run_id,
        generation=case.run_generation,
        attempt=case.run_attempt,
        now=LATER + timedelta(minutes=5),
    )

    assert first.outcome is ReconciliationOutcome.SUCCEEDED
    assert second.outcome is ReconciliationOutcome.ALREADY_RESOLVED
    assert len(transitions.commands) == 1
    assert probe.calls == 1
    assert len(journal.audit) == 1


async def test_due_scan_never_reclaims_a_resolved_or_quarantined_case() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "running"
    reconciler = _reconciler(journal, FakeProbe([succeeded_observation()]), transitions)

    await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)
    later_scan = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER + timedelta(hours=2))

    assert later_scan.claimed == 0
    assert len(transitions.commands) == 1


async def test_crash_between_decision_and_transition_resumes_with_the_same_command() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "running"
    probe = FakeProbe([failed_observation()])

    first = await _reconciler(journal, probe, transitions).reconcile_due(
        tenant_id="tenant-a", now=LATER
    )
    assert first.failed == 1
    decided_key = journal.cases[case.reconcile_key].transition_key
    assert decided_key is not None

    # Simulate a peer that rebooted the case from its durable decision.
    durable = journal.cases[case.reconcile_key]
    resumed = _seeded_case(
        journal,
        run_id=durable.run_id,
        generation=durable.run_generation,
        attempt=durable.run_attempt,
    )
    from dataclasses import replace

    journal.cases[resumed.reconcile_key] = replace(
        durable,
        state=UnknownOutcomeState.DECIDED,
        resolved_at=None,
        resolved_status=None,
        claim_token=None,
        lease_until=None,
    )
    transitions.run_status[case.run_id] = "running"

    second = await _reconciler(journal, FakeProbe(default=None), transitions).reconcile_due(
        tenant_id="tenant-a", now=LATER + timedelta(minutes=1)
    )

    assert second.claimed == 1
    assert second.failed == 1
    assert len(transitions.commands) == 2
    assert transitions.commands[0].command_idempotency_key == decided_key
    assert transitions.commands[1].command_idempotency_key == decided_key
    assert len(transitions.applied) == 1


async def test_run_already_terminal_supersedes_the_case_without_a_second_status() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status[case.run_id] = "cancelled"
    reconciler = _reconciler(journal, FakeProbe([succeeded_observation()]), transitions)

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.superseded == 1
    assert result.succeeded == 0
    stored = journal.cases[case.reconcile_key]
    assert stored.state is UnknownOutcomeState.RESOLVED
    assert stored.resolved_status is CaseResolution.SUPERSEDED
    assert transitions.run_status[case.run_id] == "cancelled"


# --------------------------------------------------------------------------- #
# Inconclusive / unavailable / contradicting sources
# --------------------------------------------------------------------------- #


async def test_inconclusive_probe_defers_with_backoff_and_quarantines_after_the_cap() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(
        journal,
        FakeProbe(default=still_unknown_observation()),
        transitions,
        policy=AgentRunReconcilePolicy(
            max_reconcile_attempts=1, initial_backoff_seconds=30, max_backoff_seconds=60,
            jitter_basis_points=0,
        ),
    )

    first = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)
    assert first.deferred == 1
    stored = journal.cases[case.reconcile_key]
    assert stored.state is UnknownOutcomeState.PENDING
    assert stored.next_reconcile_at == LATER + timedelta(seconds=30)

    second = await reconciler.reconcile_due(tenant_id="tenant-a", now=stored.next_reconcile_at)
    assert second.quarantined == 1
    quarantined = journal.cases[case.reconcile_key]
    assert quarantined.state is UnknownOutcomeState.QUARANTINED
    assert quarantined.reason_code == "agent_run.reconcile.attempts_exhausted"
    assert transitions.commands == []

    third = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER + timedelta(days=1))
    assert third.claimed == 0


async def test_probe_error_is_treated_as_unresolved_never_as_success() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(journal, FakeProbe([OSError("provider timeout")]), transitions)

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.deferred == 1
    assert transitions.commands == []
    assert journal.cases[case.reconcile_key].state is UnknownOutcomeState.PENDING


async def test_missing_source_record_is_not_a_resolution() -> None:
    journal = FakeUnknownJournal()
    _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(journal, FakeProbe([None]), transitions)

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.deferred == 1
    assert transitions.commands == []


async def test_contradicting_evidence_quarantines_instead_of_choosing_a_side() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    from dataclasses import replace

    journal.cases[case.reconcile_key] = replace(
        case, evidence_id="provider-attempt-1", evidence_digest=DIGEST_A
    )
    transitions = FakeTransitions()
    reconciler = _reconciler(
        journal,
        FakeProbe(
            [
                ProviderOutcomeObservation(
                    state=ProviderObservationState.SUCCEEDED,
                    source=ProviderEvidenceSource.PROVIDER_STATUS,
                    observed_at=LATER,
                    evidence_id="provider-attempt-2",
                    evidence_digest=DIGEST_B,
                )
            ]
        ),
        transitions,
    )

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.quarantined == 1
    stored = journal.cases[case.reconcile_key]
    assert stored.reason_code == "agent_run.reconcile.evidence_conflict"
    assert transitions.commands == []


async def test_future_dated_evidence_is_rejected_as_unusable() -> None:
    journal = FakeUnknownJournal()
    _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(
        journal, FakeProbe([succeeded_observation(at=LATER + timedelta(days=1))]), transitions
    )

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.deferred == 1
    assert transitions.commands == []


async def test_resolution_deadline_quarantines_an_unresolved_case() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    reconciler = _reconciler(journal, FakeProbe(default=still_unknown_observation()), transitions)

    result = await reconciler.reconcile_due(
        tenant_id="tenant-a", now=LATER, resolution_deadline=LATER
    )

    assert result.quarantined == 1
    assert journal.cases[case.reconcile_key].reason_code == (
        "agent_run.reconcile.deadline_exceeded"
    )


# --------------------------------------------------------------------------- #
# Fail-closed wiring, races, and tenant isolation
# --------------------------------------------------------------------------- #


async def test_blocked_transition_raises_and_leaves_the_case_resumable() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.blocked = True
    reconciler = _reconciler(journal, FakeProbe([succeeded_observation()]), transitions)

    with pytest.raises(AgentRunReconcileBlocked):
        await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    stored = journal.cases[case.reconcile_key]
    assert stored.state is UnknownOutcomeState.DECIDED
    assert stored.resolved_at is None


async def test_lost_resolution_race_is_reported_not_claimed_as_success() -> None:
    journal = FakeUnknownJournal()
    _seeded_case(journal)
    transitions = FakeTransitions()
    transitions.run_status["agr_1"] = "running"
    journal.lose_resolution_race = True
    reconciler = _reconciler(journal, FakeProbe([succeeded_observation()]), transitions)

    result = await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)

    assert result.lost_race == 1
    assert result.succeeded == 0
    assert journal.audit == []


async def test_unwired_reconciler_fails_closed() -> None:
    for journal, probe, transitions in (
        (None, FakeProbe(), FakeTransitions()),
        (FakeUnknownJournal(), None, FakeTransitions()),
        (FakeUnknownJournal(), FakeProbe(), None),
    ):
        reconciler = _reconciler(journal, probe, transitions)
        assert reconciler.is_wired is False
        assert reconciler.wiring_blocker == WIRING_BLOCKER
        with pytest.raises(AgentRunReconcileBlocked) as blocked:
            await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)
        assert blocked.value.blocker == WIRING_BLOCKER


async def test_foreign_tenant_cannot_scan_or_address_a_case() -> None:
    journal = FakeUnknownJournal()
    case = _seeded_case(journal, tenant_id="tenant-b", run_id="agr_b")
    transitions = FakeTransitions()
    probe = FakeProbe(default=succeeded_observation())
    reconciler = _reconciler(journal, probe, transitions, tenants=("tenant-a",))

    with pytest.raises(TenantScopeViolation):
        await reconciler.reconcile_due(tenant_id="tenant-b", now=LATER)
    assert probe.calls == 0
    assert journal.cases[case.reconcile_key].state is UnknownOutcomeState.PENDING


async def test_journal_returning_a_foreign_case_aborts_the_pass() -> None:
    journal = FakeUnknownJournal()
    _seeded_case(journal, tenant_id="tenant-a", run_id="agr_a")
    _seeded_case(journal, tenant_id="tenant-b", run_id="agr_b")
    transitions = FakeTransitions()
    probe = FakeProbe(default=succeeded_observation())
    reconciler = _reconciler(journal, probe, transitions, tenants=("tenant-a", "tenant-b"))

    original = journal.claim_due

    async def leaking(*, tenant_id, now, lease_until, limit):
        claims = await original(tenant_id=tenant_id, now=now, lease_until=lease_until, limit=limit)
        foreign = await original(
            tenant_id="tenant-b", now=now, lease_until=lease_until, limit=limit
        )
        return tuple(claims) + tuple(foreign)

    journal.claim_due = leaking  # type: ignore[assignment]

    with pytest.raises(TenantScopeViolation):
        await reconciler.reconcile_due(tenant_id="tenant-a", now=LATER)
    assert transitions.commands == []


async def test_unknown_case_registration_is_idempotent_per_attempt() -> None:
    journal = FakeUnknownJournal()
    transitions = FakeTransitions()
    reconciler = _reconciler(journal, FakeProbe(), transitions)

    first = await reconciler.register_case(case=unknown_case())
    second = await reconciler.register_case(case=unknown_case())

    assert first.reconcile_id == second.reconcile_id
    assert len(journal.cases) == 1


def test_records_reject_incoherent_states() -> None:
    with pytest.raises(ValueError):
        build_unknown_outcome_case(
            tenant_id="tenant-a",
            project_id="project-1",
            run_id="agr_1",
            generation=0,
            attempt=1,
            observed_at=NOW,
        )
    with pytest.raises(ValueError):
        ProviderOutcomeObservation(
            state=ProviderObservationState.SUCCEEDED,
            source=ProviderEvidenceSource.ATTEMPT_JOURNAL,
            observed_at=datetime(2026, 11, 4, 12, 0),  # naive
            evidence_id="provider-attempt-1",
            evidence_digest=DIGEST_A,
        )
    with pytest.raises(ValueError):
        ProviderOutcomeObservation(
            state=ProviderObservationState.STILL_UNKNOWN,
            source=ProviderEvidenceSource.ATTEMPT_JOURNAL,
            observed_at=NOW,
            evidence_id="provider-attempt-1",
            evidence_digest=DIGEST_A,
            failure_code="agent_run.error.transient",
        )
    with pytest.raises(ValueError):
        # Proof of a failure must be a bounded redacted code, not free text.
        ProviderOutcomeObservation(
            state=ProviderObservationState.FAILED,
            source=ProviderEvidenceSource.ATTEMPT_JOURNAL,
            observed_at=NOW,
            evidence_id="provider-attempt-1",
            evidence_digest=DIGEST_A,
            failure_code="boom: secret=abc",
        )


def test_reconcile_policy_bounds_and_determinism() -> None:
    with pytest.raises(ValueError):
        AgentRunReconcilePolicy(max_reconcile_attempts=0)
    with pytest.raises(ValueError):
        AgentRunReconcilePolicy(initial_backoff_seconds=100, max_backoff_seconds=10)

    policy = AgentRunReconcilePolicy(
        initial_backoff_seconds=30, max_backoff_seconds=30, jitter_basis_points=0
    )
    case = unknown_case()
    assert policy.next_probe_at(case=case, now=NOW) == NOW + timedelta(seconds=30)
    assert policy.next_probe_at(case=case, now=NOW) == policy.next_probe_at(case=case, now=NOW)
