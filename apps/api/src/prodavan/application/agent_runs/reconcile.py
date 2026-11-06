"""Reconciliation of Agent Runs whose provider outcome is unknown.

Context: ``tests/api/agent_runs/readiness_blockers.md`` blocker №5 — an unknown
provider outcome must be reconciled, never blind-retried
(``agent_run_provider_outcome_reconciliation_unwired``).

The retry scheduler refuses to schedule an ambiguous attempt.  This module is
the only way such a run may leave the ambiguous state, and it has exactly three
exits:

1. an authoritative source proves the attempt **succeeded** -> the run becomes
   ``succeeded`` with the provider evidence attached;
2. an authoritative source proves the attempt **failed** -> the run becomes
   ``failed`` with a redacted failure code;
3. an authoritative source proves the attempt was **never applied** -> the case
   is closed as retry-eligible.  The reconciler does *not* schedule the retry
   itself: the caller hands the proof to
   :class:`~prodavan.application.agent_runs.retry.AgentRunRetryScheduler` with
   ``request_dispatch_observed=False``, which is the only admissible "retry"
   input for a previously ambiguous attempt.

Anything else — no source, an inconclusive source, contradicting evidence, an
expired resolution deadline, a provider that never answers — ends in
**quarantine**.  A quarantined case is never scanned again, so the same
ambiguity can never be re-reconciled or retried in a loop.

Resumability: a decision is persisted *before* the run transition is attempted,
so a crash between them is replayed on the next pass with the *same deterministic
idempotency key*.  The durable run transition replays as a duplicate instead of
producing a second terminal transition, and the case is then closed.  Nothing is
lost and nothing is applied twice.

Tenant isolation: the reconciler is constructed with a server-resolved
:class:`~prodavan.application.agent_runs.retry.TenantScope`; every scan, claim,
read and write is bound to the requested tenant, and every claim returned by the
journal is re-validated before any provider call is made.

Schema ownership: the SQL adapter below addresses the published
``agent_run_unknown_outcomes`` and ``agent_run_reconciliation_audit`` table
contracts through metadata-free ``TableClause`` objects.  It registers no ORM
metadata and emits no DDL; until those tables exist it fails closed with
``agent_run_unknown_outcomes_table_missing``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    Column,
    insert,
    or_,
    select,
    table,
    update,
)

from .retry import (
    TenantScope,
    TenantScopeViolation,
    _aware,
    _canonical_digest,
    _error_code,
    _required,
    _valid_digest,
)

UNKNOWN_OUTCOME_TABLE = "agent_run_unknown_outcomes"
RECONCILIATION_AUDIT_TABLE = "agent_run_reconciliation_audit"

#: Columns the persistence task must materialise.  ``reconcile_key`` uniqueness
#: is what makes one case correspond to exactly one provider attempt;
#: ``(tenant_id, run_id, run_generation, run_attempt)`` is the lookup key.
UNKNOWN_OUTCOME_REQUIRED_COLUMNS = (
    "reconcile_id",
    "reconcile_key",
    "tenant_id",
    "project_id",
    "run_id",
    "run_generation",
    "run_attempt",
    "provider_request_id",
    "state",
    "claim_token",
    "claim_generation",
    "lease_until",
    "reconcile_attempts",
    "next_reconcile_at",
    "decided_status",
    "resolution_digest",
    "transition_key",
    "decided_at",
    "resolved_status",
    "evidence_id",
    "evidence_digest",
    "output_digest",
    "reason_code",
    "resolved_at",
    "created_at",
    "updated_at",
)

RECONCILIATION_AUDIT_REQUIRED_COLUMNS = (
    "audit_id",
    "tenant_id",
    "project_id",
    "run_id",
    "run_generation",
    "run_attempt",
    "decision",
    "resolution",
    "reason_code",
    "evidence_id",
    "evidence_digest",
    "recorded_at",
)

RECONCILE_BLOCKER_JOURNAL = "agent_run_unknown_outcome_journal_unwired"
RECONCILE_BLOCKER_TABLE = "agent_run_unknown_outcomes_table_missing"
RECONCILE_BLOCKERS = {
    "journal": RECONCILE_BLOCKER_JOURNAL,
    "table": RECONCILE_BLOCKER_TABLE,
}
WIRING_BLOCKER = RECONCILE_BLOCKER_JOURNAL

RECONCILE_REASON_UNKNOWN = "agent_run.reconcile.unknown"
RECONCILE_REASON_SOURCE_UNAVAILABLE = "agent_run.reconcile.source_unavailable"
RECONCILE_REASON_ATTEMPTS_EXHAUSTED = "agent_run.reconcile.attempts_exhausted"
RECONCILE_REASON_DEADLINE_EXCEEDED = "agent_run.reconcile.deadline_exceeded"
RECONCILE_REASON_CONFLICTING_EVIDENCE = "agent_run.reconcile.evidence_conflict"
RECONCILE_REASON_RUN_TERMINAL = "agent_run.reconcile.run_terminal"
RECONCILE_REASON_SETTLED = "agent_run.reconcile.settled"

_REASON_PREFIX = "agent_run.reconcile."

#: Failure code written to the run when a resumed decision settles it as failed.
#:
#: Two vocabularies meet here and they are not interchangeable: ``reason_code``
#: explains *why reconciliation decided* (``agent_run.reconcile.*``), while
#: ``failure_code`` describes *what happened to the run* and must be a redacted
#: ``agent_run.error.*`` code. Reusing the reason code as the failure code makes
#: the transition validator reject the command, so a resumed failure could never
#: be applied at all.
RECONCILE_FAILURE_CODE = "agent_run.error.reconciled_failed"


def _reason_code(value: str) -> str:
    text = _required(value, "reason_code", max_length=128)
    if not text.startswith(_REASON_PREFIX):
        raise ValueError("reason_code must be a redacted agent_run.reconcile code")
    return text


class AgentRunReconcileError(RuntimeError):
    """Base class for reconciliation failures."""


class AgentRunReconcileBlocked(AgentRunReconcileError):
    """The reconciliation boundary is unwired or the adapter cannot be used."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        super().__init__(detail)


class ReconciledRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProviderObservationState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    #: The provider proves it never applied (and never will apply) the attempt.
    NOT_APPLIED = "not_applied"
    #: The source answered but cannot tell; the case stays open.
    STILL_UNKNOWN = "still_unknown"


class ProviderEvidenceSource(StrEnum):
    """Authoritative sources, in the order the design prefers them."""

    ATTEMPT_JOURNAL = "attempt_journal"
    PROVIDER_STATUS = "provider_status"
    IDEMPOTENT_LOOKUP = "idempotent_lookup"


@dataclass(frozen=True, slots=True)
class ProviderOutcomeObservation:
    """One authoritative answer about a previously ambiguous attempt.

    Carries an evidence handle and digest only — never a payload, message or
    secret.  The digest is what the run transition records, so a caller must not
    fabricate it.
    """

    state: ProviderObservationState
    source: ProviderEvidenceSource
    observed_at: datetime
    evidence_id: str
    evidence_digest: str
    #: Redacted, bounded failure code; required for ``FAILED``/``NOT_APPLIED``.
    failure_code: str | None = None
    #: Digest of the persisted output, never the output itself; ``SUCCEEDED`` only.
    output_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ProviderObservationState):
            raise ValueError("state must be a ProviderObservationState")
        if not isinstance(self.source, ProviderEvidenceSource):
            raise ValueError("source must be a ProviderEvidenceSource")
        _aware(self.observed_at, "observed_at")
        object.__setattr__(self, "evidence_id", _required(self.evidence_id, "evidence_id", max_length=128))
        _valid_digest(self.evidence_digest, "evidence_digest")
        if self.state is ProviderObservationState.STILL_UNKNOWN:
            if self.failure_code is not None or self.output_digest is not None:
                raise ValueError("an inconclusive observation cannot carry outcome fields")
            return
        if self.state is ProviderObservationState.SUCCEEDED:
            if self.failure_code is not None:
                raise ValueError("a succeeded observation cannot carry a failure code")
            if self.output_digest is not None:
                _valid_digest(self.output_digest, "output_digest")
            return
        if self.failure_code is None:
            raise ValueError("failed or not-applied observations require a redacted failure code")
        _error_code(self.failure_code)
        if self.output_digest is not None:
            raise ValueError("a failed observation cannot carry an output digest")

    @property
    def conclusive(self) -> bool:
        return self.state is not ProviderObservationState.STILL_UNKNOWN


