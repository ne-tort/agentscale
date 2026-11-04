"""Acceptance: ``insert_start`` is idempotent, conflicts on a changed digest.

Every case here is about the *same* statement arriving twice: a retried HTTP
request, a re-delivered queue message or a second worker in the same generation.
One attempt row must survive all of them, and a different provider call under the
same key must be a conflict rather than an overwrite.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import ProgrammingError

from prodavan.application.agent_runs.attempts import (
    ATTEMPT_JOURNAL_BLOCKER,
    AttemptConflict,
    AttemptJournalUnavailable,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
)

from .acceptance_fixtures import (
    RUN_ID,
    TENANT,
    FakeResult,
    Script,
    Step,
    make_attempt,
    make_lease,
    record,
    row_of,
    store_for,
)


def created(attempt) -> Script:
    """The insert returned a row: this call created the attempt."""
    return Script(Step("scalar", FakeResult(scalar=attempt.attempt_id)))


def already_stored(attempt) -> Script:
    """The insert hit the conflict target, then the read found the winner's row."""
    return Script(
        Step("scalar", FakeResult(scalar=None)),
        Step("execute", FakeResult(rows=(row_of(attempt),))),
    )


# --------------------------------------------------------------------------- #
# First start
# --------------------------------------------------------------------------- #


async def test_start_returns_a_created_attempt_and_issues_one_statement() -> None:
    attempt = make_attempt()
    script = created(attempt)

    start = await store_for(script).start_attempt(attempt)

    assert start.created is True
    assert start.attempt is attempt
    assert script.kinds == ["scalar"]
    # the insert is the arbiter: no read-then-write, nothing pending afterwards
    assert script.pending == 0


async def test_start_writes_a_started_row_only() -> None:
    attempt = make_attempt()
    script = created(attempt)

    await store_for(script).start_attempt(attempt)

    statement = script.calls[0][1]
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in str(statement)
    assert "RETURNING" in str(statement)


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


async def test_repeated_start_returns_the_stored_row_and_does_not_create() -> None:
    attempt = make_attempt()
    stored = record(provider_request_id="req-from-the-first-call")
    script = already_stored(stored)

    start = await store_for(script).start_attempt(attempt)

    assert start.created is False
    # the *stored* record is returned, not the caller's copy of it
    assert start.attempt.provider_request_id == "req-from-the-first-call"
    assert start.attempt.idempotency_key == attempt.idempotency_key
    assert script.kinds == ["scalar", "execute"]


async def test_a_replay_with_the_same_digest_does_not_raise() -> None:
    attempt = make_attempt()
    stored = make_attempt()
    script = already_stored(stored)

    start = await store_for(script).start_attempt(attempt)

    assert start.attempt.request_digest == attempt.request_digest
    assert start.created is False


# --------------------------------------------------------------------------- #
# Conflict
# --------------------------------------------------------------------------- #


async def test_the_same_key_with_a_different_digest_is_a_conflict() -> None:
    attempt = make_attempt()
    different_call = make_attempt(prompt="a different prompt")
    assert different_call.idempotency_key == attempt.idempotency_key
    assert different_call.request_digest != attempt.request_digest
    script = already_stored(different_call)

    with pytest.raises(AttemptConflict) as rejected:
        await store_for(script).start_attempt(attempt)

    assert rejected.value.reason is AttemptRefusal.DIGEST_CONFLICT
    # the conflict is detected after the read and never overwrites the row
    assert script.kinds == ["scalar", "execute"]
    assert script.pending == 0


async def test_conflict_leaves_no_room_for_a_second_write() -> None:
    attempt = make_attempt()
    script = already_stored(make_attempt(prompt="a different prompt"))

    with pytest.raises(AttemptConflict):
        await store_for(script).start_attempt(attempt)

    # exactly one insert and one read: no update was attempted behind the conflict
    assert len(script.calls) == 2


async def test_a_stored_row_that_violates_the_contract_is_never_returned() -> None:
    """A forged key (same key, another run) is refused, not quietly trusted."""
    attempt = make_attempt()
    forged = row_of(make_attempt())
    forged.run_id = "agr-someone-else"
    script = Script(
        Step("scalar", FakeResult(scalar=None)),
        Step("execute", FakeResult(rows=(forged,))),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).start_attempt(attempt)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


# --------------------------------------------------------------------------- #
# Fail closed before touching the database
# --------------------------------------------------------------------------- #


async def test_start_refuses_a_record_that_is_not_freshly_started() -> None:
    finished = record(AttemptState.KNOWN_SUCCEEDED)
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).start_attempt(finished)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    # the statement is built before the transaction opens, so nothing was issued
    assert script.calls == []


async def test_start_refuses_a_non_attempt_value() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).start_attempt("not an attempt")  # type: ignore[arg-type]

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert script.calls == []


async def test_a_missing_table_is_a_named_blocker_not_a_crash() -> None:
    attempt = make_attempt()
    script = Script(
        Step(
            "scalar",
            error=ProgrammingError(
                "INSERT INTO agent_run_provider_attempts ...",
                {},
                Exception('relation "agent_run_provider_attempts" does not exist'),
            ),
        )
    )

    with pytest.raises(AttemptJournalUnavailable) as unavailable:
        await store_for(script).start_attempt(attempt)

    assert unavailable.value.blocker == ATTEMPT_JOURNAL_BLOCKER
    assert "2026110311" in str(unavailable.value)


async def test_an_unrelated_programming_error_is_not_relabelled() -> None:
    attempt = make_attempt()
    script = Script(
        Step(
            "scalar",
            error=ProgrammingError(
                "INSERT INTO agent_run_provider_attempts ...",
                {},
                Exception('column "not_a_column" does not exist'),
            ),
        )
    )

    with pytest.raises(ProgrammingError):
        await store_for(script).start_attempt(attempt)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


async def test_read_returns_the_stored_attempt() -> None:
    attempt = make_attempt()
    script = Script(Step("execute", FakeResult(rows=(row_of(attempt),))))

    stored = await store_for(script).read_attempt(attempt_id=attempt.attempt_id, tenant_id=TENANT)

    assert stored is not None
    assert stored.attempt_id == attempt.attempt_id
    assert stored.state is AttemptState.STARTED
    assert stored.metadata == {"provider": "agent-runtime", "phase": "dispatch"}


async def test_read_returns_none_for_an_unknown_or_foreign_attempt() -> None:
    script = Script(Step("execute", FakeResult(rows=())))

    assert await store_for(script).read_attempt(attempt_id="pat_missing") is None


async def test_read_refuses_a_corrupt_row_instead_of_half_trusting_it() -> None:
    attempt = make_attempt()
    corrupt = row_of(attempt)
    corrupt.state = "known_succeeded"  # closed state without finished_at
    script = Script(Step("execute", FakeResult(rows=(corrupt,))))

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).read_attempt(attempt_id=attempt.attempt_id)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


async def test_read_rejects_an_empty_identifier() -> None:
    script = Script()

    with pytest.raises(ValueError):
        await store_for(script).read_attempt(attempt_id="")

    assert script.calls == []


def test_attempt_identity_is_run_scoped_while_the_digest_is_not() -> None:
    """The key separates runs; the request digest alone could not."""
    first = make_attempt()
    second = make_attempt(lease=make_lease(run_id=RUN_ID + "-2"))

    assert first.request_digest == second.request_digest
    assert first.idempotency_key != second.idempotency_key
    assert first.attempt_id != second.attempt_id
