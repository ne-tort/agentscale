"""Durable, tenant-scoped retry scheduling for Agent Runs.

Context: ``tests/api/agent_runs/readiness_blockers.md`` blocker №5 — there is no
durable retry scheduler.  ``AgentRunExecutionPort.schedule_retry`` is a port
requirement only, so a retryable provider failure had nowhere to live, and an
unknown provider outcome could be blind-retried.

This module owns the application-level retry scheduler:

* failure classification into ``retryable`` / ``terminal`` / ``reconcile_required``;
* deterministic exponential backoff with stable jitter, a bounded delay, an
  attempt cap and an optional absolute deadline;
* a durable ``retry_at`` schedule (one row per failed attempt) with
  ``FOR UPDATE SKIP LOCKED`` due-selection and lease-based claims;
* idempotent planning: replaying the same decision returns the stored row, a
  changed decision for the same attempt is a conflict, never a rewrite;
* a hard refusal to schedule an unknown provider outcome.

Durability model (no process ever sleeps here — work is selected by comparing the
supplied ``now`` with the persisted ``retry_at``):

* a schedule row is written *before* the run is moved back to the queue, so a
  crash between the journal write and the run write leaves a durable due time
  that the next pass still sees;
* a claim never deletes the row: it takes a lease token plus an incremented
  claim generation.  A crash between claim and write leaves the row ``claimed``
  with an expired lease, and the next pass reclaims it.  Nothing is lost, and
  two workers cannot release the same retry.

Tenant isolation: every journal operation takes a mandatory ``tenant_id``, the
scheduler is constructed with a server-resolved :class:`TenantScope`, and every
returned claim is re-validated against the requested tenant before use.

Wiring status: the scheduler needs a concrete ``AgentRunRetryJournalPort``.  The
SQL adapter below is authored against the published table contract
(``agent_run_retry_schedules``); until that table exists in the database the
adapter fails closed with ``agent_run_retry_schedules_table_missing`` and the
scheduler is not wired (``WIRING_BLOCKER``).  This module never emits DDL and
never registers ORM metadata, so schema ownership stays with the persistence
task.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping, Protocol

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    column,
    insert,
    or_,
    select,
    table,
    update,
)

from .execution import (
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
    validate_retry_result,
)

RETRY_SCHEDULE_TABLE = "agent_run_retry_schedules"

#: Columns the persistence task must materialise for the SQL adapter.  The
#: uniqueness of ``schedule_key`` (one row per failed attempt) and the index on
#: ``(tenant_id, state, retry_at)`` are required for correctness, not tuning.
RETRY_SCHEDULE_REQUIRED_COLUMNS = (
    "schedule_id",
    "tenant_id",
    "project_id",
    "run_id",
    "run_generation",
    "run_attempt",
    "retry_at",
    "failure_code",
    "schedule_key",
    "schedule_digest",
    "state",
    "claim_token",
    "claim_generation",
    "lease_until",
    "released_at",
    "cancelled_reason",
    "created_at",
    "updated_at",
)

RETRY_JOURNAL_BLOCKER = "agent_run_retry_journal_unwired"
RETRY_TABLE_BLOCKER = "agent_run_retry_schedules_table_missing"
RETRY_BLOCKERS = {
    "journal": RETRY_JOURNAL_BLOCKER,
    "table": RETRY_TABLE_BLOCKER,
}
WIRING_BLOCKER = RETRY_JOURNAL_BLOCKER

RETRY_REASON_SCHEDULED = "agent_run.retry.scheduled"
RETRY_REASON_EXHAUSTED = "agent_run.retry.exhausted"
RETRY_REASON_TERMINAL = "agent_run.retry.terminal"
RETRY_REASON_RECONCILE_REQUIRED = "agent_run.retry.reconcile_required"
RETRY_REASON_DEADLINE_EXCEEDED = "agent_run.retry.deadline_exceeded"
RETRY_REASON_RUN_TERMINAL = "agent_run.retry.run_terminal"
RETRY_REASON_QUARANTINED = "agent_run.retry.quarantined"
RETRY_REASON_CANCELLED = "agent_run.retry.cancelled"

_RETRY_REASON_PREFIX = "agent_run.retry."
_ERROR_CODE_PREFIX = "agent_run.error."
_MAX_CONFIGURED_DELAY_SECONDS = 365 * 24 * 60 * 60


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class AgentRunRetryError(RuntimeError):
    """Base class for retry scheduling failures."""


class AgentRunRetryBlocked(AgentRunRetryError):
    """The durable retry boundary is not wired or the adapter is unusable."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        super().__init__(detail)