class UnknownOutcomeState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    #: A decision is durable; the run transition may not have been applied yet.
    DECIDED = "decided"
    RESOLVED = "resolved"
    QUARANTINED = "quarantined"

    @property
    def terminal(self) -> bool:
        return self in {UnknownOutcomeState.RESOLVED, UnknownOutcomeState.QUARANTINED}


class CaseDecision(StrEnum):
    """Persisted decision for one unknown case (written before the transition)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_APPLIED = "not_applied"


class CaseResolution(StrEnum):
    """How a case was finally closed."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_ELIGIBLE = "retry_eligible"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class UnknownOutcomeIdentity:
    """Immutable identity of one ambiguous provider attempt."""

    tenant_id: str
    project_id: str
    run_id: str
    generation: int
    attempt: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=40))
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=40))
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")

    @property
    def key(self) -> str:
        return _canonical_digest(
            {
                "kind": "agent_run.unknown_outcome",
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "run_id": self.run_id,
                "generation": self.generation,
                "attempt": self.attempt,
            }
        )


@dataclass(frozen=True, slots=True)
class UnknownOutcomeRecord:
    """One durable unknown-outcome case; mirrors the table contract 1:1."""

    reconcile_id: str
    reconcile_key: str
    tenant_id: str
    project_id: str
    run_id: str
    run_generation: int
    run_attempt: int
    state: UnknownOutcomeState
    created_at: datetime
    updated_at: datetime
    provider_request_id: str | None = None
    claim_token: str | None = None
    claim_generation: int = 0
    lease_until: datetime | None = None
    reconcile_attempts: int = 0
    next_reconcile_at: datetime | None = None
    decided_status: CaseDecision | None = None
    resolution_digest: str | None = None
    transition_key: str | None = None
    decided_at: datetime | None = None
    resolved_status: CaseResolution | None = None
    evidence_id: str | None = None
    evidence_digest: str | None = None
    output_digest: str | None = None
    reason_code: str | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reconcile_id", _required(self.reconcile_id, "reconcile_id", max_length=64))
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=40))
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=40))
        _valid_digest(self.reconcile_key, "reconcile_key")
        if self.reconcile_key != self.identity.key:
            raise ValueError("reconcile_key does not match the case identity")
        if self.run_generation < 1 or self.run_attempt < 1:
            raise ValueError("run_generation and run_attempt must be positive")
        if not isinstance(self.state, UnknownOutcomeState):
            raise ValueError("state must be an UnknownOutcomeState")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.provider_request_id is not None:
            object.__setattr__(
                self,
                "provider_request_id",
                _required(self.provider_request_id, "provider_request_id", max_length=128),
            )
        if self.claim_generation < 0 or self.reconcile_attempts < 0:
            raise ValueError("claim generation and attempt counter cannot be negative")
        active = self.state in {UnknownOutcomeState.CLAIMED, UnknownOutcomeState.DECIDED}
        if active:
            if not self.claim_token or self.lease_until is None or self.claim_generation < 1:
                raise ValueError("an active case requires a claim token, lease and positive generation")
            _aware(self.lease_until, "lease_until")
        elif self.claim_token is not None or self.lease_until is not None:
            raise ValueError("only an active case may carry claim fields")
        if self.next_reconcile_at is not None:
            _aware(self.next_reconcile_at, "next_reconcile_at")
        if self.decided_status is not None:
            if not isinstance(self.decided_status, CaseDecision):
                raise ValueError("decided_status must be a CaseDecision")
            if self.decision_incomplete:
                raise ValueError("a decided case requires a digest, transition key, time and evidence")
            _valid_digest(self.resolution_digest or "", "resolution_digest")
            _required(self.transition_key or "", "transition_key", max_length=128)
            _aware(self.decided_at, "decided_at")  # type: ignore[arg-type]
            _valid_digest(self.evidence_digest or "", "evidence_digest")
            _required(self.evidence_id or "", "evidence_id", max_length=128)
            if self.decided_status is CaseDecision.SUCCEEDED and self.output_digest is not None:
                _valid_digest(self.output_digest, "output_digest")
            if self.decided_status is not CaseDecision.SUCCEEDED and self.output_digest is not None:
                raise ValueError("only a succeeded decision may carry an output digest")
        elif self.transition_key is not None or self.resolution_digest is not None:
            raise ValueError("a case without a decision cannot carry decision fields")
        if self.state is UnknownOutcomeState.DECIDED and self.decided_status is None:
            raise ValueError("a decided case must carry its decision")
        if self.state in {UnknownOutcomeState.RESOLVED, UnknownOutcomeState.QUARANTINED}:
            if self.resolved_at is None:
                raise ValueError("a terminal case requires its resolution time")
            _aware(self.resolved_at, "resolved_at")
        if self.state is UnknownOutcomeState.RESOLVED:
            if self.resolved_status is None:
                raise ValueError("a resolved case requires its resolution")
            if self.evidence_id is None or self.evidence_digest is None:
                raise ValueError("a resolved case requires its provider evidence")
        elif self.resolved_status is not None:
            raise ValueError("only a resolved case may carry a resolution")
        if self.state is UnknownOutcomeState.QUARANTINED:
            if self.reason_code is None:
                raise ValueError("a quarantined case requires a redacted reason")
            _reason_code(self.reason_code)
        elif self.state is UnknownOutcomeState.RESOLVED:
            if self.reason_code is not None:
                _reason_code(self.reason_code)
        elif self.reason_code is not None:
            # Before resolution, a reason is the provider's redacted failure code.
            _error_code(self.reason_code)
        if self.evidence_digest is not None:
            _valid_digest(self.evidence_digest, "evidence_digest")

    @property
    def decision_incomplete(self) -> bool:
        return any(
            part is None
            for part in (
                self.resolution_digest,
                self.transition_key,
                self.decided_at,
                self.evidence_id,
                self.evidence_digest,
            )
        )

    @property
    def identity(self) -> UnknownOutcomeIdentity:
        return UnknownOutcomeIdentity(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            run_id=self.run_id,
            generation=self.run_generation,
            attempt=self.run_attempt,
        )


@dataclass(frozen=True, slots=True)
class UnknownOutcomeClaim:
    """Exclusive lease on one unknown case."""

    record: UnknownOutcomeRecord
    claim_token: str
    claim_generation: int
    lease_until: datetime
    claimed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.record, UnknownOutcomeRecord):
            raise ValueError("record must be an UnknownOutcomeRecord")
        if self.record.state not in {UnknownOutcomeState.CLAIMED, UnknownOutcomeState.DECIDED}:
            raise ValueError("a claim must reference an active case")
        object.__setattr__(self, "claim_token", _required(self.claim_token, "claim_token", max_length=128))
        if self.claim_token != self.record.claim_token:
            raise ValueError("claim token does not match the claimed case")
        if self.claim_generation != self.record.claim_generation or self.claim_generation < 1:
            raise ValueError("claim generation does not match the claimed case")
        _aware(self.claimed_at, "claimed_at")
        _aware(self.lease_until, "lease_until")
        if self.record.lease_until != self.lease_until:
            raise ValueError("claim lease does not match the claimed case")
        if self.lease_until <= self.claimed_at:
            raise ValueError("claim lease must expire after it was taken")

    @property
    def tenant_id(self) -> str:
        return self.record.tenant_id


