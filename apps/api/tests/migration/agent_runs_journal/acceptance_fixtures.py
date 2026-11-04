"""Fixtures for the SQL attempt-journal acceptance artifacts.

The unit under test is a SQL adapter, so the double is a **scripted session**, not
an in-memory database.  It records the statement it was handed and returns the
next canned result; it never pretends to evaluate SQL.  That split is deliberate:

* *predicate semantics* are asserted by rendering each statement for the
  PostgreSQL dialect (``test_journal_sql_shape.py``) — no database, no DDL;
* *adapter branching* (idempotent insert, conflict, lost race, redaction, missing
  table) is asserted by scripting row counts and row contents here.

Nothing in this module is importable from ``prodavan``; the production store takes
an ``async_sessionmaker`` and is only ever handed one by composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from prodavan.application.agent_runs.attempts import (
    CLOSED_ATTEMPT_STATES,
    AttemptState,
    ProviderAttempt,
    build_attempt,
    request_digest,
)
from prodavan.application.agent_runs.execution import AgentRunLease
from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
    SqlAlchemyProviderAttemptJournal,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_registry import (
    AgentRunProviderAttemptRow,
)

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(seconds=60)

TENANT = "tenant-journal"
PROJECT = "project-journal"
RUN_ID = "agr-journal"
SESSION = "session-journal"
PROVIDER = "agent-runtime"
PROMPT = "summarise the pipeline"

FAILURE_CODE = "agent_run.provider_failed"
UNKNOWN_CODE = "agent_run.provider_unknown"


# --------------------------------------------------------------------------- #
# Contract records
# --------------------------------------------------------------------------- #


def make_lease(**overrides: object) -> AgentRunLease:
    """One claimed lease, i.e. the identity a provider attempt is built from."""
    fields: dict[str, object] = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "run_id": RUN_ID,
        "session_id": SESSION,
        "owner_id": "worker-journal",
        "lease_token": "token-generation-1",
        "generation": 1,
        "attempt": 1,
        "sequence": 2,
        "lease_until": DEADLINE,
    }
    fields.update(overrides)
    return AgentRunLease(**fields)  # type: ignore[arg-type]


def digest_for(prompt: str = PROMPT, *, provider: str = PROVIDER) -> str:
    return request_digest(
        provider=provider,
        model=None,
        session_id=SESSION,
        attachments=0,
        prompt=prompt,
    )


def make_attempt(
    *,
    prompt: str = PROMPT,
    metadata: Mapping[str, Any] | None = None,
    lease: AgentRunLease | None = None,
) -> ProviderAttempt:
    """A fresh ``started`` attempt as the worker would build it."""
    lease = lease or make_lease()
    return build_attempt(
        lease=lease,
        provider=PROVIDER,
        request_digest_value=digest_for(prompt),
        started_at=NOW,
        deadline_at=lease.lease_until,
        metadata=metadata
        if metadata is not None
        else {"provider": PROVIDER, "phase": "dispatch"},
    )


def record(
    state: AttemptState = AttemptState.STARTED,
    *,
    attempt: ProviderAttempt | None = None,
    failure_code: str | None = None,
    provider_request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderAttempt:
    """A record in ``state`` (``started`` returns the attempt unchanged).

    ``dataclasses.replace`` re-runs ``ProviderAttempt.__post_init__``, so an
    impossible combination (terminal without ``finished_at``, failure state
    without a code) fails here instead of silently seeding a bad fixture.
    """
    base = attempt or make_attempt(metadata=metadata)
    if state is AttemptState.STARTED:
        if provider_request_id is None:
            return base
        return replace(base, provider_request_id=provider_request_id)
    return replace(
        base,
        state=state,
        finished_at=NOW,
        failure_code=failure_code,
        provider_request_id=provider_request_id,
    )


def row_of(attempt: ProviderAttempt) -> AgentRunProviderAttemptRow:
    """The row shape the store maps back, built without a database."""
    return AgentRunProviderAttemptRow(
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        tenant_id=attempt.tenant_id,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        generation=attempt.generation,
        attempt=attempt.attempt,
        sequence=attempt.sequence,
        provider=attempt.provider,
        request_digest=attempt.request_digest,
        lease_token_digest=attempt.lease_token_digest,
        state=attempt.state.value,
        started_at=attempt.started_at,
        session_id=attempt.session_id,
        model_digest=attempt.model_digest,
        prompt_digest=attempt.prompt_digest,
        deadline_at=attempt.deadline_at,
        finished_at=attempt.finished_at,
        failure_code=attempt.failure_code,
        provider_request_id=attempt.provider_request_id,
        metadata_json=dict(attempt.metadata),
        metadata_dropped=attempt.metadata_dropped,
    )


def is_closed(state: AttemptState) -> bool:
    """True for states the contract will not let a caller leave again."""
    return state in CLOSED_ATTEMPT_STATES


# --------------------------------------------------------------------------- #
# Scripted session double
# --------------------------------------------------------------------------- #


class FakeResult:
    """A canned statement result (row count + rows + scalar)."""

    def __init__(
        self,
        *,
        rows: tuple[Any, ...] = (),
        rowcount: int = 0,
        scalar: Any = None,
    ) -> None:
        self._rows = rows
        self.rowcount = rowcount
        self.scalar_value = scalar

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)


class FakeScalars:
    def __init__(self, rows: tuple[Any, ...]) -> None:
        self._rows = rows

    def one_or_none(self) -> Any:
        if len(self._rows) > 1:  # pragma: no cover - a primary-key read cannot
            raise AssertionError("the statement returned more than one row")
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


@dataclass(frozen=True)
class Step:
    """One scripted database interaction."""

    kind: Literal["scalar", "execute"]
    result: FakeResult | None = None
    error: BaseException | None = None


class Script:
    """Ordered expectations plus the statements the adapter actually issued."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.calls: list[tuple[str, Any]] = []

    def take(self, kind: str, statement: Any) -> FakeResult:
        self.calls.append((kind, statement))
        if not self._steps:
            raise AssertionError(
                f"the adapter issued an unexpected {kind}: the script is exhausted"
            )
        step = self._steps.pop(0)
        assert step.kind == kind, f"scripted {step.kind}, adapter issued {kind}"
        if step.error is not None:
            raise step.error
        return step.result if step.result is not None else FakeResult()

    @property
    def pending(self) -> int:
        return len(self._steps)

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _statement in self.calls]


