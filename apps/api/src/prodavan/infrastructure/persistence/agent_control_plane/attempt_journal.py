"""SQL implementation of the durable provider-attempt journal.

Why this module exists
----------------------
``application/agent_runs/attempts.py`` declares the attempt journal twice over:
as *policy* (``AttemptState``, the transition matrix, ``next_state``, the
redaction allow-list) and as *storage* (:class:`ProviderAttemptJournalPort`,
``insert_start`` / ``read`` / ``find_unresolved`` / ``cas_finish``).  It also
refuses, by design, to fall back to process memory: a host without a journal must
raise ``AttemptJournalUnavailable``.

The table those rows belong in exists — ``agent_run_provider_attempts``
(:mod:`.attempt_registry`, revision ``2026110311``) — but nothing implemented the
port, so every execution host failed closed with
``agent_run_provider_attempt_journal_unwired``.  This module is that
implementation, and only that: which transition is legal, which metadata key
survives and what ``unknown`` means stay in the application module and are
*called*, never copied.

Contract notes (the parts that are easy to get wrong)
-----------------------------------------------------
* ``insert_start`` is ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING
  RETURNING attempt_id``.  One statement decides "created", so two workers that
  race on the same key cannot both believe they created the attempt: the loser
  gets no returned row and falls through to a read of the winner's row.  Same
  ``request_digest`` -> the stored record with ``created=False``; a different
  digest -> :class:`AttemptConflict`.  Nothing here ever overwrites an attempt.
* ``cas_finish`` is exactly one ``UPDATE ... WHERE attempt_id = ? AND state = ?``
  whose ``SET`` clause carries the new state, ``finished_at``, the failure code,
  the redacted metadata and the accumulated ``metadata_dropped``.  It returns the
  affected row count and never raises on ``0``: ``0`` is a lost race (another
  worker, a reconciler, a cancellation won first), which is the contract.
* :meth:`SqlAlchemyProviderAttemptJournal.finish_attempt` is the *typed* variant
  for callers that do not go through ``ProviderAttemptJournal``: it resolves the
  target through the same matrix and turns ``rowcount == 0`` into a typed
  :class:`AttemptRejected` (``ALREADY_TERMINAL`` / ``ILLEGAL_TRANSITION`` /
  ``UNKNOWN_REQUIRES_RECONCILIATION`` / ``INVALID_RECORD``) instead of a silent
  "probably fine".  A refused finish is never reported as success.
* ``unknown`` cannot be laundered into a known outcome *by this store either*:
  :func:`finish_values` accepts only targets reachable from the current state in
  the application matrix (:func:`_legal_targets` probes ``next_state`` rather than
  repeating the table), so ``unknown -> known_succeeded`` is refused even if a
  caller bypasses the application tier.  ``unknown`` leaves through
  ``reconciled_succeeded``/``reconciled_failed`` only.
* metadata is re-redacted at the write site (``redact_metadata`` +
  ``_assert_secret_free``) even though the application tier has already done it.
  Re-redaction is idempotent — an allow-listed scalar mapping drops nothing — so
  the stored ``metadata_dropped`` is the caller's count plus the honest extra,
  never double-counted, and a direct caller that passes raw metadata still cannot
  persist a prompt, a credential or a payload.
* a missing table is not a crash and not a workaround: ``ProgrammingError`` for
  ``agent_run_provider_attempts`` becomes :class:`AttemptJournalUnavailable`
  (fail closed, revision named), every other database error propagates unchanged.
  There is no in-memory fallback anywhere in this module.

Statement builders
------------------
Every statement is produced by a pure function (``start_insert_statement``,
``read_statement``, ``unresolved_statement``, ``expired_unresolved_statement``,
``finish_statement``) that the store method only executes, and the two value
builders (``start_values``, ``finish_values``) are pure as well.  That is what
makes the write side reviewable without a database: the acceptance artifacts
render the PostgreSQL text and assert on the ``ON CONFLICT`` target, the
``RETURNING`` column, the ``WHERE`` predicate and the rendered metadata count
instead of trusting a mock.

Extensions beyond the protocol
------------------------------
``find_unresolved`` is the reconciler's question "may this run call the provider
again?"; the sweep question "which attempts outlived their deadline?" is a
different predicate over the same table and is exposed as
``find_expired_unresolved`` (``state = 'started' AND deadline_at <= now``,
ordered by deadline, using ``ix_agent_run_attempts_state_deadline``).  Both are
read-only; neither mutates a row, because only a reconciliation write may move an
unresolved attempt.

``read``/``find_unresolved`` also accept an optional ``tenant_id`` narrow
argument: the protocol identifies an attempt by its hashed id, and a store that
answered a cross-tenant id with the row would be a tenancy leak.  With
``tenant_id`` supplied a foreign row is reported as *absent*, not as forbidden.

Column widths are enforced before the statement is built (``_TEXT_WIDTHS``).  The
contract validates looser bounds than the columns allow — ``tenant_id``,
``run_id`` and ``provider`` up to 96 characters versus 40 — so an over-long
identity becomes a typed ``INVALID_RECORD`` instead of a truncation error from the
driver.  Nothing is ever silently truncated on the way in.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any, NoReturn

from sqlalchemy import Insert, Update, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from prodavan.application.agent_runs.attempts import (
    FAILURE_ATTEMPT_STATES,
    TERMINAL_ATTEMPT_STATES,
    UNRESOLVED_ATTEMPT_STATES,
    AttemptConflict,
    AttemptJournalUnavailable,
    AttemptOutcome,
    AttemptRefusal,
    AttemptRejected,
    AttemptStart,
    AttemptState,
    ProviderAttempt,
    _assert_secret_free,
    next_state,
    redact_metadata,
)
from prodavan.application.agent_runs.lease import CasUpdate
from prodavan.infrastructure.persistence.agent_control_plane.attempt_registry import (
    AgentRunProviderAttemptRow,
)

TABLE_NAME = AgentRunProviderAttemptRow.__tablename__
"""Table this store owns (asserted by the acceptance artifacts)."""

REVISION = "2026110311"
"""Alembic revision that creates the table."""

UNRESOLVED_STATE_VALUES: tuple[str, ...] = tuple(
    sorted(state.value for state in UNRESOLVED_ATTEMPT_STATES)
)
"""Persisted states the reconciler must still decide (``started``, ``unknown``)."""

MAX_UNRESOLVED_SCAN = 200
"""Upper bound of one expired-deadline sweep (bounded work per call)."""

#: Failure codes must stay readable as ``ProviderAttempt.failure_code``; the
#: contract's own pattern allows 128 characters while the column is 64 wide, so
#: this pattern is the *narrower* of the two: anything accepted here also passes
#: the application rule, and nothing accepted here can overflow the column.
_FAILURE_CODE_SHAPE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

_MISSING_TABLE_DETAIL = (
    f"{TABLE_NAME} is not present; apply revision {REVISION} — "
    "the provider attempt journal stays unwired"
)


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _required(value: str, name: str, *, max_length: int = 64) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _state(value: AttemptState | str, name: str) -> AttemptState:
    """Coerce a state, refusing anything outside the closed vocabulary."""
    if isinstance(value, AttemptState):
        return value
    try:
        return AttemptState(value)
    except ValueError as exc:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            f"{name} is outside the attempt state vocabulary",
        ) from exc


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


#: Column widths of the identity strings.  The contract validates looser bounds
#: (``tenant_id``/``run_id``/``provider`` up to 96 characters) than the columns
#: allow (40), so the store refuses an over-long identity *before* PostgreSQL
#: raises a truncation error — a typed refusal instead of a driver-level 500.
_TEXT_WIDTHS: dict[str, int] = {
    "attempt_id": 64,
    "idempotency_key": 160,
    "tenant_id": 40,
    "project_id": 40,
    "run_id": 40,
    "provider": 40,
    "session_id": 40,
    "provider_request_id": 160,
}


def _column_text(value: str, name: str) -> str:
    """A non-empty string that fits its column (never truncated, always refused)."""
    text = _required(value, name, max_length=_TEXT_WIDTHS[name] * 4)
    width = _TEXT_WIDTHS[name]
    if len(text) > width:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            f"{name} does not fit its {width}-character column",
        )
    return text


def _optional_column_text(value: str | None, name: str) -> str | None:
    return None if value is None else _column_text(value, name)


@lru_cache(maxsize=None)
def _legal_targets(current: AttemptState) -> frozenset[AttemptState]:
    """Every state the application matrix lets ``current`` become.

    Derived from :func:`next_state` (the single source of truth) by probing the
    public outcome vocabulary, so this store cannot drift from the matrix: a new
    transition in ``attempts.py`` is legal here the moment it is legal there, and
    a removed one is refused here too.  ``unknown`` therefore has exactly two
    successors (the two reconciled states) and terminal states have none.
    """
    targets: set[AttemptState] = set()
    for outcome in AttemptOutcome:
        try:
            targets.add(next_state(current, outcome))
        except AttemptRejected:
            continue
    return frozenset(targets)


def _is_missing_table(exc: ProgrammingError) -> bool:
    """True when the error says the journal table itself does not exist."""
    original = getattr(exc, "orig", None)
    if "undefinedtable" in type(original).__name__.lower():
        return True
    text = str(original if original is not None else exc).lower()
    return TABLE_NAME in text and ("does not exist" in text or "undefined" in text)


# --------------------------------------------------------------------------- #
# Pure value builders (no session, no SQL — the write contract in one place)
# --------------------------------------------------------------------------- #


def start_values(attempt: ProviderAttempt) -> dict[str, Any]:
    """Column values of one ``started`` attempt row.

    Accepts only a freshly started record (the application journal refuses
    anything else and this builder repeats the check so a direct caller cannot
    insert a finished attempt as "started").  Metadata is redacted *again* here:
    ``ProviderAttempt`` already did it, re-redaction of an allow-listed mapping
    drops nothing, so ``metadata_dropped`` stays exactly what the record says.
    """
    if not isinstance(attempt, ProviderAttempt):
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD, "journal start requires a ProviderAttempt"
        )
    if attempt.state is not AttemptState.STARTED:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD, "journal start only accepts a started attempt"
        )
    metadata, extra_dropped = redact_metadata(attempt.metadata)
    _assert_secret_free(metadata)
    return {
        "attempt_id": _column_text(attempt.attempt_id, "attempt_id"),
        "idempotency_key": _column_text(attempt.idempotency_key, "idempotency_key"),
        "tenant_id": _column_text(attempt.tenant_id, "tenant_id"),
        "project_id": _column_text(attempt.project_id, "project_id"),
        "run_id": _column_text(attempt.run_id, "run_id"),
        "generation": attempt.generation,
        "attempt": attempt.attempt,
        "sequence": attempt.sequence,
        "provider": _column_text(attempt.provider, "provider"),
        "request_digest": attempt.request_digest,
        "lease_token_digest": attempt.lease_token_digest,
        "state": attempt.state.value,
        "started_at": attempt.started_at,
        "session_id": _optional_column_text(attempt.session_id, "session_id"),
        "model_digest": attempt.model_digest,
        "prompt_digest": attempt.prompt_digest,
        "deadline_at": attempt.deadline_at,
        "finished_at": attempt.finished_at,
        "failure_code": attempt.failure_code,
        "provider_request_id": _optional_column_text(
            attempt.provider_request_id, "provider_request_id"
        ),
        "metadata_json": dict(metadata),
        "metadata_dropped": attempt.metadata_dropped + extra_dropped,
    }


def finish_values(
    *,
    expected_state: AttemptState | str,
    next_state: AttemptState | str,
    finished_at: datetime,
    failure_code: str | None = None,
    provider_request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    metadata_dropped: int = 0,
) -> dict[str, Any]:
    """``SET`` values of one attempt transition, validated against the matrix.

    The order of the checks mirrors the application journal: terminal first
    (``ALREADY_TERMINAL``), then reachability (``unknown -> known_*`` and every
    other impossible pair are ``ILLEGAL_TRANSITION``), then the failure-code
    pairing.  A record that would be unreadable by ``ProviderAttempt`` is refused
    here rather than persisted, because a row the journal cannot read back is
    indistinguishable from a lost attempt.
    """
    expected = _state(expected_state, "expected_state")
    target = _state(next_state, "next_state")
    finished_at = _aware(finished_at, "finished_at")

    if expected in TERMINAL_ATTEMPT_STATES:
        raise AttemptRejected(
            AttemptRefusal.ALREADY_TERMINAL, f"attempt is already {expected.value}"
        )
    if target not in _legal_targets(expected):
        # Covers ``unknown -> known_*`` (only the two reconciled states are
        # reachable) and every other pair the matrix does not contain.
        raise AttemptRejected(
            AttemptRefusal.ILLEGAL_TRANSITION,
            f"{expected.value} cannot become {target.value}",
        )
    if target in FAILURE_ATTEMPT_STATES:
        if failure_code is None or not _FAILURE_CODE_SHAPE.match(failure_code):
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "a failed attempt transition requires a bounded failure code",
            )
    elif failure_code is not None:
        # Includes every success target: a code here would make the row
        # unreadable by ``ProviderAttempt``.
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            "a non-failure attempt transition cannot carry a failure code",
        )
    if (
        isinstance(metadata_dropped, bool)
        or not isinstance(metadata_dropped, int)
        or metadata_dropped < 0
    ):
        raise ValueError("metadata_dropped must be a non-negative integer")

    cleaned, extra_dropped = redact_metadata(metadata)
    _assert_secret_free(cleaned)

    request_id: str | None = None
    if provider_request_id is not None:
        request_id = _column_text(provider_request_id, "provider_request_id")

    values: dict[str, Any] = {
        "state": target.value,
        "finished_at": finished_at,
        # Validated above: a failure target carries a bounded code and every
        # other target carries none, so the row stays readable by the contract.
        "failure_code": failure_code,
        "metadata_json": dict(cleaned),
        # This is the *delta* this transition contributes.  ``finish_statement``
        # adds it to the stored column so the row holds the honest total (the
        # start already recorded its own drops); the value builder stays free of
        # SQL and of any read of the current row.
        "metadata_dropped": metadata_dropped + extra_dropped,
    }
    if request_id is not None:
        values["provider_request_id"] = request_id
    return values


# --------------------------------------------------------------------------- #
# Pure statement builders (one statement each; no reads, no fallbacks)
# --------------------------------------------------------------------------- #


def start_insert_statement(*, attempt: ProviderAttempt) -> Insert:
    """Idempotent insert: one row per ``idempotency_key``, never an overwrite.

    ``ON CONFLICT (idempotency_key) DO NOTHING`` makes the *insert* the
    arbiter, so a retried worker cannot fork history and cannot clobber the
    winner's row; ``RETURNING attempt_id`` distinguishes "we created it" from
    "somebody already did" in the same statement.
    """
    return (
        pg_insert(AgentRunProviderAttemptRow)
        .values(**start_values(attempt))
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AgentRunProviderAttemptRow.attempt_id)
    )


def read_statement(*, attempt_id: str, tenant_id: str | None = None) -> Select:
    """``SELECT`` one attempt by primary key, optionally tenant-narrowed.

    A filter wider than its column cannot match a row and is refused rather than
    silently returning ``None``: a caller passing the wrong identity should see a
    refusal, not an absence that looks like "no attempt".
    """
    statement = select(AgentRunProviderAttemptRow).where(
        AgentRunProviderAttemptRow.attempt_id
        == _required(attempt_id, "attempt_id", max_length=_TEXT_WIDTHS["attempt_id"])
    )
    if tenant_id is not None:
        statement = statement.where(
            AgentRunProviderAttemptRow.tenant_id
            == _required(tenant_id, "tenant_id", max_length=_TEXT_WIDTHS["tenant_id"])
        )
    return statement


def unresolved_statement(*, run_id: str, tenant_id: str | None = None) -> Select:
    """``SELECT`` the newest attempt of a run that has no decided outcome.

    ``started`` (in flight or abandoned mid-call) and ``unknown`` (the provider
    answered ambiguously) are the two states that forbid another provider call;
    the newest claim wins because that is the one a retry would collide with.
    """
    statement = (
        select(AgentRunProviderAttemptRow)
        .where(
            AgentRunProviderAttemptRow.run_id
            == _required(run_id, "run_id", max_length=_TEXT_WIDTHS["run_id"]),
            AgentRunProviderAttemptRow.state.in_(UNRESOLVED_STATE_VALUES),
        )
        .order_by(
            AgentRunProviderAttemptRow.generation.desc(),
            AgentRunProviderAttemptRow.attempt.desc(),
            AgentRunProviderAttemptRow.attempt_id.desc(),
        )
        .limit(1)
    )
    if tenant_id is not None:
        statement = statement.where(
            AgentRunProviderAttemptRow.tenant_id
            == _required(tenant_id, "tenant_id", max_length=_TEXT_WIDTHS["tenant_id"])
        )
    return statement


def expired_unresolved_statement(*, now: datetime, limit: int = MAX_UNRESOLVED_SCAN) -> Select:
    """``SELECT`` started attempts whose deadline has passed (reconcile sweep).

    A ``started`` row whose deadline expired is an attempt whose provider answer
    was never recorded — the crash-in-the-window case.  It is already inside the
    unresolved set; this predicate is the *time-ordered* view of it, for a sweep
    that wants the oldest expired attempt first instead of a run's newest.
    """
    now = _aware(now, "now")
    limit = _positive(limit, "limit")
    if limit > MAX_UNRESOLVED_SCAN:
        raise ValueError(f"limit cannot exceed {MAX_UNRESOLVED_SCAN}")
    return (
        select(AgentRunProviderAttemptRow)
        .where(
            AgentRunProviderAttemptRow.state == AttemptState.STARTED.value,
            AgentRunProviderAttemptRow.deadline_at.is_not(None),
            AgentRunProviderAttemptRow.deadline_at <= now,
        )
        .order_by(
            AgentRunProviderAttemptRow.deadline_at.asc(),
            AgentRunProviderAttemptRow.attempt_id.asc(),
        )
        .limit(limit)
    )


def finish_statement(
    *,
    attempt_id: str,
    expected_state: AttemptState | str,
    next_state: AttemptState | str,
    finished_at: datetime,
    failure_code: str | None = None,
    provider_request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    metadata_dropped: int = 0,
) -> Update:
    """The one and only attempt transition: ``UPDATE ... WHERE attempt_id AND state``.

    ``state = <expected>`` in the predicate is the CAS fence: the row is only
    advanced from the state the caller observed, so a stale or duplicated finish
    affects ``0`` rows instead of overwriting a newer outcome.  A
    ``provider_request_id`` the finish does not know is left untouched (the
    ``SET`` omits the column) rather than erased, and ``metadata_dropped`` is
    incremented by the transition's delta instead of being reset, so the column
    keeps counting every key the journal refused to store.
    """
    expected = _state(expected_state, "expected_state")
    values = finish_values(
        expected_state=expected,
        next_state=next_state,
        finished_at=finished_at,
        failure_code=failure_code,
        provider_request_id=provider_request_id,
        metadata=metadata,
        metadata_dropped=metadata_dropped,
    )
    row = AgentRunProviderAttemptRow
    values["metadata_dropped"] = row.metadata_dropped + values["metadata_dropped"]
    return (
        update(row)
        .where(
            row.attempt_id
            == _required(attempt_id, "attempt_id", max_length=_TEXT_WIDTHS["attempt_id"]),
            row.state == expected.value,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )


# --------------------------------------------------------------------------- #
# Row mapping
# --------------------------------------------------------------------------- #


def attempt_from_row(row: AgentRunProviderAttemptRow) -> ProviderAttempt:
    """Rebuild the journal record from its row, refusing a corrupt one.

    ``ProviderAttempt.__post_init__`` re-validates the whole record (digest
    shapes, key derivation, state/finished_at pairing, failure-code pairing), so
    a row that violates the contract surfaces as ``INVALID_RECORD`` rather than
    as a half-trusted object or a raw ``ValueError``.
    """
    metadata = row.metadata_json if isinstance(row.metadata_json, Mapping) else None
    try:
        return ProviderAttempt(
            attempt_id=row.attempt_id,
            idempotency_key=row.idempotency_key,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            run_id=row.run_id,
            generation=int(row.generation),
            attempt=int(row.attempt),
            sequence=int(row.sequence),
            provider=row.provider,
            request_digest=row.request_digest,
            lease_token_digest=row.lease_token_digest,
            state=AttemptState(row.state),
            started_at=row.started_at,
            session_id=row.session_id,
            model_digest=row.model_digest,
            prompt_digest=row.prompt_digest,
            deadline_at=row.deadline_at,
            finished_at=row.finished_at,
            failure_code=row.failure_code,
            provider_request_id=row.provider_request_id,
            metadata=metadata,
            metadata_dropped=int(row.metadata_dropped or 0),
        )
    except AttemptRejected:
        raise
    except ValueError as exc:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            "stored attempt row violates the journal contract",
        ) from exc


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class SqlAlchemyProviderAttemptJournal:
    """Durable attempt journal over ``agent_run_provider_attempts``.

    Structurally satisfies ``ProviderAttemptJournalPort`` (``insert_start``,
    ``read``, ``find_unresolved``, ``cas_finish``; asserted by the acceptance
    artifact) and is the object to hand to ``ProviderAttemptJournal(port=...)``.

    ``session_factory`` must point at the database that holds ``agent_runs`` (the
    ``run_id`` foreign key is enforced there) and the attempt table.  Every
    method is one short transaction; the store keeps no state between calls, so
    two replicas see the same journal and a crash loses nothing that was
    committed.  There is no in-memory fallback: without the table every method
    raises :class:`AttemptJournalUnavailable`.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- transaction scope --------------------------------------------------

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """One transaction per call; a missing table fails closed, not loudly."""
        try:
            async with self._session_factory() as session, session.begin():
                yield session
        except ProgrammingError as exc:
            if not _is_missing_table(exc):
                raise
            raise AttemptJournalUnavailable(_MISSING_TABLE_DETAIL) from exc

    # -- ProviderAttemptJournalPort ----------------------------------------

    async def insert_start(self, attempt: ProviderAttempt) -> AttemptStart:
        """Insert the attempt once per idempotency key; never overwrite it.

        Returns ``created=True`` when this call created the row and
        ``created=False`` with *the stored record* when an identical start was
        already recorded.  The same key with a different ``request_digest`` is a
        :class:`AttemptConflict` (a reused key is a different provider call, not
        a replay).  A key bound to a different run/generation/attempt is
        ``INVALID_RECORD``: that can only be a forged or corrupted key.
        """
        statement = start_insert_statement(attempt=attempt)
        async with self._session() as session:
            created_id = await session.scalar(statement)
            if created_id is not None:
                return AttemptStart(attempt=attempt, created=True)
            stored = await self._load_by_key(session, idempotency_key=attempt.idempotency_key)
        if stored is None:  # pragma: no cover - the conflicting row is committed
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD,
                "attempt key is reserved by a transaction that did not commit a visible row",
            )
        _assert_same_request(stored=stored, incoming=attempt)
        return AttemptStart(attempt=stored, created=False)

    async def read(self, *, attempt_id: str, tenant_id: str | None = None) -> ProviderAttempt | None:
        """Return the stored attempt, or ``None`` when it does not exist."""
        statement = read_statement(attempt_id=attempt_id, tenant_id=tenant_id)
        async with self._session() as session:
            row = (await session.execute(statement)).scalars().one_or_none()
        return None if row is None else attempt_from_row(row)

    async def find_unresolved(
        self, *, run_id: str, tenant_id: str | None = None
    ) -> ProviderAttempt | None:
        """The newest ``started``/``unknown`` attempt of a run, if any.

        A non-``None`` answer means the run must not call the provider again
        until reconciliation decides that attempt.
        """
        statement = unresolved_statement(run_id=run_id, tenant_id=tenant_id)
        async with self._session() as session:
            row = (await session.execute(statement)).scalars().one_or_none()
        return None if row is None else attempt_from_row(row)

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
    ) -> int:
        """Apply one transition; returns the affected row count (``0`` = lost race).

        The statement is built before the session opens, so an illegal pair is
        refused with ``ILLEGAL_TRANSITION`` / ``ALREADY_TERMINAL`` without touching
        the database at all.
        """
        statement = finish_statement(
            attempt_id=attempt_id,
            expected_state=expected_state,
            next_state=next_state,
            finished_at=finished_at,
            failure_code=failure_code,
            provider_request_id=provider_request_id,
            metadata=metadata,
            metadata_dropped=metadata_dropped,
        )
        async with self._session() as session:
            result = await session.execute(statement)
        return int(result.rowcount or 0)

    # -- typed operation vocabulary ----------------------------------------

    async def start_attempt(self, attempt: ProviderAttempt) -> AttemptStart:
        """Alias of :meth:`insert_start` (the migration's operation name)."""
        return await self.insert_start(attempt)

    async def read_attempt(
        self, *, attempt_id: str, tenant_id: str | None = None
    ) -> ProviderAttempt | None:
        """Alias of :meth:`read` (the migration's operation name)."""
        return await self.read(attempt_id=attempt_id, tenant_id=tenant_id)

    async def finish_attempt(
        self,
        attempt: ProviderAttempt,
        *,
        outcome: AttemptOutcome,
        at: datetime,
        failure_code: str | None = None,
        provider_request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CasUpdate:
        """Advance an attempt through the matrix, refusing every silent outcome.

        The target comes from :func:`next_state`, so ``ALREADY_TERMINAL``,
        ``UNKNOWN_REQUIRES_RECONCILIATION`` and ``ILLEGAL_TRANSITION`` are raised
        before any SQL.  After the CAS, ``rowcount == 0`` is *classified from the
        stored row* instead of being returned as a vague zero:

        * the row is gone -> ``INVALID_RECORD`` (the journal cannot confirm it);
        * the stored state is the requested target, or any other terminal state ->
          ``ALREADY_TERMINAL`` (an outcome is already on record, and the detail
          names which one);
        * the stored state is ``unknown`` and the caller wanted a ``known_*``
          outcome -> ``UNKNOWN_REQUIRES_RECONCILIATION``;
        * anything else (including a row that is still where the caller left it,
          which means the read and the write disagree) -> ``ILLEGAL_TRANSITION``.

        Only ``rowcount == 1`` is returned as :meth:`CasUpdate.matched`; a lost
        race is therefore never reported as success.
        """
        if not isinstance(attempt, ProviderAttempt):
            raise AttemptRejected(
                AttemptRefusal.INVALID_RECORD, "finish requires a ProviderAttempt"
            )
        target = next_state(attempt.state, outcome)
        rows = await self.cas_finish(
            attempt_id=attempt.attempt_id,
            expected_state=attempt.state,
            next_state=target,
            finished_at=at,
            failure_code=failure_code,
            provider_request_id=provider_request_id,
            metadata=metadata or {},
            metadata_dropped=0,
        )
        if rows == 1:
            return CasUpdate.matched()
        stored = await self.read(attempt_id=attempt.attempt_id, tenant_id=attempt.tenant_id)
        _refuse_lost_finish(attempt=attempt, target=target, stored=stored)

    async def find_expired_unresolved(
        self, *, now: datetime, limit: int = MAX_UNRESOLVED_SCAN
    ) -> tuple[ProviderAttempt, ...]:
        """Started attempts whose deadline passed, oldest deadline first."""
        statement = expired_unresolved_statement(now=now, limit=limit)
        async with self._session() as session:
            rows = (await session.execute(statement)).scalars().all()
        return tuple(attempt_from_row(row) for row in rows)

    # -- wiring gate --------------------------------------------------------

    async def ensure_ready(self) -> None:
        """Prove the journal table exists; raise ``AttemptJournalUnavailable`` if not.

        Used by composition/host wiring so an un-applied revision is a named
        blocker at start-up instead of a failed provider call later.
        """
        async with self._session() as session:
            await session.execute(select(AgentRunProviderAttemptRow.attempt_id).limit(1))

    # -- internals ----------------------------------------------------------

    async def _load_by_key(
        self, session: AsyncSession, *, idempotency_key: str
    ) -> ProviderAttempt | None:
        statement = select(AgentRunProviderAttemptRow).where(
            AgentRunProviderAttemptRow.idempotency_key
            == _required(
                idempotency_key, "idempotency_key", max_length=_TEXT_WIDTHS["idempotency_key"]
            )
        )
        row = (await session.execute(statement)).scalars().one_or_none()
        return None if row is None else attempt_from_row(row)


# --------------------------------------------------------------------------- #
# Refusal classification
# --------------------------------------------------------------------------- #


def _assert_same_request(*, stored: ProviderAttempt, incoming: ProviderAttempt) -> None:
    """An existing key must describe the very same provider call."""
    same_identity = (
        stored.tenant_id == incoming.tenant_id
        and stored.run_id == incoming.run_id
        and stored.generation == incoming.generation
        and stored.attempt == incoming.attempt
    )
    if not same_identity:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            "attempt key is bound to a different run/generation/attempt",
        )
    if stored.request_digest != incoming.request_digest:
        raise AttemptConflict()