@dataclass(frozen=True, slots=True)
class ReconciliationAuditEntry:
    """Redacted audit row written in the same transaction as the resolution."""

    audit_id: str
    tenant_id: str
    project_id: str
    run_id: str
    generation: int
    attempt: int
    decision: CaseDecision
    resolution: CaseResolution
    reason_code: str
    evidence_id: str
    evidence_digest: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _required(self.audit_id, "audit_id", max_length=64))
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=40))
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=40))
        if not isinstance(self.decision, CaseDecision) or not isinstance(self.resolution, CaseResolution):
            raise ValueError("audit entry requires a decision and a resolution")
        object.__setattr__(self, "evidence_id", _required(self.evidence_id, "evidence_id", max_length=128))
        _valid_digest(self.evidence_digest, "evidence_digest")
        _reason_code(self.reason_code)
        _aware(self.recorded_at, "recorded_at")
        if self.generation < 1 or self.attempt < 1:
            raise ValueError("generation and attempt must be positive")

    @property
    def redacted(self) -> dict[str, Any]:
        """The only shape allowed to leave the process (no payload, no message)."""
        return {
            "audit_id": self.audit_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "attempt": self.attempt,
            "decision": self.decision.value,
            "resolution": self.resolution.value,
            "reason_code": self.reason_code,
            "evidence_id": self.evidence_id,
            "evidence_digest": self.evidence_digest,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ReconciliationWriteResult:
    applied: bool
    audit: ReconciliationAuditEntry | None = None

    def __post_init__(self) -> None:
        if self.applied and self.audit is None:
            raise ValueError("an applied reconciliation write must return its redacted audit entry")
        if not self.applied and self.audit is not None:
            raise ValueError("a lost race cannot report an audit entry")


class RunTerminalTransitionStatus(StrEnum):
    APPLIED = "applied"
    #: The same deterministic transition key had already been committed.
    DUPLICATE = "duplicate"
    #: The run is no longer in a state that admits this transition.
    CONFLICT = "conflict"
    #: The durable write could not be attempted at all.
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RunTerminalTransitionResult:
    status: RunTerminalTransitionStatus
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, RunTerminalTransitionStatus):
            raise ValueError("status must be a RunTerminalTransitionStatus")
        if self.reason_code is not None:
            _reason_code(self.reason_code)
        if self.status in {RunTerminalTransitionStatus.CONFLICT, RunTerminalTransitionStatus.BLOCKED}:
            if self.reason_code is None:
                raise ValueError("a non-applied transition must explain itself with a redacted code")
        elif self.reason_code is not None:
            raise ValueError("an applied or replayed transition cannot carry a failure reason")

    @property
    def settled(self) -> bool:
        return self.status in {RunTerminalTransitionStatus.APPLIED, RunTerminalTransitionStatus.DUPLICATE}


@dataclass(frozen=True, slots=True)
class RunTerminalTransition:
    """One generation-fenced terminal transition requested by reconciliation.

    ``command_idempotency_key`` is derived deterministically from the case, so a
    replay after a crash cannot create a second terminal transition.
    """

    tenant_id: str
    project_id: str
    run_id: str
    run_generation: int
    run_attempt: int
    status: ReconciledRunStatus
    command_idempotency_key: str
    evidence_id: str
    evidence_digest: str
    reconciled_at: datetime
    failure_code: str | None = None
    output_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=40))
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=40))
        if self.run_generation < 1 or self.run_attempt < 1:
            raise ValueError("run_generation and run_attempt must be positive")
        if not isinstance(self.status, ReconciledRunStatus):
            raise ValueError("status must be a ReconciledRunStatus")
        object.__setattr__(
            self,
            "command_idempotency_key",
            _required(self.command_idempotency_key, "command_idempotency_key", max_length=512),
        )
        object.__setattr__(self, "evidence_id", _required(self.evidence_id, "evidence_id", max_length=128))
        _valid_digest(self.evidence_digest, "evidence_digest")
        _aware(self.reconciled_at, "reconciled_at")
        if self.status is ReconciledRunStatus.SUCCEEDED:
            if self.failure_code is not None:
                raise ValueError("a succeeded transition cannot carry a failure code")
            if self.output_digest is not None:
                _valid_digest(self.output_digest, "output_digest")
        else:
            if self.failure_code is None:
                raise ValueError("a failed transition requires a redacted failure code")
            _error_code(self.failure_code)
            if self.output_digest is not None:
                raise ValueError("a failed transition cannot carry an output digest")


class ProviderOutcomeProbePort(Protocol):
    async def probe(self, *, case: UnknownOutcomeRecord) -> ProviderOutcomeObservation | None:
        """Resolve one ambiguous attempt from an authoritative source.

        Returns ``None`` when the source holds no record for the attempt (for
        example a provider that has already pruned its journal).  Returning
        ``None`` is not a resolution and must never be treated as success.
        Implementations must not repeat the provider call.
        """
        ...


class RunTerminalTransitionPort(Protocol):
    async def apply(self, *, transition: RunTerminalTransition) -> RunTerminalTransitionResult:
        """Compare-and-set the run to its terminal state in one durable write.

        Implementations must bind ``command_idempotency_key`` to the immutable
        command digest (identical replay returns ``DUPLICATE``, changed digest is
        a conflict), fence on the current run generation and attempt, and record
        the evidence handle/digest with the state transition.  They must never
        publish output or reopen a run whose generation has advanced.
        """
        ...


