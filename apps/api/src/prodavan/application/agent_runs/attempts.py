"""Durable provider-attempt journal for Agent Runs.

Why this module exists
----------------------
A fenced lease says *which* worker owns a run.  It does not answer the question
that actually decides whether a run may be retried: **did the provider call
already happen, and what did it answer?**  Without a durable journal, a worker
that crashes or loses its lease between "we called the model" and "we wrote the
result" forces a choice between two wrong answers — blindly retry (duplicate
side effects, duplicate spend) or blindly succeed (silently losing the answer).

This module owns that journal as a *contract plus pure policy*:

* every provider call gets a deterministic idempotency key derived from
  ``(tenant, run, generation, attempt)`` — a retried HTTP request, a re-delivered
  queue message or a second worker in the same generation resolves to the same
  attempt instead of a second call;
* a new claim increments the generation, therefore a new claim is a *new*
  attempt key, and the old attempt keeps its own record;
* the attempt records a bounded, redacted metadata digest set — never a
  prompt, a completion, a credential or a raw payload;
* ``unknown`` is a first-class terminal-for-now state: an ambiguous provider
  outcome can only leave ``unknown`` through an explicit reconciliation write,
  never through a plain "mark succeeded";
* a worker whose lease was superseded records ``discarded_stale`` — its result
  is evidence, not state.

Storage is an abstraction (:class:`ProviderAttemptJournalPort`) because the
durable table does not exist yet; see the blocker note below.  A host without a
journal must raise :class:`AttemptJournalUnavailable` instead of falling back to
process memory.

Blockers (fixed in the task report; no ORM/migration is authored here)
----------------------------------------------------------------------
``ATTEMPT_TABLE_BLOCKER`` — no ``agent_control_plane_provider_attempts`` table
and no Alembic revision exist.  The infrastructure zone must add it with:

``attempt_id`` PK, ``idempotency_key`` UNIQUE, ``tenant_id``/``project_id``/
``run_id`` (FK ``agent_runs.id`` cascade), ``session_id``, ``generation``,
``attempt``, ``sequence``, ``provider``, ``model_digest``, ``request_digest``,
``prompt_digest``, ``lease_token_digest``, ``state``, ``started_at``,
``deadline_at``, ``finished_at``, ``failure_code``, ``provider_request_id``,
``metadata`` JSONB, ``created_at``, plus indexes ``(run_id, generation)`` and
``(state, started_at)`` for the reconciler's unresolved scan, and a CHECK that a
terminal state implies ``finished_at`` while a non-terminal state forbids it.
The port must implement ``insert_start`` as an idempotent insert (same key with
a different ``request_digest`` is a conflict) and ``cas_finish`` as a single
``UPDATE ... WHERE attempt_id = ? AND state = ?`` returning the row count.

``ATTEMPT_JOURNAL_WRITE_ATOMICITY`` — the port contract cannot co-commit the
journal row with the run's terminal transition (no such port method exists).
The worker therefore commits the run transition first and the journal second;
if the journal write then fails, the attempt stays ``started`` and the next
claim refuses to execute until reconciliation.  That is fail-closed but leaves a
window the future port method (see report) must close.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from .execution import AgentRunLease, ProviderExecutionResult, ProviderOutcome
from .lease import CasUpdate

ATTEMPT_JOURNAL_BLOCKER = "agent_run_provider_attempt_journal_unwired"
"""Blocker while no durable attempt journal adapter is wired."""

ATTEMPT_TABLE_BLOCKER = "agent_run_provider_attempt_table_missing"
"""Blocker while the durable attempt table/revision is absent."""

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

MAX_REDACTED_METADATA_KEYS = 12
MAX_METADATA_TEXT = 200

REDACTED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "provider_request_id_digest",
        "runtime_ref_digest",
        "provider_status",
        "http_status",
        "latency_ms",
        "event_count",
        "assistant_text_digest",
        "error_class",
        "error_code",
        "retry_after_seconds",
        "bridge_event_type",
        "stall_retry",
        "phase",
    }
)
"""Allow-list of metadata keys that may be persisted in the journal."""

FORBIDDEN_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "content",
        "credential",
        "credentials",
        "input",
        "input_payload",
        "lease_token",
        "message",
        "messages",
        "output",
        "output_payload",
        "password",
        "prompt",
        "raw",
        "secret",
        "system_prompt",
        "text",
        "token",
        "transcript",
    }
)
"""Keys that must never reach the journal, even if someone allow-lists them."""


class AttemptState(StrEnum):
    """Durable lifecycle of one provider attempt."""

    STARTED = "started"
    KNOWN_SUCCEEDED = "known_succeeded"
    KNOWN_FAILED = "known_failed"
    KNOWN_RETRYABLE = "known_retryable"
    UNKNOWN = "unknown"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED = "reconciled_failed"
    ABANDONED = "abandoned"
    DISCARDED_STALE = "discarded_stale"


TERMINAL_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {
        AttemptState.KNOWN_SUCCEEDED,
        AttemptState.KNOWN_FAILED,
        AttemptState.KNOWN_RETRYABLE,
        AttemptState.RECONCILED_SUCCEEDED,
        AttemptState.RECONCILED_FAILED,
        AttemptState.ABANDONED,
        AttemptState.DISCARDED_STALE,
    }
)
"""States a caller may never leave again."""

CLOSED_ATTEMPT_STATES: frozenset[AttemptState] = TERMINAL_ATTEMPT_STATES | {
    AttemptState.UNKNOWN
}
# ``unknown`` is *closed for now*: the provider outcome was observed to be
# ambiguous at ``finished_at``.  It is not terminal — only reconciliation may
# move it, and no other transition may touch it.
UNRESOLVED_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {AttemptState.STARTED, AttemptState.UNKNOWN}
)
SUCCESS_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {AttemptState.KNOWN_SUCCEEDED, AttemptState.RECONCILED_SUCCEEDED}
)
FAILURE_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {
        AttemptState.KNOWN_FAILED,
        AttemptState.KNOWN_RETRYABLE,
        AttemptState.RECONCILED_FAILED,
        # A stale worker's evidence: the attempt did not fail, but it is recorded
        # with the reason it was discarded so the journal is self-explanatory.
        AttemptState.DISCARDED_STALE,
    }
)


class AttemptOutcome(StrEnum):
    """A requested attempt transition; distinct from the persisted state."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYABLE = "retryable"
    UNKNOWN = "unknown"
    ABANDONED = "abandoned"
    DISCARDED_STALE = "discarded_stale"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED = "reconciled_failed"


