"""Acceptance: the attempt transition matrix, enforced by SQL and by refusal.

The application journal owns the policy (``next_state``).  This artifact proves
that the *store* obeys the same policy when it is called directly, that the CAS
predicate is fenced by ``attempt_id`` **and** the observed state, and that
``rowcount == 0`` becomes a typed refusal instead of a vague zero:

* every legal transition of the matrix applies (and only those);
* ``unknown`` cannot be closed as a known outcome, either through
  ``finish_attempt`` (matrix) or by building the statement (reachability);
* a second finish of an already decided attempt is ``ALREADY_TERMINAL``;
* a lost race is classified from the stored row, never reported as success;
* the raw ``cas_finish`` keeps the protocol semantics (``0`` = lost race) for the
  application tier, which is a *return value*, not an exception.
"""

from __future__ import annotations

import pytest

from prodavan.application.agent_runs.attempts import (
    AttemptOutcome,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
)

from .acceptance_fixtures import (
    FAILURE_CODE,
    NOW,
    TENANT,
    FakeResult,
    Script,
    Step,
    record,
    row_of,
    store_for,
)

#: Every row of the contract's ``_TRANSITIONS`` table, with its outcome.
LEGAL_TRANSITIONS: tuple[tuple[AttemptState, AttemptOutcome, AttemptState], ...] = (
    (AttemptState.STARTED, AttemptOutcome.SUCCEEDED, AttemptState.KNOWN_SUCCEEDED),
    (AttemptState.STARTED, AttemptOutcome.FAILED, AttemptState.KNOWN_FAILED),
    (AttemptState.STARTED, AttemptOutcome.RETRYABLE, AttemptState.KNOWN_RETRYABLE),
    (AttemptState.STARTED, AttemptOutcome.UNKNOWN, AttemptState.UNKNOWN),
    (AttemptState.STARTED, AttemptOutcome.ABANDONED, AttemptState.ABANDONED),
    (AttemptState.STARTED, AttemptOutcome.DISCARDED_STALE, AttemptState.DISCARDED_STALE),
    (AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_SUCCEEDED, AttemptState.RECONCILED_SUCCEEDED),
    (AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_FAILED, AttemptState.RECONCILED_FAILED),
)

FAILURE_TARGETS = frozenset(
    {
        AttemptState.KNOWN_FAILED,
        AttemptState.KNOWN_RETRYABLE,
        AttemptState.RECONCILED_FAILED,
        AttemptState.DISCARDED_STALE,
    }
)


# --------------------------------------------------------------------------- #
# The whole matrix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("current", "outcome", "target"), LEGAL_TRANSITIONS)
async def test_every_legal_transition_applies(
    current: AttemptState, outcome: AttemptOutcome, target: AttemptState
) -> None:
    attempt = record(current, failure_code=FAILURE_CODE if current in FAILURE_TARGETS else None)
    script = Script(Step("execute", FakeResult(rowcount=1)))

    applied = await store_for(script).finish_attempt(
        attempt,
        outcome=outcome,
        at=NOW,
        failure_code=FAILURE_CODE if target in FAILURE_TARGETS else None,
    )

    assert applied.applied
    assert applied.rows == 1
    assert script.kinds == ["execute"]
    statement = str(script.calls[0][1])
    # exactly one UPDATE, fenced by the row identity *and* the observed state
    assert statement.startswith(f"UPDATE agent_run_provider_attempts SET state=")
    assert f"agent_run_provider_attempts.state = '{current.value}'" in statement
    assert f"state='{target.value}'" in statement


@pytest.mark.parametrize(("current", "outcome", "target"), LEGAL_TRANSITIONS)
def test_no_legal_transition_targets_a_started_state(
    current: AttemptState, outcome: AttemptOutcome, target: AttemptState
) -> None:
    """Every legal transition closes the attempt or records ambiguity."""
    assert target is not AttemptState.STARTED
    assert current is not target