class _Begin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class ScriptedSession:
    """``AsyncSession``-shaped: context manager, ``begin``, ``scalar``, ``execute``."""

    def __init__(self, script: Script) -> None:
        self._script = script

    async def __aenter__(self) -> ScriptedSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def begin(self) -> _Begin:
        return _Begin()

    async def scalar(self, statement: Any) -> Any:
        return self._script.take("scalar", statement).scalar_value

    async def execute(self, statement: Any) -> FakeResult:
        return self._script.take("execute", statement)


class ScriptedSessionFactory:
    """``async_sessionmaker``-shaped callable returning sessions over one script."""

    def __init__(self, script: Script) -> None:
        self.script = script

    def __call__(self) -> ScriptedSession:
        return ScriptedSession(self.script)


def store_for(script: Script) -> SqlAlchemyProviderAttemptJournal:
    return SqlAlchemyProviderAttemptJournal(
        session_factory=ScriptedSessionFactory(script)  # type: ignore[arg-type]
    )


__all__ = [
    "DEADLINE",
    "FAILURE_CODE",
    "FakeResult",
    "NOW",
    "PROJECT",
    "PROMPT",
    "PROVIDER",
    "RUN_ID",
    "SESSION",
    "Script",
    "ScriptedSession",
    "ScriptedSessionFactory",
    "Step",
    "TENANT",
    "UNKNOWN_CODE",
    "digest_for",
    "is_closed",
    "make_attempt",
    "make_lease",
    "record",
    "row_of",
    "store_for",
]