class AgentRunRetryRefused(AgentRunRetryError):
    """A requested retry is not admissible; nothing was written."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(detail)


class AgentRunRetryScheduleConflict(AgentRunRetryError):
    """One attempt identity was reused for a different retry decision."""


class TenantScopeViolation(AgentRunRetryError):
    """A tenant outside the resolved server-side scope was requested or returned."""


# --------------------------------------------------------------------------- #
# Validation helpers (no free-form diagnostics survive these functions)
# --------------------------------------------------------------------------- #


def _required(value: str, name: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    if any(character in text for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{name} must not contain control characters")
    return text


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _canonical_digest(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _valid_digest(value: str, name: str) -> str:
    text = _required(value, name, max_length=71)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(text[7:], 16)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name} must be a sha256 digest") from exc
    return text


def _error_code(value: str) -> str:
    text = _required(value, "failure_code", max_length=128)
    if not text.startswith(_ERROR_CODE_PREFIX):
        raise ValueError("failure_code must be a redacted agent_run.error code")
    return text


def _reason_code(value: str) -> str:
    text = _required(value, "reason_code", max_length=128)
    if not text.startswith(_RETRY_REASON_PREFIX):
        raise ValueError("reason_code must be a redacted agent_run.retry code")
    return text


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #


class AgentRunFailureCategory(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class AgentRunFailureClass(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    #: The provider may or may not have applied the attempt.  Reconciled, never retried.
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True, slots=True)
class AgentRunFailureEvidence:
    """Safe failure inputs; deliberately carries no message, body or payload."""

    category: AgentRunFailureCategory
    http_status: int | None = None
    #: ``True`` when the request was observed leaving this process, ``False``
    #: when it provably never reached the provider, ``None`` when unknowable.
    request_dispatch_observed: bool | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, AgentRunFailureCategory):
            raise ValueError("category must be an AgentRunFailureCategory")
        if self.http_status is not None and (
            not isinstance(self.http_status, int)
            or isinstance(self.http_status, bool)
            or not 100 <= self.http_status <= 599
        ):
            raise ValueError("http_status must be an integer from 100 through 599")
        if self.request_dispatch_observed is not None and not isinstance(self.request_dispatch_observed, bool):
            raise ValueError("request_dispatch_observed must be a bool or None")
        if self.provider_request_id is not None:
            object.__setattr__(
                self,
                "provider_request_id",
                _required(self.provider_request_id, "provider_request_id", max_length=128),
            )

    @property
    def redacted_diagnostic(self) -> str:
        return "Agent Run failure details were redacted."


@dataclass(frozen=True, slots=True)
class AgentRunFailureClassification:
    category: AgentRunFailureCategory
    failure_class: AgentRunFailureClass
    error_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, AgentRunFailureCategory):
            raise ValueError("category must be an AgentRunFailureCategory")
        if not isinstance(self.failure_class, AgentRunFailureClass):
            raise ValueError("failure_class must be an AgentRunFailureClass")
        if self.error_code != f"{_ERROR_CODE_PREFIX}{self.category.value}":
            raise ValueError("error_code must be the fixed redacted code for its category")

    @property
    def retryable(self) -> bool:
        return self.failure_class is AgentRunFailureClass.RETRYABLE

    @property
    def reconcile_required(self) -> bool:
        return self.failure_class is AgentRunFailureClass.RECONCILE_REQUIRED

    @property
    def redacted_diagnostic(self) -> str:
        return "Agent Run failure details were redacted."


_RETRYABLE_CATEGORIES = frozenset(
    {
        AgentRunFailureCategory.TRANSIENT,
        AgentRunFailureCategory.RATE_LIMITED,
        AgentRunFailureCategory.PROVIDER_UNAVAILABLE,
        AgentRunFailureCategory.TIMEOUT,
        AgentRunFailureCategory.CONNECTION,
    }
)

_TERMINAL_CATEGORIES = frozenset(
    {
        AgentRunFailureCategory.AUTHENTICATION,
        AgentRunFailureCategory.AUTHORIZATION,
        AgentRunFailureCategory.INVALID_REQUEST,
        AgentRunFailureCategory.NOT_FOUND,
        AgentRunFailureCategory.CONFLICT,
        AgentRunFailureCategory.PERMANENT,
    }
)

#: Provider statuses that a *response* proves were rejected, not applied.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429}) | frozenset(range(500, 600))

_STATUS_CATEGORIES = {
    401: AgentRunFailureCategory.AUTHENTICATION,
    403: AgentRunFailureCategory.AUTHORIZATION,
    404: AgentRunFailureCategory.NOT_FOUND,
    409: AgentRunFailureCategory.CONFLICT,
}


def _category_from_status(status: int) -> AgentRunFailureCategory:
    if status == 408:
        return AgentRunFailureCategory.TIMEOUT
    if status == 425:
        return AgentRunFailureCategory.TRANSIENT
    if status == 429:
        return AgentRunFailureCategory.RATE_LIMITED
    if 500 <= status < 600:
        return AgentRunFailureCategory.PROVIDER_UNAVAILABLE
    if 400 <= status < 500:
        return _STATUS_CATEGORIES.get(status, AgentRunFailureCategory.INVALID_REQUEST)
    return AgentRunFailureCategory.UNKNOWN


def classify_agent_run_failure(evidence: AgentRunFailureEvidence) -> AgentRunFailureClassification:
    """Classify one provider attempt without accepting free-form diagnostics.

    Rules (fail-closed by construction):

    * an explicit provider *response* status takes precedence over the caller's
      category, because a response proves the attempt was answered;
    * an unknown category is never retryable — it must be reconciled;
    * terminal categories are never retryable;
    * a timeout/connection failure after the provider accepted the request
      (``provider_request_id`` present) is ambiguous, therefore reconcile-required;
    * a retryable category is admitted only when the provider rejected the
      attempt (explicit ``4xx``/``5xx``/``429``/``425``/``408`` response) or when
      it is proven the request never left (``request_dispatch_observed is False``
      and no provider request id).  Everything else needs reconciliation.
    """
    if not isinstance(evidence, AgentRunFailureEvidence):
        raise ValueError("evidence must be AgentRunFailureEvidence")

    category = (
        _category_from_status(evidence.http_status)
        if evidence.http_status is not None
        else evidence.category
    )

    if category is AgentRunFailureCategory.UNKNOWN:
        failure_class = AgentRunFailureClass.RECONCILE_REQUIRED
    elif category in _TERMINAL_CATEGORIES:
        failure_class = AgentRunFailureClass.TERMINAL
    elif category in _RETRYABLE_CATEGORIES:
        provider_accepted = evidence.provider_request_id is not None
        ambiguous_transport = category in {
            AgentRunFailureCategory.TIMEOUT,
            AgentRunFailureCategory.CONNECTION,
        }
        explicit_rejection = (
            evidence.http_status is not None and evidence.http_status in _RETRYABLE_HTTP_STATUSES
        )
        never_sent = (
            evidence.request_dispatch_observed is False and evidence.provider_request_id is None
        )
        if ambiguous_transport and provider_accepted:
            failure_class = AgentRunFailureClass.RECONCILE_REQUIRED
        elif explicit_rejection or never_sent:
            failure_class = AgentRunFailureClass.RETRYABLE
        else:
            failure_class = AgentRunFailureClass.RECONCILE_REQUIRED
    else:  # pragma: no cover - exhaustive over the enum
        raise ValueError("unclassified Agent Run failure category")

    return AgentRunFailureClassification(
        category=category,
        failure_class=failure_class,
        error_code=f"{_ERROR_CODE_PREFIX}{category.value}",
    )


# --------------------------------------------------------------------------- #
# Identity and tenant scope
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TenantScope:
    """Server-resolved tenant set a scheduler/reconciler may act for."""

    tenant_ids: frozenset[str]

    def __post_init__(self) -> None:
        resolved = frozenset(_required(item, "tenant_id", max_length=64) for item in self.tenant_ids)
        if not resolved:
            raise ValueError("tenant scope must not be empty")
        object.__setattr__(self, "tenant_ids", resolved)

    def covers(self, tenant_id: str) -> bool:
        return isinstance(tenant_id, str) and tenant_id.strip() in self.tenant_ids

    def require(self, tenant_id: str) -> str:
        resolved = _required(tenant_id, "tenant_id", max_length=64)
        if resolved not in self.tenant_ids:
            raise TenantScopeViolation("tenant is outside the resolved server-side scope")
        return resolved


@dataclass(frozen=True, slots=True)
class RetryScheduleIdentity:
    """Immutable identity of one failed provider attempt.

    The identity *is* the idempotency key of the schedule: one durable row per
    ``(tenant, run, generation, attempt)``.
    """

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

    @classmethod
    def from_lease(cls, lease: AgentRunLease) -> "RetryScheduleIdentity":
        return cls(
            tenant_id=lease.tenant_id,
            project_id=lease.project_id,
            run_id=lease.run_id,
            generation=lease.generation,
            attempt=lease.attempt,
        )

    @property
    def key(self) -> str:
        return _canonical_digest(
            {
                "kind": "agent_run.retry.schedule",
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "run_id": self.run_id,
                "generation": self.generation,
                "attempt": self.attempt,
            }
        )


# --------------------------------------------------------------------------- #
# Retry policy
# --------------------------------------------------------------------------- #


class RetryPlanStatus(StrEnum):
    SCHEDULED = "scheduled"
    EXHAUSTED = "exhausted"
    TERMINAL = "terminal"
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True, slots=True)
class RetryPlan:
    status: RetryPlanStatus
    classification: AgentRunFailureClassification
    attempt: int
    evaluated_at: datetime
    retry_at: datetime | None
    next_attempt: int | None
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, RetryPlanStatus):
            raise ValueError("status must be a RetryPlanStatus")
        if not isinstance(self.classification, AgentRunFailureClassification):
            raise ValueError("classification must be an AgentRunFailureClassification")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        _aware(self.evaluated_at, "evaluated_at")
        _reason_code(self.reason_code)
        if self.status is RetryPlanStatus.SCHEDULED:
            if self.retry_at is None or self.next_attempt != self.attempt + 1:
                raise ValueError("a scheduled retry requires retry_at and the next attempt")
            _aware(self.retry_at, "retry_at")
            if self.retry_at <= self.evaluated_at:
                raise ValueError("retry_at must be after the evaluated failure time")
            if not self.classification.retryable:
                raise ValueError("only a retryable classification can be scheduled")
        else:
            if self.retry_at is not None or self.next_attempt is not None:
                raise ValueError("a non-scheduled plan cannot carry a retry time or next attempt")

    @property
    def scheduled(self) -> bool:
        return self.status is RetryPlanStatus.SCHEDULED


@dataclass(frozen=True, slots=True)
class AgentRunRetryPolicy:
    """Deterministic, replayable exponential backoff for Agent Run attempts."""

    max_attempts: int = 5
    initial_delay_seconds: int = 10
    max_delay_seconds: int = 900
    jitter_basis_points: int = 2000

    def __post_init__(self) -> None:
        for name in ("max_attempts", "initial_delay_seconds", "max_delay_seconds", "jitter_basis_points"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_delay_seconds < 1:
            raise ValueError("initial_delay_seconds must be positive")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be at least the initial delay")
        if self.max_delay_seconds > _MAX_CONFIGURED_DELAY_SECONDS:
            raise ValueError("max_delay_seconds exceeds the supported range")
        if not 0 <= self.jitter_basis_points <= 10_000:
            raise ValueError("jitter_basis_points must be between 0 and 10000")

    def plan(
        self,
        *,
        identity: RetryScheduleIdentity,
        evidence: AgentRunFailureEvidence,
        failed_at: datetime,
        deadline: datetime | None = None,
        provider_retry_at: datetime | None = None,
    ) -> RetryPlan:
        """Return the retry plan for one failed attempt; writes nothing."""
        _aware(failed_at, "failed_at")
        if deadline is not None:
            _aware(deadline, "deadline")
            if deadline <= failed_at:
                raise ValueError("retry deadline must be after the failure")
        if provider_retry_at is not None:
            _aware(provider_retry_at, "provider_retry_at")

        classification = classify_agent_run_failure(evidence)
        if classification.failure_class is AgentRunFailureClass.RECONCILE_REQUIRED:
            return RetryPlan(
                status=RetryPlanStatus.RECONCILE_REQUIRED,
                classification=classification,
                attempt=identity.attempt,
                evaluated_at=failed_at,
                retry_at=None,
                next_attempt=None,
                reason_code=RETRY_REASON_RECONCILE_REQUIRED,
            )
        if classification.failure_class is AgentRunFailureClass.TERMINAL:
            return RetryPlan(
                status=RetryPlanStatus.TERMINAL,
                classification=classification,
                attempt=identity.attempt,
                evaluated_at=failed_at,
                retry_at=None,
                next_attempt=None,
                reason_code=RETRY_REASON_TERMINAL,
            )
        if identity.attempt >= self.max_attempts:
            return RetryPlan(
                status=RetryPlanStatus.EXHAUSTED,
                classification=classification,
                attempt=identity.attempt,
                evaluated_at=failed_at,
                retry_at=None,
                next_attempt=None,
                reason_code=RETRY_REASON_EXHAUSTED,
            )

        delay = self.jittered_delay(identity=identity)
        if provider_retry_at is not None:
            hint = provider_retry_at - failed_at
            delay = max(delay, min(hint, timedelta(seconds=self.max_delay_seconds)))

        retry_at = failed_at + delay
        if deadline is not None and retry_at > deadline:
            return RetryPlan(
                status=RetryPlanStatus.EXHAUSTED,
                classification=classification,
                attempt=identity.attempt,
                evaluated_at=failed_at,
                retry_at=None,
                next_attempt=None,
                reason_code=RETRY_REASON_DEADLINE_EXCEEDED,
            )
        return RetryPlan(
            status=RetryPlanStatus.SCHEDULED,
            classification=classification,
            attempt=identity.attempt,
            evaluated_at=failed_at,
            retry_at=retry_at,
            next_attempt=identity.attempt + 1,
            reason_code=RETRY_REASON_SCHEDULED,
        )

    def jittered_delay(self, *, identity: RetryScheduleIdentity) -> timedelta:
        """Stable delay: identical replay yields an identical instant."""
        attempt = identity.attempt
        if attempt - 1 >= 32:
            base_seconds = self.max_delay_seconds
        else:
            base_seconds = min(self.max_delay_seconds, self.initial_delay_seconds * (1 << (attempt - 1)))
        spread = base_seconds * self.jitter_basis_points // 10_000
        if spread:
            material = f"agent-run-retry-v1\x00{identity.key}\x00{attempt}".encode("utf-8")
            sample = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            offset = sample % (2 * spread + 1) - spread
        else:
            offset = 0
        seconds = max(1, min(self.max_delay_seconds, base_seconds + offset))
        return timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# Durable schedule values
# --------------------------------------------------------------------------- #


class RetryScheduleState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RELEASED = "released"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {RetryScheduleState.RELEASED, RetryScheduleState.CANCELLED}


@dataclass(frozen=True, slots=True)
class RetryScheduleRecord:
    """One durable retry row; mirrors the published table contract 1:1."""

    schedule_id: str
    tenant_id: str
    project_id: str
    run_id: str
    run_generation: int
    run_attempt: int
    retry_at: datetime
    failure_code: str
    schedule_key: str
    schedule_digest: str
    state: RetryScheduleState
    created_at: datetime
    updated_at: datetime
    claim_token: str | None = None
    claim_generation: int = 0
    lease_until: datetime | None = None
    released_at: datetime | None = None
    cancelled_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schedule_id", _required(self.schedule_id, "schedule_id", max_length=64))
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=40))
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id", max_length=40))
        if self.run_generation < 1 or self.run_attempt < 1:
            raise ValueError("run_generation and run_attempt must be positive")
        _aware(self.retry_at, "retry_at")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        _error_code(self.failure_code)
        _valid_digest(self.schedule_key, "schedule_key")
        _valid_digest(self.schedule_digest, "schedule_digest")
        if not isinstance(self.state, RetryScheduleState):
            raise ValueError("state must be a RetryScheduleState")
        if not isinstance(self.claim_generation, int) or isinstance(self.claim_generation, bool):
            raise ValueError("claim_generation must be an integer")
        if self.claim_generation < 0:
            raise ValueError("claim_generation cannot be negative")
        if self.state is RetryScheduleState.CLAIMED:
            if not self.claim_token or self.lease_until is None or self.claim_generation < 1:
                raise ValueError("a claimed schedule requires a token, lease and positive generation")
            _aware(self.lease_until, "lease_until")
        else:
            if self.claim_token is not None or self.lease_until is not None:
                raise ValueError("only a claimed schedule may carry claim fields")
        if self.state is RetryScheduleState.RELEASED:
            if self.released_at is None:
                raise ValueError("a released schedule requires released_at")
            _aware(self.released_at, "released_at")
        elif self.released_at is not None:
            raise ValueError("only a released schedule may carry released_at")
        if self.state is RetryScheduleState.CANCELLED:
            if self.cancelled_reason is None:
                raise ValueError("a cancelled schedule requires a redacted reason")
            _reason_code(self.cancelled_reason)
        elif self.cancelled_reason is not None:
            raise ValueError("only a cancelled schedule may carry a cancellation reason")

    @property
    def identity(self) -> RetryScheduleIdentity:
        return RetryScheduleIdentity(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            run_id=self.run_id,
            generation=self.run_generation,
            attempt=self.run_attempt,
        )


@dataclass(frozen=True, slots=True)
class RetryScheduleClaim:
    """An exclusive, leased claim on one due schedule row."""

    record: RetryScheduleRecord
    claim_token: str
    claim_generation: int
    lease_until: datetime
    claimed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.record, RetryScheduleRecord):
            raise ValueError("record must be a RetryScheduleRecord")
        if self.record.state is not RetryScheduleState.CLAIMED:
            raise ValueError("a claim must reference a claimed row")
        object.__setattr__(self, "claim_token", _required(self.claim_token, "claim_token", max_length=128))
        if self.claim_token != self.record.claim_token:
            raise ValueError("claim token does not match the claimed row")
        if self.claim_generation != self.record.claim_generation or self.claim_generation < 1:
            raise ValueError("claim generation does not match the claimed row")
        _aware(self.claimed_at, "claimed_at")
        _aware(self.lease_until, "lease_until")
        if self.record.lease_until != self.lease_until:
            raise ValueError("claim lease does not match the claimed row")
        if self.lease_until <= self.claimed_at:
            raise ValueError("claim lease must expire after it was taken")

    @property
    def tenant_id(self) -> str:
        return self.record.tenant_id


class RetryReleaseDecision(StrEnum):
    #: The run was made eligible again; the schedule becomes ``released``.
    RELEASED = "released"
    #: The run already reached a terminal state; the schedule is cancelled.
    RUN_TERMINAL = "run_terminal"
    #: The run cannot be released yet; keep the claim until its lease expires.
    NOT_ELIGIBLE = "not_eligible"


class RetryReleasePort(Protocol):
    async def release(self, *, claim: RetryScheduleClaim, now: datetime) -> RetryReleaseDecision:
        """Re-queue the claimed run for its next attempt (dispatcher-owned).

        Implementations must fence on the run's current generation and status
        inside the same durable transaction that requeues it, and must return
        ``RUN_TERMINAL`` instead of requeueing a finished run.
        """
        ...


class AgentRunRetryJournalPort(Protocol):
    """Durable retry journal.

    Implementations must, inside one database transaction per method:

    * enforce uniqueness of ``schedule_key`` and compare ``schedule_digest`` on
      conflict (identical replay returns the stored row, changed digest raises
      :class:`AgentRunRetryScheduleConflict`);
    * select due rows only for the requested tenant, using
      ``SELECT ... FOR UPDATE SKIP LOCKED`` ordered by ``retry_at``;
    * treat a ``claimed`` row whose ``lease_until <= now`` as due again, so a
      crash between claim and completion cannot lose the retry;
    * increment ``claim_generation`` and rotate ``claim_token`` on every claim;
    * compare-and-set ``(schedule_id, state, claim_token, claim_generation)``
      when recording an outcome, returning ``False`` instead of raising when the
      claim was lost to a peer.
    """

    async def insert_schedule_once(
        self, *, record: RetryScheduleRecord
    ) -> tuple[RetryScheduleRecord, bool]:
        """Insert one schedule per attempt identity; return ``(winner, inserted)``."""
        ...

    async def claim_due(
        self,
        *,
        tenant_id: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> tuple[RetryScheduleClaim, ...]:
        ...

    async def record_release(self, *, claim: RetryScheduleClaim, released_at: datetime) -> bool:
        ...

    async def record_cancel(self, *, claim: RetryScheduleClaim, reason_code: str, cancelled_at: datetime) -> bool:
        ...

    async def cancel_pending_for_run(
        self, *, tenant_id: str, run_id: str, reason_code: str, cancelled_at: datetime
    ) -> int:
        """Cancel not-yet-claimed schedules of one run; returns the row count."""
        ...


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #


class RetryScheduleStatus(StrEnum):
    SCHEDULED = "scheduled"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class RetryScheduleOutcome:
    status: RetryScheduleStatus
    record: RetryScheduleRecord
    plan: RetryPlan

    def __post_init__(self) -> None:
        if not isinstance(self.status, RetryScheduleStatus):
            raise ValueError("status must be a RetryScheduleStatus")
        if not isinstance(self.record, RetryScheduleRecord):
            raise ValueError("record must be a RetryScheduleRecord")
        if not isinstance(self.plan, RetryPlan):
            raise ValueError("plan must be a RetryPlan")
        if not self.plan.scheduled:
            raise ValueError("only a scheduled plan can produce a schedule outcome")
        if self.record.schedule_key != self.record.identity.key:
            raise ValueError("schedule key does not match the record identity")
        if self.record.retry_at != self.plan.retry_at:
            raise ValueError("stored retry_at does not match the plan")


@dataclass(frozen=True, slots=True)
class RetryDispatchReport:
    claimed: int
    released: int
    cancelled: int
    deferred: int

    def __post_init__(self) -> None:
        if min(self.claimed, self.released, self.cancelled, self.deferred) < 0:
            raise ValueError("dispatch counters cannot be negative")
        if self.released + self.cancelled + self.deferred != self.claimed:
            raise ValueError("every claim must be released, cancelled or deferred")


def _schedule_digest(*, identity: RetryScheduleIdentity, plan: RetryPlan) -> str:
    if plan.retry_at is None:  # pragma: no cover - guarded by the caller
        raise ValueError("a schedule digest requires a scheduled plan")
    return _canonical_digest(
        {
            "kind": "agent_run.retry.schedule.row",
            "policy": "agent_run.retry.v1",
            "identity": identity.key,
            "retry_at": plan.retry_at.isoformat(),
            "failure_code": plan.classification.error_code,
        }
    )


def _schedule_id(identity: RetryScheduleIdentity) -> str:
    # Deterministic: a replay addresses the same row without a read-before-write.
    digest = hashlib.sha256(identity.key.encode("utf-8")).hexdigest()
    return f"arr_{digest[:32]}"


def build_retry_schedule_record(
    *,
    identity: RetryScheduleIdentity,
    plan: RetryPlan,
    failed_at: datetime,
) -> RetryScheduleRecord:
    """Materialise the pending row for one scheduled plan (no side effects)."""
    _aware(failed_at, "failed_at")
    if not plan.scheduled:
        raise AgentRunRetryRefused(plan.reason_code, "only a scheduled plan has a durable retry row")
    return RetryScheduleRecord(
        schedule_id=_schedule_id(identity),
        tenant_id=identity.tenant_id,
        project_id=identity.project_id,
        run_id=identity.run_id,
        run_generation=identity.generation,
        run_attempt=identity.attempt,
        retry_at=plan.retry_at,  # type: ignore[arg-type]
        failure_code=plan.classification.error_code,
        schedule_key=identity.key,
        schedule_digest=_schedule_digest(identity=identity, plan=plan),
        state=RetryScheduleState.PENDING,
        created_at=failed_at,
        updated_at=failed_at,
    )


class AgentRunRetryScheduler:
    """Durable retry planning and dispatch; contains no sleeping loop."""

    def __init__(
        self,
        *,
        journal: AgentRunRetryJournalPort | None,
        scope: TenantScope,
        policy: AgentRunRetryPolicy | None = None,
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
        self._scope = scope
        self._policy = policy or AgentRunRetryPolicy()
        self._claim_lease = claim_lease
        self._max_batch = max_batch

    @property
    def is_wired(self) -> bool:
        return self._journal is not None

    @property
    def wiring_blocker(self) -> str | None:
        return None if self.is_wired else WIRING_BLOCKER

    def plan_retry(
        self,
        *,
        identity: RetryScheduleIdentity,
        evidence: AgentRunFailureEvidence,
        failed_at: datetime,
        deadline: datetime | None = None,
        provider_retry_at: datetime | None = None,
    ) -> RetryPlan:
        self._scope.require(identity.tenant_id)
        return self._policy.plan(
            identity=identity,
            evidence=evidence,
            failed_at=failed_at,
            deadline=deadline,
            provider_retry_at=provider_retry_at,
        )

    async def schedule_retry(
        self,
        *,
        identity: RetryScheduleIdentity,
        evidence: AgentRunFailureEvidence,
        failed_at: datetime,
        deadline: datetime | None = None,
        provider_retry_at: datetime | None = None,
    ) -> RetryScheduleOutcome:
        """Persist one durable ``retry_at`` for a proven retryable failure.

        Raises :class:`AgentRunRetryRefused` for a terminal failure, an unknown
        outcome, an exhausted attempt budget or a missed deadline — in which case
        nothing is written.
        """
        journal = self._require_journal()
        plan = self.plan_retry(
            identity=identity,
            evidence=evidence,
            failed_at=failed_at,
            deadline=deadline,
            provider_retry_at=provider_retry_at,
        )
        if not plan.scheduled:
            raise AgentRunRetryRefused(
                plan.reason_code,
                "this provider attempt cannot be scheduled for retry",
            )
        if plan.retry_at is None:  # pragma: no cover - guarded by RetryPlan
            raise AgentRunRetryRefused(RETRY_REASON_TERMINAL, "retry plan is not schedulable")

        record = build_retry_schedule_record(identity=identity, plan=plan, failed_at=failed_at)
        stored, inserted = await journal.insert_schedule_once(record=record)
        if stored.tenant_id != identity.tenant_id or stored.schedule_key != identity.key:
            raise TenantScopeViolation("retry journal returned a schedule outside the requested identity")
        return RetryScheduleOutcome(
            status=RetryScheduleStatus.SCHEDULED if inserted else RetryScheduleStatus.DUPLICATE,
            record=stored,
            plan=plan,
        )

    async def schedule_from_result(
        self,
        *,
        lease: AgentRunLease,
        result: ProviderExecutionResult,
        evidence: AgentRunFailureEvidence,
        failed_at: datetime,
        deadline: datetime | None = None,
    ) -> RetryScheduleOutcome:
        """Schedule a retry for one worker result.

        The execution port already forbids an unknown outcome (or a retryable
        failure without ``retry_at``); this method re-asserts that contract so a
        blind retry cannot be created from this path either.
        """
        retry_at = validate_retry_result(result)
        identity = RetryScheduleIdentity.from_lease(lease)
        if result.outcome is not ProviderOutcome.RETRYABLE_FAILURE:  # pragma: no cover - guarded above
            raise AgentRunRetryRefused(RETRY_REASON_TERMINAL, "only retryable failures may be scheduled")
        return await self.schedule_retry(
            identity=identity,
            evidence=evidence,
            failed_at=failed_at,
            deadline=deadline,
            provider_retry_at=retry_at,
        )

    async def dispatch_due(
        self,
        *,
        tenant_id: str,
        now: datetime,
        release: RetryReleasePort,
        limit: int | None = None,
    ) -> RetryDispatchReport:
        """Release matured retries back to the dispatcher for one tenant.

        Bounded by ``limit`` and never loops or sleeps: the caller decides when
        to run the next pass.  A release that cannot proceed leaves the row
        claimed, so the lease expiry makes it due again.
        """
        journal = self._require_journal()
        tenant = self._scope.require(tenant_id)
        _aware(now, "now")
        batch = self._max_batch if limit is None else limit
        if not isinstance(batch, int) or isinstance(batch, bool) or not 1 <= batch <= self._max_batch:
            raise ValueError("limit must be between 1 and the configured batch size")

        claims = await journal.claim_due(
            tenant_id=tenant,
            now=now,
            lease_until=now + self._claim_lease,
            limit=batch,
        )
        if len(claims) > batch:
            raise AgentRunRetryBlocked(
                RETRY_JOURNAL_BLOCKER,
                "retry journal returned more claims than requested",
            )

        released = cancelled = deferred = 0
        # Validate the whole batch before acting: a foreign claim must abort the
        # pass before any tenant-local run is requeued.
        if any(claim.record.tenant_id != tenant for claim in claims):
            raise TenantScopeViolation("retry journal returned a claim outside the requested tenant")
        for claim in claims:
            decision = await release.release(claim=claim, now=now)
            if decision is RetryReleaseDecision.RELEASED:
                if await journal.record_release(claim=claim, released_at=now):
                    released += 1
                else:
                    deferred += 1
            elif decision is RetryReleaseDecision.RUN_TERMINAL:
                if await journal.record_cancel(
                    claim=claim,
                    reason_code=RETRY_REASON_RUN_TERMINAL,
                    cancelled_at=now,
                ):
                    cancelled += 1
                else:
                    deferred += 1
            else:
                deferred += 1
        return RetryDispatchReport(
            claimed=len(claims),
            released=released,
            cancelled=cancelled,
            deferred=deferred,
        )

    async def cancel_for_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        reason_code: str,
        cancelled_at: datetime,
    ) -> int:
        """Cancel schedules of a run that will not be retried (terminal/quarantined)."""
        journal = self._require_journal()
        tenant = self._scope.require(tenant_id)
        _aware(cancelled_at, "cancelled_at")
        return await journal.cancel_pending_for_run(
            tenant_id=tenant,
            run_id=_required(run_id, "run_id", max_length=40),
            reason_code=_reason_code(reason_code),
            cancelled_at=cancelled_at,
        )

    def _require_journal(self) -> AgentRunRetryJournalPort:
        if self._journal is None:
            raise AgentRunRetryBlocked(
                WIRING_BLOCKER,
                "no durable retry journal adapter is wired; retries cannot be scheduled",
            )
        return self._journal


# --------------------------------------------------------------------------- #
# SQL adapter (authored against the published table contract, no DDL here)
# --------------------------------------------------------------------------- #

# Explicit column types are required: an untyped Column would make asyncpg
# unable to infer bind parameter types for the due-selection predicates.
_retry_schedules = table(
    RETRY_SCHEDULE_TABLE,
    column("schedule_id", String(64), primary_key=True),
    column("tenant_id", String(64), nullable=False),
    column("project_id", String(40), nullable=False),
    column("run_id", String(40), nullable=False),
    column("run_generation", Integer, nullable=False),
    column("run_attempt", Integer, nullable=False),
    column("retry_at", DateTime(timezone=True), nullable=False),
    column("failure_code", String(128), nullable=False),
    column("schedule_key", String(71), nullable=False),
    column("schedule_digest", String(71), nullable=False),
    column("state", String(24), nullable=False),
    column("claim_token", String(128), nullable=True),
    column("claim_generation", Integer, nullable=False),
    column("lease_until", DateTime(timezone=True), nullable=True),
    column("released_at", DateTime(timezone=True), nullable=True),
    column("cancelled_reason", String(128), nullable=True),
    column("created_at", DateTime(timezone=True), nullable=False),
    column("updated_at", DateTime(timezone=True), nullable=False),
)


def _record_from_mapping(row: Mapping[str, Any]) -> RetryScheduleRecord:
    def stamp(name: str) -> datetime:
        value = row[name]
        return _aware(value, name)

    return RetryScheduleRecord(
        schedule_id=row["schedule_id"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        run_generation=int(row["run_generation"]),
        run_attempt=int(row["run_attempt"]),
        retry_at=stamp("retry_at"),
        failure_code=row["failure_code"],
        schedule_key=row["schedule_key"],
        schedule_digest=row["schedule_digest"],
        state=RetryScheduleState(row["state"]),
        created_at=stamp("created_at"),
        updated_at=stamp("updated_at"),
        claim_token=row["claim_token"],
        claim_generation=int(row["claim_generation"] or 0),
        lease_until=stamp("lease_until") if row["lease_until"] is not None else None,
        released_at=stamp("released_at") if row["released_at"] is not None else None,
        cancelled_reason=row["cancelled_reason"],
    )


def _assert_same_schedule(stored: RetryScheduleRecord, requested: RetryScheduleRecord) -> None:
    if (
        stored.schedule_key != requested.schedule_key
        or stored.schedule_digest != requested.schedule_digest
        or stored.tenant_id != requested.tenant_id
        or stored.run_id != requested.run_id
        or stored.run_generation != requested.run_generation
        or stored.run_attempt != requested.run_attempt
    ):
        raise AgentRunRetryScheduleConflict(
            "retry schedule identity is already bound to a different decision"
        )


class SqlAlchemyAgentRunRetryJournal:
    """Durable journal on ``agent_run_retry_schedules``.

    The table is addressed through a metadata-free ``TableClause``: this module
    must not register ORM metadata or emit DDL, so schema ownership stays with
    the persistence task.  Until the table exists, :meth:`ensure_ready` raises
    ``agent_run_retry_schedules_table_missing`` instead of degrading.
    """

    def __init__(self, *, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def ensure_ready(self) -> None:
        from sqlalchemy.exc import ProgrammingError

        try:
            async with self._session_factory() as session:
                await session.execute(select(_retry_schedules.c.schedule_id).limit(1))
        except ProgrammingError as exc:
            raise AgentRunRetryBlocked(
                RETRY_TABLE_BLOCKER,
                "agent_run_retry_schedules is not present; retry scheduling stays blocked",
            ) from exc

    async def insert_schedule_once(
        self, *, record: RetryScheduleRecord
    ) -> tuple[RetryScheduleRecord, bool]:
        from sqlalchemy.exc import IntegrityError

        async with self._session_factory() as session, session.begin():
            existing = (
                await session.execute(
                    select(_retry_schedules)
                    .where(_retry_schedules.c.schedule_key == record.schedule_key)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if existing is not None:
                stored = _record_from_mapping(existing)
                _assert_same_schedule(stored, record)
                return stored, False
            try:
                async with session.begin_nested():
                    await session.execute(
                        insert(_retry_schedules).values(
                            schedule_id=record.schedule_id,
                            tenant_id=record.tenant_id,
                            project_id=record.project_id,
                            run_id=record.run_id,
                            run_generation=record.run_generation,
                            run_attempt=record.run_attempt,
                            retry_at=record.retry_at,
                            failure_code=record.failure_code,
                            schedule_key=record.schedule_key,
                            schedule_digest=record.schedule_digest,
                            state=record.state.value,
                            claim_token=None,
                            claim_generation=0,
                            lease_until=None,
                            released_at=None,
                            cancelled_reason=None,
                            created_at=record.created_at,
                            updated_at=record.updated_at,
                        )
                    )
            except IntegrityError as exc:
                winner = (
                    await session.execute(
                        select(_retry_schedules).where(
                            _retry_schedules.c.schedule_key == record.schedule_key
                        )
                    )
                ).mappings().one_or_none()
                if winner is None:
                    raise
                stored = _record_from_mapping(winner)
                _assert_same_schedule(stored, record)
                return stored, False
            return record, True

    async def claim_due(
        self,
        *,
        tenant_id: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> tuple[RetryScheduleClaim, ...]:
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        if lease_until <= now:
            raise ValueError("lease_until must be after now")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        due = and_(
            _retry_schedules.c.tenant_id == tenant,
            or_(
                and_(
                    _retry_schedules.c.state == RetryScheduleState.PENDING.value,
                    _retry_schedules.c.retry_at <= now,
                ),
                and_(
                    _retry_schedules.c.state == RetryScheduleState.CLAIMED.value,
                    _retry_schedules.c.lease_until <= now,
                ),
            ),
        )
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    select(_retry_schedules)
                    .where(due)
                    .order_by(_retry_schedules.c.retry_at, _retry_schedules.c.schedule_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).mappings().all()

            claims: list[RetryScheduleClaim] = []
            for row in rows:
                token = uuid.uuid4().hex
                generation = int(row["claim_generation"] or 0) + 1
                result = await session.execute(
                    update(_retry_schedules)
                    .where(
                        _retry_schedules.c.schedule_id == row["schedule_id"],
                        _retry_schedules.c.state == row["state"],
                        _retry_schedules.c.claim_generation == row["claim_generation"],
                    )
                    .values(
                        state=RetryScheduleState.CLAIMED.value,
                        claim_token=token,
                        claim_generation=generation,
                        lease_until=lease_until,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:  # pragma: no cover - row is locked above
                    continue
                claimed = dict(row)
                claimed.update(
                    {
                        "state": RetryScheduleState.CLAIMED.value,
                        "claim_token": token,
                        "claim_generation": generation,
                        "lease_until": lease_until,
                        "updated_at": now,
                    }
                )
                claims.append(
                    RetryScheduleClaim(
                        record=_record_from_mapping(claimed),
                        claim_token=token,
                        claim_generation=generation,
                        lease_until=lease_until,
                        claimed_at=now,
                    )
                )
            return tuple(claims)

    async def record_release(self, *, claim: RetryScheduleClaim, released_at: datetime) -> bool:
        _aware(released_at, "released_at")
        return await self._close_claim(
            claim=claim,
            values={
                "state": RetryScheduleState.RELEASED.value,
                "released_at": released_at,
                "lease_until": None,
                "updated_at": released_at,
            },
        )

    async def record_cancel(
        self, *, claim: RetryScheduleClaim, reason_code: str, cancelled_at: datetime
    ) -> bool:
        _aware(cancelled_at, "cancelled_at")
        return await self._close_claim(
            claim=claim,
            values={
                "state": RetryScheduleState.CANCELLED.value,
                "cancelled_reason": _reason_code(reason_code),
                "lease_until": None,
                "updated_at": cancelled_at,
            },
        )

    async def cancel_pending_for_run(
        self, *, tenant_id: str, run_id: str, reason_code: str, cancelled_at: datetime
    ) -> int:
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        run = _required(run_id, "run_id", max_length=40)
        _aware(cancelled_at, "cancelled_at")
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(_retry_schedules)
                .where(
                    _retry_schedules.c.tenant_id == tenant,
                    _retry_schedules.c.run_id == run,
                    _retry_schedules.c.state == RetryScheduleState.PENDING.value,
                )
                .values(
                    state=RetryScheduleState.CANCELLED.value,
                    cancelled_reason=_reason_code(reason_code),
                    updated_at=cancelled_at,
                )
            )
            return int(result.rowcount or 0)

    async def _close_claim(self, *, claim: RetryScheduleClaim, values: Mapping[str, Any]) -> bool:
        if not isinstance(claim, RetryScheduleClaim):
            raise ValueError("claim must be a RetryScheduleClaim")
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(_retry_schedules)
                .where(
                    _retry_schedules.c.schedule_id == claim.record.schedule_id,
                    _retry_schedules.c.tenant_id == claim.tenant_id,
                    _retry_schedules.c.state == RetryScheduleState.CLAIMED.value,
                    _retry_schedules.c.claim_token == claim.claim_token,
                    _retry_schedules.c.claim_generation == claim.claim_generation,
                )
                .values(**dict(values))
            )
            return result.rowcount == 1


__all__ = [
    "AgentRunFailureCategory",
    "AgentRunFailureClass",
    "AgentRunFailureClassification",
    "AgentRunFailureEvidence",
    "AgentRunRetryBlocked",
    "AgentRunRetryError",
    "AgentRunRetryJournalPort",
    "AgentRunRetryPolicy",
    "AgentRunRetryRefused",
    "AgentRunRetryScheduleConflict",
    "AgentRunRetryScheduler",
    "RETRY_BLOCKERS",
    "RETRY_JOURNAL_BLOCKER",
    "RETRY_REASON_CANCELLED",
    "RETRY_REASON_DEADLINE_EXCEEDED",
    "RETRY_REASON_EXHAUSTED",
    "RETRY_REASON_QUARANTINED",
    "RETRY_REASON_RECONCILE_REQUIRED",
    "RETRY_REASON_RUN_TERMINAL",
    "RETRY_REASON_SCHEDULED",
    "RETRY_REASON_TERMINAL",
    "RETRY_SCHEDULE_REQUIRED_COLUMNS",
    "RETRY_SCHEDULE_TABLE",
    "RETRY_TABLE_BLOCKER",
    "RetryDispatchReport",
    "RetryPlan",
    "RetryPlanStatus",
    "RetryReleaseDecision",
    "RetryReleasePort",
    "RetryScheduleClaim",
    "RetryScheduleIdentity",
    "RetryScheduleOutcome",
    "RetryScheduleRecord",
    "RetryScheduleState",
    "RetryScheduleStatus",
    "SqlAlchemyAgentRunRetryJournal",
    "TenantScope",
    "TenantScopeViolation",
    "WIRING_BLOCKER",
    "build_retry_schedule_record",
    "classify_agent_run_failure",
]