# --------------------------------------------------------------------------- #
# unknown is closed for everyone except reconciliation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "outcome",
    [AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED, AttemptOutcome.RETRYABLE],
)
async def test_unknown_cannot_be_closed_as_a_known_outcome(outcome: AttemptOutcome) -> None:
    attempt = record(AttemptState.UNKNOWN)
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(attempt, outcome=outcome, at=NOW)

    assert rejected.value.reason is AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION
    # refused before any statement: an ambiguous attempt is never "marked"
    assert script.calls == []


async def test_unknown_cannot_be_abandoned_or_discarded_either() -> None:
    for outcome, code in (
        (AttemptOutcome.ABANDONED, None),
        (AttemptOutcome.DISCARDED_STALE, "agent_run.lease_lost"),
    ):
        script = Script()
        with pytest.raises(AttemptRejected) as rejected:
            await store_for(script).finish_attempt(
                record(AttemptState.UNKNOWN), outcome=outcome, at=NOW, failure_code=code
            )
        assert rejected.value.reason is AttemptRefusal.ILLEGAL_TRANSITION
        assert script.calls == []


async def test_reconciliation_is_the_only_way_out_of_unknown() -> None:
    attempt = record(AttemptState.UNKNOWN)
    script = Script(Step("execute", FakeResult(rowcount=1)))

    applied = await store_for(script).finish_attempt(
        attempt, outcome=AttemptOutcome.RECONCILED_SUCCEEDED, at=NOW
    )

    assert applied.applied
    assert "state='reconciled_succeeded'" in str(script.calls[0][1])


# --------------------------------------------------------------------------- #
# Illegal / terminal
# --------------------------------------------------------------------------- #


async def test_a_started_attempt_cannot_jump_straight_to_reconciled() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            record(), outcome=AttemptOutcome.RECONCILED_SUCCEEDED, at=NOW
        )

    assert rejected.value.reason is AttemptRefusal.ILLEGAL_TRANSITION
    assert script.calls == []


@pytest.mark.parametrize(
    "terminal",
    [
        AttemptState.KNOWN_SUCCEEDED,
        AttemptState.KNOWN_FAILED,
        AttemptState.KNOWN_RETRYABLE,
        AttemptState.RECONCILED_SUCCEEDED,
        AttemptState.RECONCILED_FAILED,
        AttemptState.ABANDONED,
        AttemptState.DISCARDED_STALE,
    ],
)
async def test_a_terminal_attempt_can_never_move_again(terminal: AttemptState) -> None:
    attempt = record(
        terminal,
        failure_code=FAILURE_CODE if terminal in FAILURE_TARGETS else None,
    )
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            attempt, outcome=AttemptOutcome.UNKNOWN, at=NOW
        )

    assert rejected.value.reason is AttemptRefusal.ALREADY_TERMINAL
    assert script.calls == []


async def test_a_second_finish_of_the_same_record_is_already_terminal() -> None:
    """The retry path: the outcome is on record, so the replay is refused."""
    attempt = record(AttemptState.KNOWN_SUCCEEDED)
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW
        )

    assert rejected.value.reason is AttemptRefusal.ALREADY_TERMINAL
    assert script.calls == []


# --------------------------------------------------------------------------- #
# rowcount == 0 is classified from the stored row
# --------------------------------------------------------------------------- #


