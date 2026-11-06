"""Deterministic in-memory doubles for the retry/reconciler contracts.

These doubles mimic the *documented* durability semantics of the real SQL
adapters (unique schedule key, ``SKIP LOCKED``-style due selection, lease
generation CAS) so the acceptance vectors exercise the application code without
a database.  They are test-only: no production path imports this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from prodavan.application.agent_runs.reconcile import (
    CaseDecision,
    CaseResolution,
    ProviderEvidenceSource,
    ProviderObservationState,
    ProviderOutcomeObservation,
    ReconciliationAuditEntry,
    ReconciliationWriteResult,
    RunTerminalTransitionStatus,
    UnknownOutcomeRecord,
    UnknownOutcomeState,
    build_unknown_outcome_case,
)
from prodavan.application.agent_runs.retry import (
    AgentRunFailureCategory,
    AgentRunFailureEvidence,
    RetryReleaseDecision,
    RetryScheduleRecord,
    RetryScheduleState,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def evidence_id(number: int) -> str:
    return f"provider-attempt-{number}"


# --------------------------------------------------------------------------- #
# Retry journal double
# --------------------------------------------------------------------------- #


class FakeRetryJournal:
    """In-memory ``AgentRunRetryJournalPort`` with uniqueness and lease CAS."""

    def __init__(self) -> None:
        self.rows: dict[str, RetryScheduleRecord] = {}
        self.insert_calls = 0
        self.claim_calls: list[tuple[str, int]] = []
        self.release_calls = 0
        self.cancel_calls = 0
        self.crash_after_claim = False
        self.due_claim_limit_override: int | None = None

    # -- port ----------------------------------------------------------- #

    async def insert_schedule_once(
        self, *, record: RetryScheduleRecord
    ) -> tuple[RetryScheduleRecord, bool]:
        from prodavan.application.agent_runs.retry import AgentRunRetryScheduleConflict

        self.insert_calls += 1
        stored = self.rows.get(record.schedule_key)
        if stored is not None:
            if stored.schedule_digest != record.schedule_digest:
                raise AgentRunRetryScheduleConflict("changed retry decision for one attempt")
            return stored, False
        self.rows[record.schedule_key] = record
        return record, True

    async def claim_due(
        self, *, tenant_id: str, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[object, ...]:
        assert lease_until > now
        self.claim_calls.append((tenant_id, limit))
        if self.due_claim_limit_override is not None:
            limit = self.due_claim_limit_override
        due = [
            row
            for row in self.rows.values()
            if row.tenant_id == tenant_id
            and (
                (row.state is RetryScheduleState.PENDING and row.retry_at <= now)
                or (
                    row.state is RetryScheduleState.CLAIMED
                    and row.lease_until is not None
                    and row.lease_until <= now
                )
            )
        ]
        due.sort(key=lambda row: (row.retry_at, row.schedule_id))
        claimed = []
        for row in due[:limit]:
            claimed.append(self._claim(row, now=now, lease_until=lease_until))
        if self.crash_after_claim:
            # Simulates the process dying between the claim and the follow-up write.
            self.crash_after_claim = False
            raise RuntimeError("worker crashed between claim and release")
        return tuple(claimed)

    def _claim(self, row: RetryScheduleRecord, *, now: datetime, lease_until: datetime):
        from prodavan.application.agent_runs.retry import RetryScheduleClaim

        token = uuid.uuid4().hex
        generation = row.claim_generation + 1
        claimed = replace(
            row,
            state=RetryScheduleState.CLAIMED,
            claim_token=token,
            claim_generation=generation,
            lease_until=lease_until,
            updated_at=now,
        )
        self.rows[row.schedule_key] = claimed
        return RetryScheduleClaim(
            record=claimed,
            claim_token=token,
            claim_generation=generation,
            lease_until=lease_until,
            claimed_at=now,
        )

    async def record_release(self, *, claim, released_at: datetime) -> bool:
        self.release_calls += 1
        row = self.rows.get(claim.record.schedule_key)
        if row is None or row.state is not RetryScheduleState.CLAIMED:
            return False
        if row.claim_token != claim.claim_token or row.claim_generation != claim.claim_generation:
            return False
        self.rows[row.schedule_key] = replace(
            row,
            state=RetryScheduleState.RELEASED,
            released_at=released_at,
            claim_token=None,
            lease_until=None,
            updated_at=released_at,
        )
        return True

    async def record_cancel(self, *, claim, reason_code: str, cancelled_at: datetime) -> bool:
        self.cancel_calls += 1
        row = self.rows.get(claim.record.schedule_key)
        if row is None or row.state is not RetryScheduleState.CLAIMED:
            return False
        if row.claim_token != claim.claim_token or row.claim_generation != claim.claim_generation:
            return False
        self.rows[row.schedule_key] = replace(
            row,
            state=RetryScheduleState.CANCELLED,
            cancelled_reason=reason_code,
            claim_token=None,
            lease_until=None,
            updated_at=cancelled_at,
        )
        return True

    async def cancel_pending_for_run(
        self, *, tenant_id: str, run_id: str, reason_code: str, cancelled_at: datetime
    ) -> int:
        cancelled = 0
        for key, row in list(self.rows.items()):
            if (
                row.tenant_id == tenant_id
                and row.run_id == run_id
                and row.state is RetryScheduleState.PENDING
            ):
                self.rows[key] = replace(
                    row,
                    state=RetryScheduleState.CANCELLED,
                    cancelled_reason=reason_code,
                    updated_at=cancelled_at,
                )
                cancelled += 1
        return cancelled

    # -- assertions helpers --------------------------------------------- #

    def states(self) -> dict[str, RetryScheduleState]:
        return {row.run_attempt: row.state for row in self.rows.values()}

    def only_row(self) -> RetryScheduleRecord:
        assert len(self.rows) == 1
        return next(iter(self.rows.values()))


class FakeReleasePort:
    """``RetryReleasePort`` double with a configurable per-claim decision."""

    def __init__(self, decision: RetryReleaseDecision = RetryReleaseDecision.RELEASED) -> None:
        self.decision = decision
        self.calls: list[str] = []

    async def release(self, *, claim, now: datetime) -> RetryReleaseDecision:
        self.calls.append(claim.record.schedule_key)
        return self.decision


# --------------------------------------------------------------------------- #
# Unknown-outcome journal double
# --------------------------------------------------------------------------- #


class FakeUnknownJournal:
    """In-memory ``UnknownOutcomeJournalPort`` with lease CAS and audit rows."""

    def __init__(self) -> None:
        self.cases: dict[str, UnknownOutcomeRecord] = {}
        self.audit: list[ReconciliationAuditEntry] = []
        self.decision_calls = 0
        self.resolution_calls = 0
        self.deferral_calls = 0
        self.lose_resolution_race = False
        self.crash_between_decision_and_transition = False

    async def register_unknown(
        self, *, record: UnknownOutcomeRecord
    ) -> tuple[UnknownOutcomeRecord, bool]:
        existing = self.cases.get(record.reconcile_key)
        if existing is not None:
            return existing, False
        self.cases[record.reconcile_key] = record
        return record, True

    async def claim_due(
        self, *, tenant_id: str, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[object, ...]:
        from prodavan.application.agent_runs.reconcile import UnknownOutcomeClaim

        due = [
            case
            for case in self.cases.values()
            if case.tenant_id == tenant_id
            and (
                (case.state is UnknownOutcomeState.PENDING and case.next_reconcile_at is not None
                 and case.next_reconcile_at <= now)
                or (case.state is UnknownOutcomeState.PENDING and case.next_reconcile_at is None)
                or (
                    case.state is UnknownOutcomeState.CLAIMED
                    and case.lease_until is not None
                    and case.lease_until <= now
                )
                or case.state is UnknownOutcomeState.DECIDED
            )
        ]
        due.sort(key=lambda case: (case.next_reconcile_at or case.created_at, case.reconcile_id))
        claimed = []
        for case in due[:limit]:
            token = uuid.uuid4().hex
            generation = case.claim_generation + 1
            next_state = (
                UnknownOutcomeState.CLAIMED
                if case.state is UnknownOutcomeState.PENDING
                else case.state
            )
            claimed_case = replace(
                case,
                state=next_state,
                claim_token=token,
                claim_generation=generation,
                lease_until=lease_until,
                updated_at=now,
            )
            self.cases[case.reconcile_key] = claimed_case
            claimed.append(
                UnknownOutcomeClaim(
                    record=claimed_case,
                    claim_token=token,
                    claim_generation=generation,
                    lease_until=lease_until,
                    claimed_at=now,
                )
            )
        return tuple(claimed)

    async def load_case(
        self, *, tenant_id: str, run_id: str, generation: int, attempt: int
    ) -> UnknownOutcomeRecord | None:
        for case in self.cases.values():
            if (
                case.tenant_id == tenant_id
                and case.run_id == run_id
                and case.run_generation == generation
                and case.run_attempt == attempt
            ):
                return case
        return None

    async def record_decision(
        self,
        *,
        claim,
        decision: CaseDecision,
        resolution_digest: str,
        transition_key: str,
        observation: ProviderOutcomeObservation,
        attempted_at: datetime,
    ) -> bool:
        self.decision_calls += 1
        case = self.cases.get(claim.record.reconcile_key)
        if case is None or case.claim_token != claim.claim_token:
            return False
        self.cases[case.reconcile_key] = replace(
            case,
            state=UnknownOutcomeState.DECIDED,
            decided_status=decision,
            resolution_digest=resolution_digest,
            transition_key=transition_key,
            decided_at=attempted_at,
            reconcile_attempts=case.reconcile_attempts + 1,
            evidence_id=observation.evidence_id,
            evidence_digest=observation.evidence_digest,
            output_digest=observation.output_digest,
            reason_code=observation.failure_code,
            next_reconcile_at=None,
            lease_until=claim.lease_until,
            updated_at=attempted_at,
        )
        return True

    async def record_resolution(
        self,
        *,
        claim,
        resolution: CaseResolution,
        reason_code: str,
        resolved_at: datetime,
    ) -> ReconciliationWriteResult:
        self.resolution_calls += 1
        if self.lose_resolution_race:
            return ReconciliationWriteResult(applied=False)
        case = self.cases.get(claim.record.reconcile_key)
        if case is None or case.claim_token != claim.claim_token:
            return ReconciliationWriteResult(applied=False)
        self.cases[case.reconcile_key] = replace(
            case,
            state=UnknownOutcomeState.RESOLVED,
            resolved_status=resolution,
            reason_code=reason_code,
            resolved_at=resolved_at,
            claim_token=None,
            lease_until=None,
            updated_at=resolved_at,
        )
        entry = ReconciliationAuditEntry(
            audit_id=f"ara_{uuid.uuid4().hex[:16]}",
            tenant_id=case.tenant_id,
            project_id=case.project_id,
            run_id=case.run_id,
            generation=case.run_generation,
            attempt=case.run_attempt,
            decision=case.decided_status,
            resolution=resolution,
            reason_code=reason_code,
            evidence_id=case.evidence_id,
            evidence_digest=case.evidence_digest,
            recorded_at=resolved_at,
        )
        self.audit.append(entry)
        return ReconciliationWriteResult(applied=True, audit=entry)

    async def record_deferral(
        self,
        *,
        claim,
        next_reconcile_at: datetime | None,
        reason_code: str,
        attempted_at: datetime,
    ) -> bool:
        self.deferral_calls += 1
        case = self.cases.get(claim.record.reconcile_key)
        if case is None or case.claim_token != claim.claim_token:
            return False
        if next_reconcile_at is None:
            self.cases[case.reconcile_key] = replace(
                case,
                state=UnknownOutcomeState.QUARANTINED,
                reason_code=reason_code,
                resolved_at=attempted_at,
                claim_token=None,
                lease_until=None,
                updated_at=attempted_at,
            )
            return True
        self.cases[case.reconcile_key] = replace(
            case,
            state=UnknownOutcomeState.PENDING,
            next_reconcile_at=next_reconcile_at,
            reconcile_attempts=case.reconcile_attempts + 1,
            reason_code=None,
            claim_token=None,
            lease_until=None,
            updated_at=attempted_at,
        )
        return True


class FakeProbe:
    """Scripted ``ProviderOutcomeProbePort`` double."""

    def __init__(self, script: list[object] | None = None, *, default=None) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls = 0

    async def probe(self, *, case: UnknownOutcomeRecord):
        self.calls += 1
        if self.script:
            item = self.script.pop(0)
        else:
            item = self.default
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(case)
        return item


class FakeTransitions:
    """``RunTerminalTransitionPort`` double with command-level idempotency."""

    def __init__(self) -> None:
        self.commands: list[object] = []
        self.applied: dict[str, object] = {}
        self.run_status: dict[str, str] = {}
        self.blocked = False

    async def apply(self, *, transition):
        from prodavan.application.agent_runs.reconcile import RunTerminalTransitionResult

        self.commands.append(transition)
        if self.blocked:
            from prodavan.application.agent_runs.reconcile import RECONCILE_REASON_UNKNOWN

            return RunTerminalTransitionResult(
                status=RunTerminalTransitionStatus.BLOCKED, reason_code=RECONCILE_REASON_UNKNOWN
            )
        key = transition.command_idempotency_key
        if key in self.applied:
            return RunTerminalTransitionResult(status=RunTerminalTransitionStatus.DUPLICATE)
        current = self.run_status.get(transition.run_id)
        if current is not None and current != "running":
            from prodavan.application.agent_runs.reconcile import RECONCILE_REASON_RUN_TERMINAL

            return RunTerminalTransitionResult(
                status=RunTerminalTransitionStatus.CONFLICT, reason_code=RECONCILE_REASON_RUN_TERMINAL
            )
        self.commands_applied = getattr(self, "commands_applied", 0) + 1
        self.applied[key] = transition
        self.run_status[transition.run_id] = transition.status.value
        return RunTerminalTransitionResult(status=RunTerminalTransitionStatus.APPLIED)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def retry_journal_row(
    journal: FakeRetryJournal,
    *,
    run_id: str = "agr_1",
    tenant_id: str = "tenant-a",
    project_id: str = "project-1",
    generation: int = 1,
    attempt: int = 1,
    delay_seconds: int = 10,
    failed_at: datetime = NOW,
) -> RetryScheduleRecord:
    from prodavan.application.agent_runs.retry import (
        AgentRunRetryPolicy,
        RetryScheduleIdentity,
        build_retry_schedule_record,
    )

    identity = RetryScheduleIdentity(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        generation=generation,
        attempt=attempt,
    )
    policy = AgentRunRetryPolicy(
        initial_delay_seconds=delay_seconds,
        max_delay_seconds=delay_seconds,
        jitter_basis_points=0,
    )
    plan = policy.plan(
        identity=identity,
        evidence=AgentRunFailureEvidence(
            category=AgentRunFailureCategory.PROVIDER_UNAVAILABLE, http_status=503
        ),
        failed_at=failed_at,
    )
    record = build_retry_schedule_record(identity=identity, plan=plan, failed_at=failed_at)
    journal.rows[record.schedule_key] = record
    return record


def unknown_case(
    *,
    run_id: str = "agr_1",
    tenant_id: str = "tenant-a",
    project_id: str = "project-1",
    generation: int = 2,
    attempt: int = 1,
    observed_at: datetime = NOW,
    provider_request_id: str | None = "req-1",
) -> UnknownOutcomeRecord:
    return build_unknown_outcome_case(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        generation=generation,
        attempt=attempt,
        observed_at=observed_at,
        provider_request_id=provider_request_id,
    )


def succeeded_observation(*, at: datetime = NOW, number: int = 1) -> ProviderOutcomeObservation:
    return ProviderOutcomeObservation(
        state=ProviderObservationState.SUCCEEDED,
        source=ProviderEvidenceSource.ATTEMPT_JOURNAL,
        observed_at=at,
        evidence_id=evidence_id(number),
        evidence_digest=DIGEST_A,
        output_digest=DIGEST_B,
    )


def failed_observation(*, at: datetime = NOW, number: int = 1) -> ProviderOutcomeObservation:
    return ProviderOutcomeObservation(
        state=ProviderObservationState.FAILED,
        source=ProviderEvidenceSource.PROVIDER_STATUS,
        observed_at=at,
        evidence_id=evidence_id(number),
        evidence_digest=DIGEST_A,
        failure_code="agent_run.error.provider_unavailable",
    )


def not_applied_observation(*, at: datetime = NOW, number: int = 1) -> ProviderOutcomeObservation:
    return ProviderOutcomeObservation(
        state=ProviderObservationState.NOT_APPLIED,
        source=ProviderEvidenceSource.IDEMPOTENT_LOOKUP,
        observed_at=at,
        evidence_id=evidence_id(number),
        evidence_digest=DIGEST_A,
        failure_code="agent_run.error.transient",
    )


def still_unknown_observation(*, at: datetime = NOW, number: int = 1) -> ProviderOutcomeObservation:
    return ProviderOutcomeObservation(
        state=ProviderObservationState.STILL_UNKNOWN,
        source=ProviderEvidenceSource.PROVIDER_STATUS,
        observed_at=at,
        evidence_id=evidence_id(number),
        evidence_digest=DIGEST_A,
    )


__all__ = [
    "DIGEST_A",
    "DIGEST_B",
    "FakeProbe",
    "FakeReleasePort",
    "FakeRetryJournal",
    "FakeTransitions",
    "FakeUnknownJournal",
    "LEASE",
    "NOW",
    "evidence_id",
    "failed_observation",
    "not_applied_observation",
    "retry_journal_row",
    "still_unknown_observation",
    "succeeded_observation",
    "unknown_case",
]