class UnknownOutcomeJournalPort(Protocol):
    """Durable unknown-case journal.

    Implementations must, inside one database transaction per method:

    * select due cases only for the requested tenant with
      ``SELECT ... FOR UPDATE SKIP LOCKED`` ordered by ``next_reconcile_at``;
    * treat ``claimed`` cases whose lease expired and ``decided`` cases as
      claimable again, so a crash between decision and transition resumes;
    * compare-and-set on ``(reconcile_id, state, claim_token, claim_generation)``
      and report a lost race as ``False``/``applied=False`` rather than raising;
    * write the redacted audit row in the same transaction as the resolution;
    * never return a case outside the requested tenant.
    """

    async def register_unknown(
        self, *, record: UnknownOutcomeRecord
    ) -> tuple[UnknownOutcomeRecord, bool]:
        """Insert one case per attempt identity; identical replay returns ``(winner, False)``."""
        ...

    async def claim_due(
        self, *, tenant_id: str, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[UnknownOutcomeClaim, ...]: ...

    async def load_case(
        self, *, tenant_id: str, run_id: str, generation: int, attempt: int
    ) -> UnknownOutcomeRecord | None: ...

    async def record_decision(
        self,
        *,
        claim: UnknownOutcomeClaim,
        decision: CaseDecision,
        resolution_digest: str,
        transition_key: str,
        observation: ProviderOutcomeObservation,
        attempted_at: datetime,
    ) -> bool: ...

    async def record_resolution(
        self,
        *,
        claim: UnknownOutcomeClaim,
        resolution: CaseResolution,
        reason_code: str,
        resolved_at: datetime,
    ) -> ReconciliationWriteResult: ...

    async def record_deferral(
        self,
        *,
        claim: UnknownOutcomeClaim,
        next_reconcile_at: datetime | None,
        reason_code: str,
        attempted_at: datetime,
    ) -> bool:
        """Release the case for a later pass, or quarantine it when ``next`` is ``None``."""
        ...


@dataclass(frozen=True, slots=True)
class AgentRunReconcilePolicy:
    """Deterministic, resumable re-probe policy for unresolved cases."""

    max_reconcile_attempts: int = 5
    initial_backoff_seconds: int = 30
    max_backoff_seconds: int = 1800
    jitter_basis_points: int = 2000

    def __post_init__(self) -> None:
        for name in (
            "max_reconcile_attempts",
            "initial_backoff_seconds",
            "max_backoff_seconds",
            "jitter_basis_points",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
        if self.max_reconcile_attempts < 1:
            raise ValueError("max_reconcile_attempts must be positive")
        if self.initial_backoff_seconds < 1:
            raise ValueError("initial_backoff_seconds must be positive")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must be at least the initial backoff")
        if not 0 <= self.jitter_basis_points <= 10_000:
            raise ValueError("jitter_basis_points must be between 0 and 10000")

    def exhausted(self, *, attempts: int) -> bool:
        return attempts >= self.max_reconcile_attempts

    def next_probe_at(self, *, case: UnknownOutcomeRecord, now: datetime) -> datetime:
        _aware(now, "now")
        attempts = max(1, case.reconcile_attempts)
        if attempts - 1 >= 32:
            base_seconds = self.max_backoff_seconds
        else:
            base_seconds = min(
                self.max_backoff_seconds, self.initial_backoff_seconds * (1 << (attempts - 1))
            )
        spread = base_seconds * self.jitter_basis_points // 10_000
        if spread:
            material = f"agent-run-reconcile-v1\x00{case.reconcile_key}\x00{attempts}".encode("utf-8")
            sample = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            offset = sample % (2 * spread + 1) - spread
        else:
            offset = 0
        seconds = max(1, min(self.max_backoff_seconds, base_seconds + offset))
        return now + timedelta(seconds=seconds)


class ReconciliationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_ELIGIBLE = "retry_eligible"
    SUPERSEDED = "superseded"
    DEFERRED = "deferred"
    QUARANTINED = "quarantined"
    ALREADY_RESOLVED = "already_resolved"
    LOST_RACE = "lost_race"


@dataclass(frozen=True, slots=True)
class CaseReconciliation:
    outcome: ReconciliationOutcome
    case: UnknownOutcomeRecord
    reason_code: str
    observation: ProviderOutcomeObservation | None = None
    transition: RunTerminalTransitionResult | None = None
    audit: ReconciliationAuditEntry | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ReconciliationOutcome):
            raise ValueError("outcome must be a ReconciliationOutcome")
        _reason_code(self.reason_code)
        if self.outcome in {
            ReconciliationOutcome.SUCCEEDED,
            ReconciliationOutcome.FAILED,
            ReconciliationOutcome.RETRY_ELIGIBLE,
        } and self.observation is None:
            raise ValueError("a conclusive outcome requires its provider observation")
        if self.outcome in {ReconciliationOutcome.SUCCEEDED, ReconciliationOutcome.FAILED}:
            if self.transition is None or not self.transition.settled:
                raise ValueError("a settled run status requires a settled transition")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    claimed: int
    succeeded: int
    failed: int
    retry_eligible: int
    superseded: int
    deferred: int
    quarantined: int
    lost_race: int

    def __post_init__(self) -> None:
        counters = (
            self.claimed,
            self.succeeded,
            self.failed,
            self.retry_eligible,
            self.superseded,
            self.deferred,
            self.quarantined,
            self.lost_race,
        )
        if min(counters) < 0:
            raise ValueError("reconciliation counters cannot be negative")
        settled = sum(counters[1:])
        if settled != self.claimed:
            raise ValueError("every claimed case must be accounted for exactly once")


_TERMINAL = {
    ProviderObservationState.SUCCEEDED: ReconciledRunStatus.SUCCEEDED,
    ProviderObservationState.FAILED: ReconciledRunStatus.FAILED,
}

_DECISION_BY_STATE = {
    ProviderObservationState.SUCCEEDED: CaseDecision.SUCCEEDED,
    ProviderObservationState.FAILED: CaseDecision.FAILED,
    ProviderObservationState.NOT_APPLIED: CaseDecision.NOT_APPLIED,
}

_STATE_BY_DECISION = {decision: state for state, decision in _DECISION_BY_STATE.items()}


def _resolution_digest(*, case_key: str, observation: ProviderOutcomeObservation) -> str:
    return _canonical_digest(
        {
            "kind": "agent_run.reconcile.resolution",
            "policy": "agent_run.reconcile.v1",
            "case": case_key,
            "state": observation.state.value,
            "source": observation.source.value,
            "evidence_id": observation.evidence_id,
            "evidence_digest": observation.evidence_digest,
            "failure_code": observation.failure_code,
            "output_digest": observation.output_digest,
            "observed_at": observation.observed_at.isoformat(),
        }
    )


def _terminal_transition_key(*, case: UnknownOutcomeRecord, decision: CaseDecision, digest: str) -> str:
    # Deterministic: a replay addresses the same command, never a second one.
    material = f"agent_run.reconcile\x00{case.reconcile_key}\x00{decision.value}\x00{digest}".encode("utf-8")
    return "agent_run.reconcile:" + hashlib.sha256(material).hexdigest()[:40]


def _case_id(case_key: str) -> str:
    return f"aru_{hashlib.sha256(case_key.encode('utf-8')).hexdigest()[:32]}"


def _transition_for(
    *, case: UnknownOutcomeRecord, observation: ProviderOutcomeObservation
) -> RunTerminalTransition:
    status = _TERMINAL.get(observation.state)
    if status is None:  # pragma: no cover - not-applied never builds a transition
        raise ValueError("a not-applied observation does not produce a terminal transition")
    if case.transition_key is None:  # pragma: no cover - guarded by record validation
        raise ValueError("a decided case must carry its transition key")
    return RunTerminalTransition(
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        run_id=case.run_id,
        run_generation=case.run_generation,
        run_attempt=case.run_attempt,
        status=status,
        command_idempotency_key=case.transition_key,
        evidence_id=observation.evidence_id,
        evidence_digest=observation.evidence_digest,
        reconciled_at=observation.observed_at,
        failure_code=observation.failure_code,
        output_digest=observation.output_digest if status is ReconciledRunStatus.SUCCEEDED else None,
    )


def _observation_from_case(case: UnknownOutcomeRecord) -> ProviderOutcomeObservation:
    """Reconstruct the persisted decision's observation for a resumed transition."""
    if case.decided_status is None:  # pragma: no cover - guarded by record validation
        raise ValueError("a decided case must carry its decision")
    return ProviderOutcomeObservation(
        state=_STATE_BY_DECISION[case.decided_status],
        source=ProviderEvidenceSource.ATTEMPT_JOURNAL,
        observed_at=case.decided_at,  # type: ignore[arg-type]
        evidence_id=case.evidence_id,  # type: ignore[arg-type]
        evidence_digest=case.evidence_digest,  # type: ignore[arg-type]
        # The persisted reason code explains the decision; the run's failure code
        # is a different vocabulary and must stay a redacted agent_run.error code.
        # A successful outcome carries no failure code at all.
        failure_code=(
            None
            if case.decided_status is CaseDecision.SUCCEEDED
            else RECONCILE_FAILURE_CODE
        ),
        output_digest=case.output_digest,
    )


def _decided_case(
    *,
    case: UnknownOutcomeRecord,
    decision: CaseDecision,
    digest: str,
    transition_key: str,
    observation: ProviderOutcomeObservation,
    attempted_at: datetime,
) -> UnknownOutcomeRecord:
    return UnknownOutcomeRecord(
        reconcile_id=case.reconcile_id,
        reconcile_key=case.reconcile_key,
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        run_id=case.run_id,
        run_generation=case.run_generation,
        run_attempt=case.run_attempt,
        state=UnknownOutcomeState.DECIDED,
        created_at=case.created_at,
        updated_at=attempted_at,
        provider_request_id=case.provider_request_id,
        claim_token=case.claim_token,
        claim_generation=case.claim_generation,
        lease_until=case.lease_until,
        reconcile_attempts=case.reconcile_attempts + 1,
        next_reconcile_at=None,
        decided_status=decision,
        resolution_digest=digest,
        transition_key=transition_key,
        decided_at=attempted_at,
        evidence_id=observation.evidence_id,
        evidence_digest=observation.evidence_digest,
        output_digest=observation.output_digest,
        reason_code=observation.failure_code,
    )


def build_unknown_outcome_case(
    *,
    tenant_id: str,
    project_id: str,
    run_id: str,
    generation: int,
    attempt: int,
    observed_at: datetime,
    provider_request_id: str | None = None,
) -> UnknownOutcomeRecord:
    """Create the durable case a worker registers instead of blind-retrying."""
    _aware(observed_at, "observed_at")
    identity = UnknownOutcomeIdentity(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        generation=generation,
        attempt=attempt,
    )
    return UnknownOutcomeRecord(
        reconcile_id=_case_id(identity.key),
        reconcile_key=identity.key,
        tenant_id=identity.tenant_id,
        project_id=identity.project_id,
        run_id=identity.run_id,
        run_generation=identity.generation,
        run_attempt=identity.attempt,
        state=UnknownOutcomeState.PENDING,
        created_at=observed_at,
        updated_at=observed_at,
        provider_request_id=provider_request_id,
        next_reconcile_at=observed_at,
    )


class AgentRunUnknownOutcomeReconciler:
    """Resolve unknown provider outcomes; never retries them blindly."""

    def __init__(
        self,
        *,
        journal: UnknownOutcomeJournalPort | None,
        probe: ProviderOutcomeProbePort | None,
        transitions: RunTerminalTransitionPort | None,
        scope: TenantScope,
        policy: AgentRunReconcilePolicy | None = None,
        claim_lease: timedelta = timedelta(seconds=60),
        max_batch: int = 50,
    ) -> None:
        if not isinstance(scope, TenantScope):
            raise ValueError("scope must be a TenantScope")
        if not isinstance(claim_lease, timedelta) or claim_lease <= timedelta(0):
            raise ValueError("claim_lease must be a positive timedelta")
        if not isinstance(max_batch, int) or isinstance(max_batch, bool) or not 1 <= max_batch <= 500:
            raise ValueError("max_batch must be between 1 and 500")
        self._journal = journal
        self._probe = probe
        self._transitions = transitions
        self._scope = scope
        self._policy = policy or AgentRunReconcilePolicy()
        self._claim_lease = claim_lease
        self._max_batch = max_batch

    @property
    def is_wired(self) -> bool:
        return self._journal is not None and self._probe is not None and self._transitions is not None

    @property
    def wiring_blocker(self) -> str | None:
        return None if self.is_wired else WIRING_BLOCKER

    async def register_case(self, *, case: UnknownOutcomeRecord) -> UnknownOutcomeRecord:
        """Durably record an ambiguous attempt before any retry decision exists."""
        journal, _, _ = self._require_wired()
        tenant = self._scope.require(case.tenant_id)
        stored, _inserted = await journal.register_unknown(record=case)
        if stored.tenant_id != tenant or stored.reconcile_key != case.reconcile_key:
            raise TenantScopeViolation("journal returned a case outside the requested identity")
        return stored

    async def reconcile_due(
        self,
        *,
        tenant_id: str,
        now: datetime,
        limit: int | None = None,
        resolution_deadline: datetime | None = None,
    ) -> ReconciliationReport:
        """One bounded reconciliation pass for one tenant; no loop, no sleep."""
        journal, probe, transitions = self._require_wired()
        tenant = self._scope.require(tenant_id)
        _aware(now, "now")
        if resolution_deadline is not None:
            _aware(resolution_deadline, "resolution_deadline")
        batch = self._batch(limit)

        claims = await journal.claim_due(
            tenant_id=tenant,
            now=now,
            lease_until=now + self._claim_lease,
            limit=batch,
        )
        if len(claims) > batch:
            raise AgentRunReconcileBlocked(
                WIRING_BLOCKER, "unknown-outcome journal returned more claims than requested"
            )

        counters = dict.fromkeys(ReconciliationOutcome, 0)
        # Validate the whole batch before acting: one foreign case must abort the
        # pass before any tenant-local run transition is applied.
        if any(claim.record.tenant_id != tenant for claim in claims):
            raise TenantScopeViolation(
                "unknown-outcome journal returned a case outside the requested tenant"
            )
        for claim in claims:
            result = await self._reconcile_claim(
                claim=claim,
                now=now,
                probe=probe,
                transitions=transitions,
                resolution_deadline=resolution_deadline,
            )
            counters[result.outcome] += 1
        return ReconciliationReport(
            claimed=len(claims),
            succeeded=counters[ReconciliationOutcome.SUCCEEDED],
            failed=counters[ReconciliationOutcome.FAILED],
            retry_eligible=counters[ReconciliationOutcome.RETRY_ELIGIBLE],
            superseded=counters[ReconciliationOutcome.SUPERSEDED],
            deferred=counters[ReconciliationOutcome.DEFERRED],
            quarantined=counters[ReconciliationOutcome.QUARANTINED],
            lost_race=counters[ReconciliationOutcome.LOST_RACE],
        )

    async def reconcile_case(
        self,
        *,
        tenant_id: str,
        run_id: str,
        generation: int,
        attempt: int,
        now: datetime,
        resolution_deadline: datetime | None = None,
    ) -> CaseReconciliation:
        """Reconcile one explicitly addressed case.

        A case that already reached a terminal state is reported as
        ``ALREADY_RESOLVED`` and produces **no** second run transition.
        """
        journal, probe, transitions = self._require_wired()
        tenant = self._scope.require(tenant_id)
        _aware(now, "now")
        case = await journal.load_case(
            tenant_id=tenant,
            run_id=_required(run_id, "run_id", max_length=40),
            generation=generation,
            attempt=attempt,
        )
        if case is None:
            raise LookupError("no unknown-outcome case exists for this attempt")
        if case.tenant_id != tenant:
            raise TenantScopeViolation("unknown-outcome journal returned a case outside the tenant")
        if case.state.terminal:
            return CaseReconciliation(
                outcome=ReconciliationOutcome.ALREADY_RESOLVED,
                case=case,
                reason_code=RECONCILE_REASON_SETTLED,
            )

        claims = await journal.claim_due(
            tenant_id=tenant,
            now=now,
            lease_until=now + self._claim_lease,
            limit=self._batch(None),
        )
        for claim in claims:
            if claim.record.reconcile_id == case.reconcile_id:
                return await self._reconcile_claim(
                    claim=claim,
                    now=now,
                    probe=probe,
                    transitions=transitions,
                    resolution_deadline=resolution_deadline,
                )
        return CaseReconciliation(
            outcome=ReconciliationOutcome.DEFERRED,
            case=case,
            reason_code=RECONCILE_REASON_UNKNOWN,
        )

    async def _reconcile_claim(
        self,
        *,
        claim: UnknownOutcomeClaim,
        now: datetime,
        probe: ProviderOutcomeProbePort,
        transitions: RunTerminalTransitionPort,
        resolution_deadline: datetime | None,
    ) -> CaseReconciliation:
        case = claim.record

        if case.state is UnknownOutcomeState.DECIDED:
            # Resume a decision whose transition may not have been applied.
            return await self._apply_decision(
                claim=claim, observation=_observation_from_case(case), now=now, transitions=transitions
            )

        observation = await self._probe_case(case=case, probe=probe, now=now)
        if observation is None or not observation.conclusive:
            return await self._defer(
                claim=claim,
                now=now,
                reason_code=RECONCILE_REASON_UNKNOWN,
                resolution_deadline=resolution_deadline,
            )
        if _contradicts_stored_evidence(case, observation):
            return await self._quarantine(
                claim=claim, now=now, reason_code=RECONCILE_REASON_CONFLICTING_EVIDENCE
            )

        journal = self._journal
        if journal is None:  # pragma: no cover - guarded by _require_wired
            raise AgentRunReconcileBlocked(WIRING_BLOCKER, "unknown-outcome journal is not wired")
        decision = _DECISION_BY_STATE[observation.state]
        digest = _resolution_digest(case_key=case.reconcile_key, observation=observation)
        transition_key = _terminal_transition_key(case=case, decision=decision, digest=digest)
        recorded = await journal.record_decision(
            claim=claim,
            decision=decision,
            resolution_digest=digest,
            transition_key=transition_key,
            observation=observation,
            attempted_at=now,
        )
        if not recorded:
            return CaseReconciliation(
                outcome=ReconciliationOutcome.LOST_RACE,
                case=case,
                reason_code=RECONCILE_REASON_UNKNOWN,
                observation=observation,
            )
        resumed = UnknownOutcomeClaim(
            record=_decided_case(
                case=case,
                decision=decision,
                digest=digest,
                transition_key=transition_key,
                observation=observation,
                attempted_at=now,
            ),
            claim_token=claim.claim_token,
            claim_generation=claim.claim_generation,
            lease_until=claim.lease_until,
            claimed_at=claim.claimed_at,
        )
        return await self._apply_decision(
            claim=resumed, observation=observation, now=now, transitions=transitions
        )

    async def _apply_decision(
        self,
        *,
        claim: UnknownOutcomeClaim,
        observation: ProviderOutcomeObservation,
        now: datetime,
        transitions: RunTerminalTransitionPort,
    ) -> CaseReconciliation:
        journal = self._journal
        if journal is None:  # pragma: no cover - guarded by _require_wired
            raise AgentRunReconcileBlocked(WIRING_BLOCKER, "unknown-outcome journal is not wired")
        case = claim.record
        if case.decided_status is None:  # pragma: no cover - guarded by record validation
            raise AgentRunReconcileBlocked(WIRING_BLOCKER, "decided case has no persisted decision")

        if case.decided_status is CaseDecision.NOT_APPLIED:
            written = await journal.record_resolution(
                claim=claim,
                resolution=CaseResolution.RETRY_ELIGIBLE,
                reason_code=RECONCILE_REASON_SETTLED,
                resolved_at=now,
            )
            if not written.applied:
                return CaseReconciliation(
                    outcome=ReconciliationOutcome.LOST_RACE,
                    case=case,
                    reason_code=RECONCILE_REASON_SETTLED,
                    observation=observation,
                )
            return CaseReconciliation(
                outcome=ReconciliationOutcome.RETRY_ELIGIBLE,
                case=case,
                reason_code=RECONCILE_REASON_SETTLED,
                observation=observation,
                audit=written.audit,
            )

        transition = await transitions.apply(transition=_transition_for(case=case, observation=observation))
        if transition.status is RunTerminalTransitionStatus.BLOCKED:
            # Fail closed: leave the claim to expire so the next pass resumes it.
            raise AgentRunReconcileBlocked(
                WIRING_BLOCKER, "durable run transition is blocked; reconciliation cannot settle"
            )
        if transition.status is RunTerminalTransitionStatus.CONFLICT:
            written = await journal.record_resolution(
                claim=claim,
                resolution=CaseResolution.SUPERSEDED,
                reason_code=RECONCILE_REASON_RUN_TERMINAL,
                resolved_at=now,
            )
            if not written.applied:
                return CaseReconciliation(
                    outcome=ReconciliationOutcome.LOST_RACE,
                    case=case,
                    reason_code=RECONCILE_REASON_RUN_TERMINAL,
                    observation=observation,
                    transition=transition,
                )
            return CaseReconciliation(
                outcome=ReconciliationOutcome.SUPERSEDED,
                case=case,
                reason_code=RECONCILE_REASON_RUN_TERMINAL,
                observation=observation,
                transition=transition,
                audit=written.audit,
            )

        resolution = (
            CaseResolution.SUCCEEDED
            if case.decided_status is CaseDecision.SUCCEEDED
            else CaseResolution.FAILED
        )
        written = await journal.record_resolution(
            claim=claim,
            resolution=resolution,
            reason_code=RECONCILE_REASON_SETTLED,
            resolved_at=now,
        )
        if not written.applied:
            return CaseReconciliation(
                outcome=ReconciliationOutcome.LOST_RACE,
                case=case,
                reason_code=RECONCILE_REASON_SETTLED,
                observation=observation,
                transition=transition,
            )
        return CaseReconciliation(
            outcome=(
                ReconciliationOutcome.SUCCEEDED
                if resolution is CaseResolution.SUCCEEDED
                else ReconciliationOutcome.FAILED
            ),
            case=case,
            reason_code=RECONCILE_REASON_SETTLED,
            observation=observation,
            transition=transition,
            audit=written.audit,
        )

    async def _probe_case(
        self,
        *,
        case: UnknownOutcomeRecord,
        probe: ProviderOutcomeProbePort,
        now: datetime,
    ) -> ProviderOutcomeObservation | None:
        try:
            observation = await probe.probe(case=case)
        except AgentRunReconcileBlocked:
            raise
        except Exception:  # noqa: BLE001 - a failing source is inconclusive, never a success
            return None
        if observation is None:
            return None
        if observation.observed_at > now + timedelta(seconds=1):
            # A source cannot answer from the future; treat the answer as unusable.
            return None
        return observation

    async def _defer(
        self,
        *,
        claim: UnknownOutcomeClaim,
        now: datetime,
        reason_code: str,
        resolution_deadline: datetime | None,
    ) -> CaseReconciliation:
        journal = self._journal
        if journal is None:  # pragma: no cover - guarded by _require_wired
            raise AgentRunReconcileBlocked(WIRING_BLOCKER, "unknown-outcome journal is not wired")
        case = claim.record
        if resolution_deadline is not None and now >= resolution_deadline:
            return await self._quarantine(
                claim=claim, now=now, reason_code=RECONCILE_REASON_DEADLINE_EXCEEDED
            )
        if self._policy.exhausted(attempts=case.reconcile_attempts):
            return await self._quarantine(
                claim=claim, now=now, reason_code=RECONCILE_REASON_ATTEMPTS_EXHAUSTED
            )
        next_at = self._policy.next_probe_at(case=case, now=now)
        if resolution_deadline is not None and next_at > resolution_deadline:
            return await self._quarantine(
                claim=claim, now=now, reason_code=RECONCILE_REASON_DEADLINE_EXCEEDED
            )
        applied = await journal.record_deferral(
            claim=claim,
            next_reconcile_at=next_at,
            reason_code=_reason_code(reason_code),
            attempted_at=now,
        )
        if not applied:
            return CaseReconciliation(
                outcome=ReconciliationOutcome.LOST_RACE,
                case=case,
                reason_code=_reason_code(reason_code),
            )
        return CaseReconciliation(
            outcome=ReconciliationOutcome.DEFERRED,
            case=case,
            reason_code=_reason_code(reason_code),
        )

    async def _quarantine(
        self, *, claim: UnknownOutcomeClaim, now: datetime, reason_code: str
    ) -> CaseReconciliation:
        journal = self._journal
        if journal is None:  # pragma: no cover - guarded by _require_wired
            raise AgentRunReconcileBlocked(WIRING_BLOCKER, "unknown-outcome journal is not wired")
        code = _reason_code(reason_code)
        applied = await journal.record_deferral(
            claim=claim,
            next_reconcile_at=None,
            reason_code=code,
            attempted_at=now,
        )
        if not applied:
            return CaseReconciliation(
                outcome=ReconciliationOutcome.LOST_RACE,
                case=claim.record,
                reason_code=code,
            )
        # A quarantined case is never retried; callers must also cancel any
        # pending retry schedule for this run (AgentRunRetryScheduler.cancel_for_run).
        return CaseReconciliation(
            outcome=ReconciliationOutcome.QUARANTINED,
            case=claim.record,
            reason_code=code,
        )

    def _batch(self, limit: int | None) -> int:
        batch = self._max_batch if limit is None else limit
        if not isinstance(batch, int) or isinstance(batch, bool) or not 1 <= batch <= self._max_batch:
            raise ValueError("limit must be between 1 and the configured batch size")
        return batch

    def _require_wired(
        self,
    ) -> tuple[UnknownOutcomeJournalPort, ProviderOutcomeProbePort, RunTerminalTransitionPort]:
        if self._journal is None or self._probe is None or self._transitions is None:
            raise AgentRunReconcileBlocked(
                WIRING_BLOCKER,
                "no durable unknown-outcome journal, provider probe and terminal transition are wired",
            )
        return self._journal, self._probe, self._transitions


def _contradicts_stored_evidence(
    case: UnknownOutcomeRecord, observation: ProviderOutcomeObservation
) -> bool:
    """A case may not adopt evidence that disagrees with what it already recorded."""
    if case.evidence_id is not None and case.evidence_id != observation.evidence_id:
        return True
    return case.evidence_digest is not None and case.evidence_digest != observation.evidence_digest


# --------------------------------------------------------------------------- #
# SQL adapter (metadata-free TableClause; no DDL, no ORM registration)
# --------------------------------------------------------------------------- #

# Explicit column types are required: an untyped Column would make asyncpg
# unable to infer bind parameter types for the due-selection predicates.
_unknown_outcomes = table(
    UNKNOWN_OUTCOME_TABLE,
    Column("reconcile_id", String(64), primary_key=True),
    Column("reconcile_key", String(71), nullable=False),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(40), nullable=False),
    Column("run_id", String(40), nullable=False),
    Column("run_generation", Integer, nullable=False),
    Column("run_attempt", Integer, nullable=False),
    Column("provider_request_id", String(128), nullable=True),
    Column("state", String(24), nullable=False),
    Column("claim_token", String(128), nullable=True),
    Column("claim_generation", Integer, nullable=False),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("reconcile_attempts", Integer, nullable=False),
    Column("next_reconcile_at", DateTime(timezone=True), nullable=True),
    Column("decided_status", String(24), nullable=True),
    Column("resolution_digest", String(71), nullable=True),
    Column("transition_key", String(128), nullable=True),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("resolved_status", String(24), nullable=True),
    Column("evidence_id", String(128), nullable=True),
    Column("evidence_digest", String(71), nullable=True),
    Column("output_digest", String(71), nullable=True),
    Column("reason_code", String(128), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

_reconciliation_audit = table(
    RECONCILIATION_AUDIT_TABLE,
    Column("audit_id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("project_id", String(40), nullable=False),
    Column("run_id", String(40), nullable=False),
    Column("run_generation", Integer, nullable=False),
    Column("run_attempt", Integer, nullable=False),
    Column("decision", String(24), nullable=False),
    Column("resolution", String(24), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("evidence_id", String(128), nullable=False),
    Column("evidence_digest", String(71), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)


def _case_from_mapping(row: Mapping[str, Any]) -> UnknownOutcomeRecord:
    def stamp(name: str) -> datetime:
        return _aware(row[name], name)

    def optional_stamp(name: str) -> datetime | None:
        return None if row[name] is None else stamp(name)

    decision = row["decided_status"]
    resolution = row["resolved_status"]
    return UnknownOutcomeRecord(
        reconcile_id=row["reconcile_id"],
        reconcile_key=row["reconcile_key"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        run_generation=int(row["run_generation"]),
        run_attempt=int(row["run_attempt"]),
        state=UnknownOutcomeState(row["state"]),
        created_at=stamp("created_at"),
        updated_at=stamp("updated_at"),
        provider_request_id=row["provider_request_id"],
        claim_token=row["claim_token"],
        claim_generation=int(row["claim_generation"] or 0),
        lease_until=optional_stamp("lease_until"),
        reconcile_attempts=int(row["reconcile_attempts"] or 0),
        next_reconcile_at=optional_stamp("next_reconcile_at"),
        decided_status=CaseDecision(decision) if decision is not None else None,
        resolution_digest=row["resolution_digest"],
        transition_key=row["transition_key"],
        decided_at=optional_stamp("decided_at"),
        resolved_status=CaseResolution(resolution) if resolution is not None else None,
        evidence_id=row["evidence_id"],
        evidence_digest=row["evidence_digest"],
        output_digest=row["output_digest"],
        reason_code=row["reason_code"],
        resolved_at=optional_stamp("resolved_at"),
    )


class SqlAlchemyUnknownOutcomeJournal:
    """Durable unknown-outcome journal on ``agent_run_unknown_outcomes``.

    The adapter reports derived values; it never invents a decision.  A lost
    compare-and-set returns ``False`` (or a non-applied write result), which the
    reconciler maps to ``LOST_RACE`` instead of pretending the case settled.
    """

    def __init__(self, *, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def ensure_ready(self) -> None:
        from sqlalchemy.exc import ProgrammingError

        try:
            async with self._session_factory() as session:
                await session.execute(select(_unknown_outcomes.c.reconcile_id).limit(1))
                await session.execute(select(_reconciliation_audit.c.audit_id).limit(1))
        except ProgrammingError as exc:
            raise AgentRunReconcileBlocked(
                RECONCILE_BLOCKER_TABLE,
                "agent_run_unknown_outcomes/agent_run_reconciliation_audit are not present; "
                "reconciliation stays blocked",
            ) from exc

    async def register_unknown(
        self, *, record: UnknownOutcomeRecord
    ) -> tuple[UnknownOutcomeRecord, bool]:
        from sqlalchemy.exc import IntegrityError

        if record.state is not UnknownOutcomeState.PENDING:
            raise ValueError("a new unknown-outcome case must start pending")
        async with self._session_factory() as session, session.begin():
            existing = (
                await session.execute(
                    select(_unknown_outcomes)
                    .where(_unknown_outcomes.c.reconcile_key == record.reconcile_key)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if existing is not None:
                stored = _case_from_mapping(existing)
                if stored.tenant_id != record.tenant_id:
                    raise TenantScopeViolation("retry case identity belongs to another tenant")
                return stored, False
            try:
                async with session.begin_nested():
                    await session.execute(
                        insert(_unknown_outcomes).values(
                            reconcile_id=record.reconcile_id,
                            reconcile_key=record.reconcile_key,
                            tenant_id=record.tenant_id,
                            project_id=record.project_id,
                            run_id=record.run_id,
                            run_generation=record.run_generation,
                            run_attempt=record.run_attempt,
                            provider_request_id=record.provider_request_id,
                            state=record.state.value,
                            claim_token=None,
                            claim_generation=0,
                            lease_until=None,
                            reconcile_attempts=0,
                            next_reconcile_at=record.next_reconcile_at,
                            decided_status=None,
                            resolution_digest=None,
                            transition_key=None,
                            decided_at=None,
                            resolved_status=None,
                            evidence_id=None,
                            evidence_digest=None,
                            output_digest=None,
                            reason_code=None,
                            resolved_at=None,
                            created_at=record.created_at,
                            updated_at=record.updated_at,
                        )
                    )
            except IntegrityError as exc:
                winner = (
                    await session.execute(
                        select(_unknown_outcomes).where(
                            _unknown_outcomes.c.reconcile_key == record.reconcile_key
                        )
                    )
                ).mappings().one_or_none()
                if winner is None:
                    raise
                stored = _case_from_mapping(winner)
                if stored.tenant_id != record.tenant_id:
                    raise TenantScopeViolation("retry case identity belongs to another tenant") from exc
                return stored, False
            return record, True

    async def claim_due(
        self, *, tenant_id: str, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[UnknownOutcomeClaim, ...]:
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        if lease_until <= now:
            raise ValueError("lease_until must be after now")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        due = and_(
            _unknown_outcomes.c.tenant_id == tenant,
            or_(
                and_(
                    _unknown_outcomes.c.state == UnknownOutcomeState.PENDING.value,
                    or_(
                        _unknown_outcomes.c.next_reconcile_at.is_(None),
                        _unknown_outcomes.c.next_reconcile_at <= now,
                    ),
                ),
                and_(
                    _unknown_outcomes.c.state == UnknownOutcomeState.CLAIMED.value,
                    _unknown_outcomes.c.lease_until <= now,
                ),
                # A decision whose transition may not have been applied resumes here.
                _unknown_outcomes.c.state == UnknownOutcomeState.DECIDED.value,
            ),
        )
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    select(_unknown_outcomes)
                    .where(due)
                    .order_by(_unknown_outcomes.c.next_reconcile_at, _unknown_outcomes.c.reconcile_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).mappings().all()

            claims: list[UnknownOutcomeClaim] = []
            for row in rows:
                token = uuid.uuid4().hex
                generation = int(row["claim_generation"] or 0) + 1
                next_state = (
                    UnknownOutcomeState.CLAIMED.value
                    if row["state"] == UnknownOutcomeState.PENDING.value
                    else row["state"]
                )
                result = await session.execute(
                    update(_unknown_outcomes)
                    .where(
                        _unknown_outcomes.c.reconcile_id == row["reconcile_id"],
                        _unknown_outcomes.c.tenant_id == tenant,
                        _unknown_outcomes.c.state == row["state"],
                        _unknown_outcomes.c.claim_generation == row["claim_generation"],
                    )
                    .values(
                        state=next_state,
                        claim_token=token,
                        claim_generation=generation,
                        lease_until=lease_until,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:  # pragma: no cover - the row is locked above
                    continue
                claimed = dict(row)
                claimed.update(
                    {
                        "state": next_state,
                        "claim_token": token,
                        "claim_generation": generation,
                        "lease_until": lease_until,
                        "updated_at": now,
                    }
                )
                claims.append(
                    UnknownOutcomeClaim(
                        record=_case_from_mapping(claimed),
                        claim_token=token,
                        claim_generation=generation,
                        lease_until=lease_until,
                        claimed_at=now,
                    )
                )
            return tuple(claims)

    async def load_case(
        self, *, tenant_id: str, run_id: str, generation: int, attempt: int
    ) -> UnknownOutcomeRecord | None:
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(_unknown_outcomes).where(
                        _unknown_outcomes.c.tenant_id == tenant,
                        _unknown_outcomes.c.run_id == _required(run_id, "run_id", max_length=40),
                        _unknown_outcomes.c.run_generation == generation,
                        _unknown_outcomes.c.run_attempt == attempt,
                    )
                )
            ).mappings().one_or_none()
        return None if row is None else _case_from_mapping(row)

    async def record_decision(
        self,
        *,
        claim: UnknownOutcomeClaim,
        decision: CaseDecision,
        resolution_digest: str,
        transition_key: str,
        observation: ProviderOutcomeObservation,
        attempted_at: datetime,
    ) -> bool:
        _aware(attempted_at, "attempted_at")
        _valid_digest(resolution_digest, "resolution_digest")
        _required(transition_key, "transition_key", max_length=128)
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(_unknown_outcomes)
                .where(
                    _unknown_outcomes.c.reconcile_id == claim.record.reconcile_id,
                    _unknown_outcomes.c.tenant_id == claim.tenant_id,
                    _unknown_outcomes.c.state.in_(
                        (UnknownOutcomeState.PENDING.value, UnknownOutcomeState.CLAIMED.value)
                    ),
                    _unknown_outcomes.c.claim_token == claim.claim_token,
                    _unknown_outcomes.c.claim_generation == claim.claim_generation,
                )
                .values(
                    state=UnknownOutcomeState.DECIDED.value,
                    decided_status=decision.value,
                    resolution_digest=resolution_digest,
                    transition_key=transition_key,
                    decided_at=attempted_at,
                    reconcile_attempts=_unknown_outcomes.c.reconcile_attempts + 1,
                    evidence_id=observation.evidence_id,
                    evidence_digest=observation.evidence_digest,
                    output_digest=observation.output_digest,
                    reason_code=observation.failure_code,
                    next_reconcile_at=None,
                    lease_until=claim.lease_until,
                    updated_at=attempted_at,
                )
            )
            return result.rowcount == 1

    async def record_resolution(
        self,
        *,
        claim: UnknownOutcomeClaim,
        resolution: CaseResolution,
        reason_code: str,
        resolved_at: datetime,
    ) -> ReconciliationWriteResult:
        code = _reason_code(reason_code)
        _aware(resolved_at, "resolved_at")
        record = claim.record
        if record.decided_status is None or record.evidence_id is None or record.evidence_digest is None:
            raise ValueError("a case can only be resolved after its durable decision")
        audit = ReconciliationAuditEntry(
            audit_id=f"ara_{uuid.uuid4().hex[:16]}",
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            run_id=record.run_id,
            generation=record.run_generation,
            attempt=record.run_attempt,
            decision=record.decided_status,
            resolution=resolution,
            reason_code=code,
            evidence_id=record.evidence_id,
            evidence_digest=record.evidence_digest,
            recorded_at=resolved_at,
        )
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(_unknown_outcomes)
                .where(
                    _unknown_outcomes.c.reconcile_id == record.reconcile_id,
                    _unknown_outcomes.c.tenant_id == claim.tenant_id,
                    _unknown_outcomes.c.state == UnknownOutcomeState.DECIDED.value,
                    _unknown_outcomes.c.claim_token == claim.claim_token,
                    _unknown_outcomes.c.claim_generation == claim.claim_generation,
                )
                .values(
                    state=UnknownOutcomeState.RESOLVED.value,
                    resolved_status=resolution.value,
                    reason_code=code,
                    resolved_at=resolved_at,
                    claim_token=None,
                    lease_until=None,
                    updated_at=resolved_at,
                )
            )
            if result.rowcount != 1:
                return ReconciliationWriteResult(applied=False)
            await session.execute(
                insert(_reconciliation_audit).values(
                    audit_id=audit.audit_id,
                    tenant_id=audit.tenant_id,
                    project_id=audit.project_id,
                    run_id=audit.run_id,
                    run_generation=audit.generation,
                    run_attempt=audit.attempt,
                    decision=audit.decision.value,
                    resolution=audit.resolution.value,
                    reason_code=audit.reason_code,
                    evidence_id=audit.evidence_id,
                    evidence_digest=audit.evidence_digest,
                    recorded_at=audit.recorded_at,
                )
            )
            return ReconciliationWriteResult(applied=True, audit=audit)

    async def record_deferral(
        self,
        *,
        claim: UnknownOutcomeClaim,
        next_reconcile_at: datetime | None,
        reason_code: str,
        attempted_at: datetime,
    ) -> bool:
        code = _reason_code(reason_code)
        _aware(attempted_at, "attempted_at")
        if next_reconcile_at is not None:
            _aware(next_reconcile_at, "next_reconcile_at")
            if next_reconcile_at <= attempted_at:
                raise ValueError("next_reconcile_at must be in the future")
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(_unknown_outcomes)
                .where(
                    _unknown_outcomes.c.reconcile_id == claim.record.reconcile_id,
                    _unknown_outcomes.c.tenant_id == claim.tenant_id,
                    _unknown_outcomes.c.state.in_(
                        (UnknownOutcomeState.PENDING.value, UnknownOutcomeState.CLAIMED.value)
                    ),
                    _unknown_outcomes.c.claim_token == claim.claim_token,
                    _unknown_outcomes.c.claim_generation == claim.claim_generation,
                )
                .values(
                    state=(
                        UnknownOutcomeState.PENDING.value
                        if next_reconcile_at is not None
                        else UnknownOutcomeState.QUARANTINED.value
                    ),
                    next_reconcile_at=next_reconcile_at,
                    reconcile_attempts=_unknown_outcomes.c.reconcile_attempts + 1,
                    # A deferred case keeps its reason out of the row: before a
                    # decision that column holds a provider failure code only,
                    # and a quarantine stores its terminal reason here.
                    reason_code=None if next_reconcile_at is not None else code,
                    resolved_at=None if next_reconcile_at is not None else attempted_at,
                    claim_token=None,
                    lease_until=None,
                    updated_at=attempted_at,
                )
            )
            return result.rowcount == 1


__all__ = [
    "AgentRunReconcileBlocked",
    "AgentRunReconcileError",
    "AgentRunReconcilePolicy",
    "AgentRunUnknownOutcomeReconciler",
    "CaseDecision",
    "CaseReconciliation",
    "CaseResolution",
    "ProviderEvidenceSource",
    "ProviderObservationState",
    "ProviderOutcomeObservation",
    "ProviderOutcomeProbePort",
    "RECONCILIATION_AUDIT_REQUIRED_COLUMNS",
    "RECONCILIATION_AUDIT_TABLE",
    "RECONCILE_BLOCKERS",
    "RECONCILE_BLOCKER_JOURNAL",
    "RECONCILE_BLOCKER_TABLE",
    "RECONCILE_REASON_ATTEMPTS_EXHAUSTED",
    "RECONCILE_REASON_CONFLICTING_EVIDENCE",
    "RECONCILE_REASON_DEADLINE_EXCEEDED",
    "RECONCILE_REASON_RUN_TERMINAL",
    "RECONCILE_REASON_SETTLED",
    "RECONCILE_REASON_SOURCE_UNAVAILABLE",
    "RECONCILE_REASON_UNKNOWN",
    "ReconciledRunStatus",
    "ReconciliationAuditEntry",
    "ReconciliationOutcome",
    "ReconciliationReport",
    "ReconciliationWriteResult",
    "RunTerminalTransition",
    "RunTerminalTransitionPort",
    "RunTerminalTransitionResult",
    "RunTerminalTransitionStatus",
    "SqlAlchemyUnknownOutcomeJournal",
    "UNKNOWN_OUTCOME_REQUIRED_COLUMNS",
    "UNKNOWN_OUTCOME_TABLE",
    "UnknownOutcomeClaim",
    "UnknownOutcomeIdentity",
    "UnknownOutcomeJournalPort",
    "UnknownOutcomeRecord",
    "UnknownOutcomeState",
    "WIRING_BLOCKER",
    "build_unknown_outcome_case",
]