async def test_a_finish_that_lost_the_race_to_the_same_state_is_already_terminal() -> None:
    attempt = record()
    winner = record(AttemptState.KNOWN_SUCCEEDED, provider_request_id="req-winner")
    script = Script(
        Step("execute", FakeResult(rowcount=0)),
        Step("execute", FakeResult(rows=(row_of(winner),))),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.ALREADY_TERMINAL
    assert "known_succeeded" in rejected.value.detail
    assert script.kinds == ["execute", "execute"]


async def test_a_finish_that_lost_the_race_to_another_outcome_is_illegal() -> None:
    attempt = record()
    winner = record(AttemptState.KNOWN_FAILED, failure_code=FAILURE_CODE)
    script = Script(
        Step("execute", FakeResult(rowcount=0)),
        Step("execute", FakeResult(rows=(row_of(winner),))),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.ILLEGAL_TRANSITION
    assert "known_failed" in rejected.value.detail


async def test_a_finish_that_lost_the_race_to_unknown_requires_reconciliation() -> None:
    attempt = record()
    ambiguous = record(AttemptState.UNKNOWN)
    script = Script(
        Step("execute", FakeResult(rowcount=0)),
        Step("execute", FakeResult(rows=(row_of(ambiguous),))),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION


async def test_a_finish_whose_row_vanished_is_an_invalid_record() -> None:
    """A cascade delete or a lost row is never read as "it probably worked"."""
    script = Script(
        Step("execute", FakeResult(rowcount=0)),
        Step("execute", FakeResult(rows=())),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(record(), outcome=AttemptOutcome.SUCCEEDED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


async def test_the_raw_cas_returns_zero_instead_of_raising() -> None:
    """The application tier's contract: ``0`` rows is a lost race, not an error."""
    attempt = record()
    script = Script(Step("execute", FakeResult(rowcount=0)))

    rows = await store_for(script).cas_finish(
        attempt_id=attempt.attempt_id,
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.KNOWN_SUCCEEDED,
        finished_at=NOW,
        failure_code=None,
        provider_request_id=None,
        metadata={},
        metadata_dropped=0,
    )

    assert rows == 0
    # no follow-up read: the primitive reports the count and nothing else
    assert script.kinds == ["execute"]


async def test_the_raw_cas_refuses_an_illegal_pair_before_issuing_sql() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).cas_finish(
            attempt_id="pat_any",
            expected_state=AttemptState.UNKNOWN,
            next_state=AttemptState.KNOWN_SUCCEEDED,
            finished_at=NOW,
            failure_code=None,
            provider_request_id=None,
            metadata={},
            metadata_dropped=0,
        )

    assert rejected.value.reason is AttemptRefusal.ILLEGAL_TRANSITION
    assert script.calls == []


# --------------------------------------------------------------------------- #
# Failure-code pairing (a row the journal could not read back)
# --------------------------------------------------------------------------- #


async def test_a_failure_transition_requires_a_failure_code() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(record(), outcome=AttemptOutcome.FAILED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert script.calls == []


async def test_a_free_text_failure_code_is_refused() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            record(),
            outcome=AttemptOutcome.FAILED,
            at=NOW,
            failure_code="the provider said no",
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


async def test_a_failure_code_longer_than_the_column_is_refused() -> None:
    """The contract allows 128 characters, the column is 64: the store narrows."""
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            record(),
            outcome=AttemptOutcome.FAILED,
            at=NOW,
            failure_code="agent_run." + "x" * 60,
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert script.calls == []


async def test_a_success_transition_cannot_carry_a_failure_code() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            record(),
            outcome=AttemptOutcome.SUCCEEDED,
            at=NOW,
            failure_code=FAILURE_CODE,
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert script.calls == []


async def test_an_unknown_outcome_cannot_carry_a_failure_code() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            record(),
            outcome=AttemptOutcome.UNKNOWN,
            at=NOW,
            failure_code=FAILURE_CODE,
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


async def test_finish_requires_a_journal_record() -> None:
    script = Script()

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(
            object(),  # type: ignore[arg-type]
            outcome=AttemptOutcome.SUCCEEDED,
            at=NOW,
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert script.calls == []


async def test_the_lost_race_read_is_tenant_narrowed() -> None:
    """A cross-tenant row must read as absent, so the refusal stays INVALID_RECORD."""
    attempt = record()
    script = Script(
        Step("execute", FakeResult(rowcount=0)),
        Step("execute", FakeResult(rows=())),
    )

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).finish_attempt(attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert f"agent_run_provider_attempts.tenant_id = '{TENANT}'" in str(script.calls[1][1])
