"""Acceptance: the reconciler's two questions about unresolved attempts.

``find_unresolved(run_id=...)`` answers "may this run call the provider again?" —
the *newest* attempt whose state is ``started`` or ``unknown`` blocks it.
``find_expired_unresolved(now=...)`` answers "which attempts outlived their
deadline?" — the time-ordered sweep of the same unresolved set.

Both are read-only: an unresolved attempt leaves its state only through a
reconciliation write, so neither of these may mutate a row.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from prodavan.application.agent_runs.attempts import (
    CLOSED_ATTEMPT_STATES,
    TERMINAL_ATTEMPT_STATES,
    UNRESOLVED_ATTEMPT_STATES,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
    MAX_UNRESOLVED_SCAN,
    UNRESOLVED_STATE_VALUES,
    expired_unresolved_statement,
    unresolved_statement,
)

from .acceptance_fixtures import (
    DEADLINE,
    NOW,
    RUN_ID,
    TENANT,
    FakeResult,
    Script,
    Step,
    make_attempt,
    make_lease,
    record,
    rendered_sql,
    rendered_where,
    row_of,
    store_for,
)

# --------------------------------------------------------------------------- #
# The state sets the scan is built from
# --------------------------------------------------------------------------- #


def test_the_scan_states_are_the_contract_sets() -> None:
    assert set(UNRESOLVED_STATE_VALUES) == {state.value for state in UNRESOLVED_ATTEMPT_STATES}
    assert set(UNRESOLVED_STATE_VALUES) == {"started", "unknown"}


def test_no_terminal_state_is_in_the_unresolved_set() -> None:
    """A decided attempt must never block a run."""
    assert not (TERMINAL_ATTEMPT_STATES & UNRESOLVED_ATTEMPT_STATES)


def test_unknown_is_closed_but_not_terminal() -> None:
    """``unknown`` blocks the run (closed for now) yet reconciliation may move it."""
    assert AttemptState.UNKNOWN in CLOSED_ATTEMPT_STATES
    assert AttemptState.UNKNOWN not in TERMINAL_ATTEMPT_STATES
    assert AttemptState.UNKNOWN in UNRESOLVED_ATTEMPT_STATES


# --------------------------------------------------------------------------- #
# find_unresolved — statement shape
# --------------------------------------------------------------------------- #


def test_unresolved_scan_filters_on_state_and_run() -> None:
    sql = rendered_sql(unresolved_statement(run_id=RUN_ID))

    assert "agent_run_provider_attempts.state IN ('started', 'unknown')" in sql
    assert f"agent_run_provider_attempts.run_id = '{RUN_ID}'" in sql
    # exactly one row: the newest claim is the one a retry would collide with
    assert sql.endswith("LIMIT 1")
    assert "ORDER BY agent_run_provider_attempts.generation DESC" in sql
    assert "agent_run_provider_attempts.attempt DESC" in sql


def test_unresolved_scan_is_never_tenant_blind_when_a_tenant_is_supplied() -> None:
    sql = rendered_sql(unresolved_statement(run_id=RUN_ID, tenant_id=TENANT))

    assert f"agent_run_provider_attempts.tenant_id = '{TENANT}'" in sql


def test_unresolved_scan_has_no_lock_and_no_write() -> None:
    """A read must not take a row lock: reconciliation writes are the only writers."""
    sql = rendered_sql(unresolved_statement(run_id=RUN_ID))

    assert "FOR UPDATE" not in sql
    assert "UPDATE " not in sql and "DELETE " not in sql


def test_unresolved_scan_rejects_an_empty_run_id() -> None:
    with pytest.raises(ValueError):
        unresolved_statement(run_id="")


# --------------------------------------------------------------------------- #
# find_unresolved — adapter behaviour
# --------------------------------------------------------------------------- #


async def test_a_started_attempt_blocks_the_run() -> None:
    attempt = make_attempt()
    script = Script(Step("execute", FakeResult(rows=(row_of(attempt),))))

    unresolved = await store_for(script).find_unresolved(run_id=RUN_ID, tenant_id=TENANT)

    assert unresolved is not None
    assert unresolved.state is AttemptState.STARTED
    assert unresolved.is_unresolved
    assert script.kinds == ["execute"]


async def test_an_unknown_attempt_blocks_the_run() -> None:
    attempt = record(AttemptState.UNKNOWN)
    script = Script(Step("execute", FakeResult(rows=(row_of(attempt),))))

    unresolved = await store_for(script).find_unresolved(run_id=RUN_ID)

    assert unresolved is not None
    assert unresolved.state is AttemptState.UNKNOWN
    assert not unresolved.is_terminal


async def test_a_decided_run_reports_nothing_unresolved() -> None:
    script = Script(Step("execute", FakeResult(rows=())))

    assert await store_for(script).find_unresolved(run_id=RUN_ID) is None


async def test_a_corrupt_unresolved_row_is_a_refusal_not_a_blocker() -> None:
    attempt = make_attempt()
    corrupt = row_of(attempt)
    corrupt.request_digest = "not-a-digest"
    script = Script(Step("execute", FakeResult(rows=(corrupt,))))

    with pytest.raises(AttemptRejected) as rejected:
        await store_for(script).find_unresolved(run_id=RUN_ID)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


# --------------------------------------------------------------------------- #
# find_expired_unresolved — the reconciliation sweep
# --------------------------------------------------------------------------- #


def test_the_sweep_finds_only_started_attempts_past_their_deadline() -> None:
    sql = rendered_sql(expired_unresolved_statement(now=NOW))

    assert f"agent_run_provider_attempts.state = '{AttemptState.STARTED.value}'" in sql
    assert "agent_run_provider_attempts.deadline_at IS NOT NULL" in sql
    assert (
        "agent_run_provider_attempts.deadline_at <= '2026-11-04 09:00:00+00:00'" in sql
    )
    # oldest deadline first: the longest-abandoned attempt is reconciled first
    assert "ORDER BY agent_run_provider_attempts.deadline_at ASC" in sql
    assert sql.endswith(f"LIMIT {MAX_UNRESOLVED_SCAN}")


def test_the_sweep_does_not_pick_up_an_attempt_without_a_deadline() -> None:
    """A started attempt with no deadline is unresolved but never "expired"."""
    where = rendered_where(expired_unresolved_statement(now=NOW))

    assert "agent_run_provider_attempts.deadline_at IS NOT NULL" in where
    assert f"agent_run_provider_attempts.state = '{AttemptState.STARTED.value}'" in where
    # an ``unknown`` row is already decided-for-now: the sweep is about live calls
    assert "unknown" not in where


def test_the_sweep_is_bounded() -> None:
    with pytest.raises(ValueError):
        expired_unresolved_statement(now=NOW, limit=0)
    with pytest.raises(ValueError):
        expired_unresolved_statement(now=NOW, limit=MAX_UNRESOLVED_SCAN + 1)


def test_the_sweep_requires_an_aware_clock() -> None:
    with pytest.raises(ValueError):
        expired_unresolved_statement(now=NOW.replace(tzinfo=None))


async def test_the_sweep_returns_the_expired_records() -> None:
    expired = record()
    script = Script(Step("execute", FakeResult(rows=(row_of(expired),))))

    found = await store_for(script).find_expired_unresolved(now=DEADLINE + timedelta(seconds=1))

    assert len(found) == 1
    assert found[0].attempt_id == expired.attempt_id
    assert found[0].deadline_at == DEADLINE


async def test_the_sweep_returns_nothing_when_everything_is_still_live() -> None:
    script = Script(Step("execute", FakeResult(rows=())))

    assert await store_for(script).find_expired_unresolved(now=NOW) == ()


async def test_neither_scan_can_change_a_state() -> None:
    """Both statements are reads: no UPDATE, no DELETE, no INSERT, no lock."""
    for statement in (
        unresolved_statement(run_id=RUN_ID),
        expired_unresolved_statement(now=NOW),
        unresolved_statement(run_id=RUN_ID, tenant_id=TENANT),
    ):
        sql = rendered_sql(statement)
        assert sql.startswith("SELECT ")
        for forbidden in ("UPDATE ", "DELETE ", "INSERT ", "FOR UPDATE"):
            assert forbidden not in sql, forbidden


# --------------------------------------------------------------------------- #
# A run whose newest attempt is decided but whose older one is not
# --------------------------------------------------------------------------- #


def test_the_scan_is_ordered_newest_first_not_oldest() -> None:
    """A reconciliation that closed generation 1 must not hide generation 2."""
    newer = make_attempt(lease=make_lease(generation=2, attempt=2, lease_token="token-2"))
    sql = rendered_sql(unresolved_statement(run_id=RUN_ID))

    # the ordering keywords are what decide which row wins the LIMIT 1
    assert "generation DESC" in sql and "attempt DESC" in sql
    assert newer.generation == 2