_TRANSITIONS: dict[tuple[AttemptState, AttemptOutcome], AttemptState] = {
    (AttemptState.STARTED, AttemptOutcome.SUCCEEDED): AttemptState.KNOWN_SUCCEEDED,
    (AttemptState.STARTED, AttemptOutcome.FAILED): AttemptState.KNOWN_FAILED,
    (AttemptState.STARTED, AttemptOutcome.RETRYABLE): AttemptState.KNOWN_RETRYABLE,
    (AttemptState.STARTED, AttemptOutcome.UNKNOWN): AttemptState.UNKNOWN,
    (AttemptState.STARTED, AttemptOutcome.ABANDONED): AttemptState.ABANDONED,
    (AttemptState.STARTED, AttemptOutcome.DISCARDED_STALE): AttemptState.DISCARDED_STALE,
    (AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_SUCCEEDED): AttemptState.RECONCILED_SUCCEEDED,
    (AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_FAILED): AttemptState.RECONCILED_FAILED,
}


class AttemptRefusal(StrEnum):
    """Stable, redacted reasons an attempt operation is refused."""

    ALREADY_TERMINAL = "agent_run_attempt_already_terminal"
    UNKNOWN_REQUIRES_RECONCILIATION = "agent_run_attempt_unknown_requires_reconciliation"
    ILLEGAL_TRANSITION = "agent_run_attempt_illegal_transition"
    DIGEST_CONFLICT = "agent_run_attempt_digest_conflict"
    INVALID_RECORD = "agent_run_attempt_invalid_record"
    UNRESOLVED_ATTEMPT_EXISTS = "agent_run_attempt_unresolved_exists"
    STORAGE_MISSING = ATTEMPT_JOURNAL_BLOCKER