def _refuse_lost_finish(
    *,
    attempt: ProviderAttempt,
    target: AttemptState,
    stored: ProviderAttempt | None,
) -> NoReturn:
    """Turn a ``rowcount == 0`` into the reason the database implies."""
    if stored is None:
        raise AttemptRejected(
            AttemptRefusal.INVALID_RECORD,
            "attempt row disappeared; the journal cannot confirm the outcome",
        )
    if stored.state is target or stored.state in TERMINAL_ATTEMPT_STATES:
        raise AttemptRejected(
            AttemptRefusal.ALREADY_TERMINAL,
            f"attempt is already {stored.state.value}",
        )
    if stored.state is AttemptState.UNKNOWN and target not in (
        AttemptState.RECONCILED_SUCCEEDED,
        AttemptState.RECONCILED_FAILED,
    ):
        raise AttemptRejected(
            AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION,
            "an ambiguous provider outcome must be reconciled, never re-marked",
        )
    if stored.state is attempt.state:
        raise AttemptRejected(
            AttemptRefusal.ILLEGAL_TRANSITION,
            f"attempt is unchanged at {stored.state.value} but the transition did not apply",
        )
    raise AttemptRejected(
        AttemptRefusal.ILLEGAL_TRANSITION,
        f"attempt moved from {attempt.state.value} to {stored.state.value}",
    )


__all__ = [
    "MAX_UNRESOLVED_SCAN",
    "REVISION",
    "TABLE_NAME",
    "UNRESOLVED_STATE_VALUES",
    "SqlAlchemyProviderAttemptJournal",
    "attempt_from_row",
    "expired_unresolved_statement",
    "finish_statement",
    "finish_values",
    "read_statement",
    "start_insert_statement",
    "start_values",
    "unresolved_statement",
]