class AttemptRejected(RuntimeError):
    """An attempt operation was refused; no journal or run write may follow."""

    def __init__(self, reason: AttemptRefusal, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


class AttemptConflict(AttemptRejected):
    """The same attempt key arrived with a different request digest."""

    def __init__(self, detail: str = "attempt key reused with a different request digest") -> None:
        super().__init__(AttemptRefusal.DIGEST_CONFLICT, detail)


class AttemptJournalUnavailable(RuntimeError):
    """No durable attempt journal exists; execution must fail closed."""

    def __init__(self, detail: str = "durable provider attempt journal is not wired") -> None:
        self.blocker = ATTEMPT_JOURNAL_BLOCKER
        super().__init__(detail)


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _required(value: str, name: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _digest(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(f"{name} must be a lowercase sha256 hex digest")
    return value


def canonical_digest(payload: Mapping[str, Any]) -> str:
    """Stable sha256 over a JSON-serialisable mapping (never stores the payload)."""
    encoded = json.dumps(
        _plain(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_digest(text: str | None) -> str | None:
    """Digest of the user prompt; the prompt itself must never be persisted."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("prompt text must be a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_digest(
    *,
    provider: str,
    model: str | None,
    session_id: str | None,
    attachments: int,
    prompt: str | None,
) -> str:
    """Digest of the request shape the provider was asked to serve."""
    return canonical_digest(
        {
            "provider": provider,
            "model": model,
            "session_id": session_id,
            "attachments": attachments,
            "prompt_digest": prompt_digest(prompt),
        }
    )


def lease_token_digest(token: str) -> str:
    """Digest of the lease token, so the journal never stores a usable token."""
    return hashlib.sha256(_required(token, "lease_token", max_length=512).encode("utf-8")).hexdigest()


def attempt_idempotency_key(
    *,
    tenant_id: str,
    run_id: str,
    generation: int,
    attempt: int,
) -> str:
    """Deterministic key for one provider attempt of one generation."""
    _required(tenant_id, "tenant_id", max_length=64)
    _required(run_id, "run_id", max_length=64)
    if generation < 1 or attempt < 1:
        raise ValueError("attempt key requires positive generation and attempt")
    digest = canonical_digest(
        {
            "kind": "agent_run_provider_attempt",
            "tenant_id": tenant_id,
            "run_id": run_id,
            "generation": generation,
            "attempt": attempt,
        }
    )
    return f"provider-attempt:{digest[:40]}"


def attempt_id_for(key: str) -> str:
    return f"pat_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:30]}"


def redact_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], int]:
    """Return (allow-listed redacted metadata, dropped key count).

    Unknown keys are dropped, forbidden keys are dropped even when they appear
    in the allow-list (defence in depth), and values are coerced to bounded
    JSON scalars.  Prompts, credentials and payloads therefore cannot reach the
    journal through this path.
    """
    if metadata is None:
        return MappingProxyType({}), 0
    if not isinstance(metadata, Mapping):
        raise AttemptRejected(AttemptRefusal.INVALID_RECORD, "attempt metadata must be a mapping")
    kept: dict[str, Any] = {}
    dropped = 0
    for key, value in metadata.items():
        name = str(key)
        if name in FORBIDDEN_METADATA_KEYS or name not in REDACTED_METADATA_KEYS:
            dropped += 1
            continue
        if value is None:
            dropped += 1
            continue
        if isinstance(value, bool):
            kept[name] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            kept[name] = value
        elif isinstance(value, float):
            kept[name] = value
        elif isinstance(value, str):
            kept[name] = value[:MAX_METADATA_TEXT]
        else:
            dropped += 1
            continue
        if len(kept) >= MAX_REDACTED_METADATA_KEYS:
            dropped += 1
    frozen = MappingProxyType(kept)
    _assert_secret_free(frozen)
    return frozen, dropped


def _assert_secret_free(metadata: Mapping[str, Any]) -> None:
    for key in metadata:
        if key in FORBIDDEN_METADATA_KEYS:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "attempt metadata contains a forbidden key",
            )


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """One durable provider attempt; contains no prompt, payload or secret."""

    attempt_id: str
    idempotency_key: str
    tenant_id: str
    project_id: str
    run_id: str
    generation: int
    attempt: int
    sequence: int
    provider: str
    request_digest: str
    lease_token_digest: str
    state: AttemptState
    started_at: datetime
    session_id: str | None = None
    model_digest: str | None = None
    prompt_digest: str | None = None
    deadline_at: datetime | None = None
    finished_at: datetime | None = None
    failure_code: str | None = None
    provider_request_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    metadata_dropped: int = 0

    def __post_init__(self) -> None:
        for name in ("attempt_id", "idempotency_key", "tenant_id", "project_id", "run_id", "provider"):
            object.__setattr__(self, name, _required(getattr(self, name), name, max_length=96))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _required(self.session_id, "session_id", max_length=64))
        if self.generation < 1 or self.attempt < 1 or self.sequence < 1:
            raise ValueError("attempt requires positive generation, attempt and sequence")
        if self.metadata_dropped < 0:
            raise ValueError("metadata_dropped cannot be negative")
        _aware(self.started_at, "started_at")
        if self.deadline_at is not None:
            _aware(self.deadline_at, "deadline_at")
        if self.finished_at is not None:
            _aware(self.finished_at, "finished_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at cannot precede started_at")
        _digest(self.request_digest, "request_digest")
        _digest(self.lease_token_digest, "lease_token_digest")
        if self.model_digest is not None:
            _digest(self.model_digest, "model_digest")
        if self.prompt_digest is not None:
            _digest(self.prompt_digest, "prompt_digest")
        if self.provider_request_id is not None:
            object.__setattr__(
                self, "provider_request_id", _required(self.provider_request_id, "provider_request_id", max_length=160)
            )
        expected_key = attempt_idempotency_key(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            generation=self.generation,
            attempt=self.attempt,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("attempt idempotency key is not stable for run/generation/attempt")
        if self.attempt_id != attempt_id_for(expected_key):
            raise ValueError("attempt id is not derived from its idempotency key")
        if self.state in CLOSED_ATTEMPT_STATES and self.finished_at is None:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "a closed attempt state (terminal or unknown) requires finished_at",
            )
        if self.state not in CLOSED_ATTEMPT_STATES and self.finished_at is not None:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "an open attempt state cannot carry finished_at",
            )
        if self.state in FAILURE_ATTEMPT_STATES:
            if self.failure_code is None:
                raise AttemptRejected(
                    AttemptRefusal.INVALID_RECORD,
                    "failed attempt requires a failure code",
                )
        if self.state in SUCCESS_ATTEMPT_STATES and self.failure_code is not None:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "successful attempt cannot carry a failure code",
            )
        if self.failure_code is not None and not _FAILURE_CODE.match(self.failure_code):
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "failure code must be a bounded machine code",
            )
        redacted, dropped = redact_metadata(self.metadata)
        object.__setattr__(self, "metadata", redacted)
        object.__setattr__(self, "metadata_dropped", self.metadata_dropped + dropped)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_ATTEMPT_STATES

    @property
    def is_unresolved(self) -> bool:
        return self.state in UNRESOLVED_ATTEMPT_STATES

    @property
    def is_success(self) -> bool:
        return self.state in SUCCESS_ATTEMPT_STATES


@dataclass(frozen=True, slots=True)
class AttemptStart:
    """Result of an idempotent attempt start."""

    attempt: ProviderAttempt
    created: bool

    def __post_init__(self) -> None:
        if not isinstance(self.created, bool):
            raise ValueError("created must be a bool")


def next_state(current: AttemptState, outcome: AttemptOutcome) -> AttemptState:
    """Pure transition rule; unknown outcomes can never jump to success."""
    if current in TERMINAL_ATTEMPT_STATES:
        raise AttemptRejected(
            AttemptRefusal.ALREADY_TERMINAL,
            f"attempt is already {current.value}",
        )
    if current is AttemptState.UNKNOWN and outcome in {
        AttemptOutcome.SUCCEEDED,
        AttemptOutcome.FAILED,
        AttemptOutcome.RETRYABLE,
    }:
        raise AttemptRejected(
            AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION,
            "an ambiguous provider outcome must be reconciled, never re-marked",
        )
    target = _TRANSITIONS.get((current, outcome))
    if target is None:
        raise AttemptRejected(
            AttemptRefusal.ILLEGAL_TRANSITION,
            f"{current.value} cannot become {outcome.value}",
        )
    return target


def outcome_for_result(result: ProviderExecutionResult) -> AttemptOutcome:
    """Map a provider result to an attempt transition without inventing success."""
    if not isinstance(result, ProviderExecutionResult):
        raise AttemptRejected(AttemptRefusal.INVALID_RECORD, "result must be a ProviderExecutionResult")
    return {
        ProviderOutcome.SUCCEEDED: AttemptOutcome.SUCCEEDED,
        ProviderOutcome.FAILED: AttemptOutcome.FAILED,
        ProviderOutcome.RETRYABLE_FAILURE: AttemptOutcome.RETRYABLE,
        ProviderOutcome.UNKNOWN: AttemptOutcome.UNKNOWN,
    }[result.outcome]


def build_attempt(
    *,
    lease: AgentRunLease,
    provider: str,
    request_digest_value: str,
    started_at: datetime,
    model_digest_value: str | None = None,
    prompt_digest_value: str | None = None,
    deadline_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderAttempt:
    """Build the ``started`` attempt record for one claimed lease."""
    if not isinstance(lease, AgentRunLease):
        raise AttemptRejected(AttemptRefusal.INVALID_RECORD, "attempt requires an AgentRunLease")
    key = attempt_idempotency_key(
        tenant_id=lease.tenant_id,
        run_id=lease.run_id,
        generation=lease.generation,
        attempt=lease.attempt,
    )
    return ProviderAttempt(
        attempt_id=attempt_id_for(key),
        idempotency_key=key,
        tenant_id=lease.tenant_id,
        project_id=lease.project_id,
        run_id=lease.run_id,
        session_id=lease.session_id,
        generation=lease.generation,
        attempt=lease.attempt,
        sequence=lease.sequence,
        provider=_required(provider, "provider", max_length=64),
        request_digest=_digest(request_digest_value, "request_digest"),
        lease_token_digest=lease_token_digest(lease.lease_token),
        state=AttemptState.STARTED,
        started_at=_aware(started_at, "started_at"),
        model_digest=model_digest_value,
        prompt_digest=prompt_digest_value,
        deadline_at=deadline_at,
        metadata=metadata or {},
    )


class ProviderAttemptJournalPort(Protocol):
    """Durable storage for provider attempts (implemented by infrastructure).

    Contract:

    * ``insert_start`` inserts once per ``idempotency_key``; when the key exists
      with the same ``request_digest`` it returns the stored record with
      ``created=False``; with a different digest it raises
      :class:`AttemptConflict`.  It never overwrites an attempt.
    * ``read`` returns the stored attempt or ``None``.
    * ``find_unresolved`` returns the newest attempt for a run whose state is
      ``started``/``unknown`` (the caller must refuse a new provider call).
    * ``cas_finish`` is one ``UPDATE ... WHERE attempt_id = ? AND state = ?``
      and returns the affected row count; ``0`` means the attempt already moved
      on (a lost race, not an error).
    """

    async def insert_start(self, attempt: ProviderAttempt) -> AttemptStart: ...

    async def read(self, *, attempt_id: str) -> ProviderAttempt | None: ...

    async def find_unresolved(self, *, run_id: str) -> ProviderAttempt | None: ...

    async def cas_finish(
        self,
        *,
        attempt_id: str,
        expected_state: AttemptState,
        next_state: AttemptState,
        finished_at: datetime,
        failure_code: str | None,
        provider_request_id: str | None,
        metadata: Mapping[str, Any],
        metadata_dropped: int,
    ) -> int: ...


class ProviderAttemptJournal:
    """Application-tier journal: idempotent start, fenced finish, unresolved scan."""

    def __init__(self, *, port: ProviderAttemptJournalPort) -> None:
        if not callable(getattr(port, "cas_finish", None)):
            raise TypeError("port must implement the ProviderAttemptJournalPort contract")
        self._port = port

    async def unresolved_for_run(self, *, run_id: str) -> ProviderAttempt | None:
        _required(run_id, "run_id", max_length=64)
        return await self._port.find_unresolved(run_id=run_id)

    async def begin(self, attempt: ProviderAttempt) -> AttemptStart:
        if not isinstance(attempt, ProviderAttempt):
            raise AttemptRejected(AttemptRefusal.INVALID_RECORD, "begin requires a ProviderAttempt")
        if attempt.state is not AttemptState.STARTED:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "begin only accepts a freshly started attempt",
            )
        start = await self._port.insert_start(attempt)
        if not isinstance(start, AttemptStart):
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "journal port returned an invalid start result",
            )
        if start.attempt.idempotency_key != attempt.idempotency_key:
            raise AttemptConflict("journal returned a different attempt key")
        return start

    async def finish(
        self,
        attempt: ProviderAttempt,
        *,
        outcome: AttemptOutcome,
        at: datetime,
        failure_code: str | None = None,
        provider_request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CasUpdate:
        """Advance the attempt; ``0`` rows means the attempt already moved on."""
        target = next_state(attempt.state, outcome)
        _aware(at, "at")
        if target in FAILURE_ATTEMPT_STATES:
            if failure_code is None or not _FAILURE_CODE.match(failure_code):
                raise AttemptRejected(
                    AttemptRefusal.INVALID_RECORD,
                    "a failed attempt transition requires a bounded failure code",
                )
        elif failure_code is not None:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "a non-failure attempt transition cannot carry a failure code",
            )
        if target is AttemptState.UNKNOWN and outcome is not AttemptOutcome.UNKNOWN:
            raise AttemptRejected(
                AttemptRefusal.ILLEGAL_TRANSITION,
                "unknown state is reachable only through an unknown outcome",
            )
        if outcome is AttemptOutcome.UNKNOWN and target is not AttemptState.UNKNOWN:
            raise AttemptRejected(
                AttemptRefusal.ILLEGAL_TRANSITION,
                "an unknown outcome cannot be recorded as a known state",
            )
        redacted, dropped = redact_metadata(metadata)
        rows = await self._port.cas_finish(
            attempt_id=attempt.attempt_id,
            expected_state=attempt.state,
            next_state=target,
            finished_at=at,
            failure_code=failure_code if target in FAILURE_ATTEMPT_STATES else None,
            provider_request_id=provider_request_id,
            metadata=redacted,
            metadata_dropped=dropped,
        )
        return CasUpdate(rows)

    async def finish_with_result(
        self,
        attempt: ProviderAttempt,
        *,
        result: ProviderExecutionResult,
        at: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> CasUpdate:
        """Record a provider result, refusing to launder ``unknown`` into success."""
        outcome = outcome_for_result(result)
        failure_code = result.failure_code
        if result.outcome is ProviderOutcome.FAILED and failure_code is None:
            failure_code = "agent_run.provider_failed"
        if result.outcome is ProviderOutcome.RETRYABLE_FAILURE and failure_code is None:
            failure_code = "agent_run.provider_retryable"
        if result.outcome is ProviderOutcome.UNKNOWN and failure_code is not None:
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "an unknown provider outcome must not carry a failure code",
            )
        return await self.finish(
            attempt,
            outcome=outcome,
            at=at,
            failure_code=failure_code,
            provider_request_id=result.provider_request_id,
            metadata=metadata,
        )

    async def reconcile(
        self,
        attempt: ProviderAttempt,
        *,
        result: ProviderExecutionResult,
        at: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> CasUpdate:
        """Resolve an ``unknown`` attempt from proven reconciliation evidence."""
        if result.outcome is ProviderOutcome.UNKNOWN:
            raise AttemptRejected(
                AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION,
                "reconciliation cannot conclude unknown",
            )
        outcome = (
            AttemptOutcome.RECONCILED_SUCCEEDED
            if result.outcome is ProviderOutcome.SUCCEEDED
            else AttemptOutcome.RECONCILED_FAILED
        )
        failure_code = None
        if outcome is AttemptOutcome.RECONCILED_FAILED:
            failure_code = result.failure_code or "agent_run.reconciled_failed"
        return await self.finish(
            attempt,
            outcome=outcome,
            at=at,
            failure_code=failure_code,
            provider_request_id=result.provider_request_id,
            metadata=metadata,
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = [
    "ATTEMPT_JOURNAL_BLOCKER",
    "ATTEMPT_TABLE_BLOCKER",
    "AttemptConflict",
    "AttemptJournalUnavailable",
    "AttemptOutcome",
    "AttemptRefusal",
    "AttemptRejected",
    "AttemptStart",
    "AttemptState",
    "CLOSED_ATTEMPT_STATES",
    "FAILURE_ATTEMPT_STATES",
    "FORBIDDEN_METADATA_KEYS",
    "MAX_REDACTED_METADATA_KEYS",
    "ProviderAttempt",
    "ProviderAttemptJournal",
    "ProviderAttemptJournalPort",
    "REDACTED_METADATA_KEYS",
    "SUCCESS_ATTEMPT_STATES",
    "TERMINAL_ATTEMPT_STATES",
    "UNRESOLVED_ATTEMPT_STATES",
    "attempt_id_for",
    "attempt_idempotency_key",
    "build_attempt",
    "canonical_digest",
    "lease_token_digest",
    "next_state",
    "outcome_for_result",
    "prompt_digest",
    "redact_metadata",
    "request_digest",
]
