"""Criterion 2 — aggregate compare-and-set race.

Two transitions carrying the *same* ``expected_sequence`` must not both win:
one returns ``applied``, the other ``conflict``, and the loser must leave no
partial write behind (no second event, no second outbox row, no second queue
intent, no state change).

Two angles are covered:

* :func:`test_two_cancels_with_the_same_expected_sequence_have_one_winner`
  drives the real repository.  The application layer rejects the loser on the
  run status first (``run_status_conflict``); the aggregate cursor
  (``agent_control_plane_aggregates``) is the durable second line of defence.
* :func:`test_aggregate_cursor_is_a_compare_and_set_on_one_row` isolates the
  cursor itself with a forced interleaving: the winner's ``UPDATE`` holds the
  row lock while the loser's identical ``UPDATE`` is parked in the database, so
  the loser re-evaluates the predicate against the committed row and affects
  zero rows.  A stale and a fresh expectation are then checked one after the
  other to prove the cursor only moves forward.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from prodavan.application.agent_control_plane import ControlPlaneWriteStatus
from prodavan.domain.agent.control_plane import AgentRunCreate, AgentRunStatus

from ._seeds import NOW, control_plane, linkage, seed_scope
from ._support import advisory_gate, blocked_backends, hold_until_advisory

pytestmark = pytest.mark.integration

CAS_UPDATE = text(
    """
    UPDATE agent_control_plane_aggregates
       SET last_sequence = :next_sequence,
           status = :target_status,
           updated_at = now()
     WHERE aggregate_type = :aggregate_type
       AND aggregate_id = :aggregate_id
       AND last_sequence = :expected_sequence
       AND status = :expected_status
    """
)
"""The exact predicate the repository uses for its cursor CAS (``_apply``)."""


async def _start_run(pg_schema, scope, *, key: str) -> tuple[str, int]:
    """Create one queued run and return ``(run_id, sequence)``."""
    service = control_plane(pg_schema, scope)
    created = await service.submit_run(
        AgentRunCreate(
            project_id=scope.project_id,
            session_id=None,
            requested_by_employee_id=scope.employee_id,
            idempotency_key=key,
            input_payload={"prompt": "cas"},
        ),
        linkage=linkage(scope, trace_id=f"agent-command:{key}"),
        occurred_at=NOW,
    )
    assert created.status is ControlPlaneWriteStatus.APPLIED
    assert created.run is not None
    return created.run.id, 1


# --------------------------------------------------------------------------- #
# real path
# --------------------------------------------------------------------------- #
async def test_two_cancels_with_the_same_expected_sequence_have_one_winner(pg_schema):
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    run_id, sequence = await _start_run(pg_schema, scope, key="cas-create")

    async def cancel(key: str):
        return await service.cancel_run(
            run_id=run_id,
            linkage=linkage(scope, trace_id=f"agent-command:{key}"),
            expected_sequence=sequence,
            expected_status=AgentRunStatus.QUEUED,
            command_idempotency_key=key,
            occurred_at=NOW,
        )

    first, second = await asyncio.gather(cancel("cancel-a"), cancel("cancel-b"))

    statuses = sorted(result.status.value for result in (first, second))
    assert statuses == ["applied", "conflict"], f"expected one winner, got {statuses}"
    loser = first if first.status is ControlPlaneWriteStatus.CONFLICT else second
    assert loser.reason in {"run_status_conflict", "aggregate_sequence_conflict"}

    # The cursor advanced exactly once: created(1) -> cancelled(2).
    rows = await pg_schema.rows(
        "SELECT last_sequence, status FROM agent_control_plane_aggregates "
        "WHERE aggregate_type = 'agent_run' AND aggregate_id = :run",
        {"run": run_id},
    )
    assert len(rows) == 1, "the loser must not create a second cursor row"
    last_sequence, aggregate_status = rows[0]
    assert last_sequence == 2
    assert aggregate_status == AgentRunStatus.CANCELLED.value

    run_status = await pg_schema.scalar(
        "SELECT status FROM agent_runs WHERE id = :run", {"run": run_id}
    )
    assert run_status == AgentRunStatus.CANCELLED.value

    # One created event and one cancelled event, no partial writes from the loser.
    event_types = [
        row[0]
        for row in await pg_schema.rows(
            "SELECT event_type FROM agent_control_plane_events ORDER BY sequence"
        )
    ]
    assert event_types == ["agent_run.created", "agent_run.cancelled"]
    assert await pg_schema.count("agent_control_plane_outbox_events") == 2
    assert await pg_schema.count("agent_control_plane_enqueue_intents") == 2
    # Both attempts recorded their outcome; only one of them is `applied`.
    receipt_statuses = {
        row[0]: row[1]
        for row in await pg_schema.rows(
            "SELECT status, count(*) FROM agent_control_plane_commands GROUP BY status"
        )
    }
    assert receipt_statuses == {"applied": 1, "conflict": 1}
    # The loser wrote no aggregate of its own: still exactly one cursor row.
    assert await pg_schema.count("agent_control_plane_aggregates") == 1


async def test_a_second_cancel_with_a_fresh_sequence_cannot_be_replayed(pg_schema):
    """After the winner commits, the stale sequence is refused, not re-applied."""
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    run_id, sequence = await _start_run(pg_schema, scope, key="cas-stale-create")

    applied = await service.cancel_run(
        run_id=run_id,
        linkage=linkage(scope, trace_id="agent-command:cancel-first"),
        expected_sequence=sequence,
        expected_status=AgentRunStatus.QUEUED,
        command_idempotency_key="cancel-first",
        occurred_at=NOW,
    )
    assert applied.status is ControlPlaneWriteStatus.APPLIED

    stale = await service.cancel_run(
        run_id=run_id,
        linkage=linkage(scope, trace_id="agent-command:cancel-second"),
        expected_sequence=sequence,  # the same, already-consumed expectation
        expected_status=AgentRunStatus.QUEUED,
        command_idempotency_key="cancel-second",
        occurred_at=NOW,
    )
    assert stale.status is ControlPlaneWriteStatus.CONFLICT
    assert await pg_schema.count("agent_control_plane_events") == 2
    assert await pg_schema.scalar(
        "SELECT last_sequence FROM agent_control_plane_aggregates"
    ) == 2


# --------------------------------------------------------------------------- #
# cursor in isolation
# --------------------------------------------------------------------------- #
async def test_aggregate_cursor_is_a_compare_and_set_on_one_row(pg_schema):
    """Forced interleaving: one winner, one zero-row update, no phantom cursor."""
    run_id = "agr_cursor_cas"
    await pg_schema.execute(
        "INSERT INTO agent_control_plane_aggregates "
        "(aggregate_type, aggregate_id, last_sequence, status) "
        "VALUES ('agent_run', :run, 5, 'running')",
        {"run": run_id},
    )
    gate_key = 891_002
    params = {
        "aggregate_type": "agent_run",
        "aggregate_id": run_id,
        "expected_sequence": 5,
        "next_sequence": 6,
        "expected_status": "running",
        "target_status": "cancelled",
    }

    async with advisory_gate(pg_schema.engine, key=gate_key) as release_gate:
        winner_updated = asyncio.Event()

        async def winner() -> int:
            async with pg_schema.transaction(commit=True) as conn:
                result = await conn.execute(CAS_UPDATE, params)
                winner_updated.set()
                await hold_until_advisory(conn, key=gate_key)
                return result.rowcount

        async def loser() -> int:
            async with pg_schema.transaction(commit=True) as conn:
                result = await conn.execute(CAS_UPDATE, params)
                return result.rowcount

        winner_task = asyncio.create_task(winner())
        await winner_updated.wait()
        loser_task = asyncio.create_task(loser())
        await blocked_backends(
            pg_schema,
            needle="UPDATE agent_control_plane_aggregates",
            at_least=1,
        )
        assert not loser_task.done(), "the loser must wait for the winner's row lock"
        await release_gate()

        rowcounts = sorted([await winner_task, await loser_task])

    assert rowcounts == [0, 1], f"exactly one CAS may win, got rowcounts {rowcounts}"

    stored_rows = await pg_schema.rows(
        "SELECT last_sequence, status FROM agent_control_plane_aggregates "
        "WHERE aggregate_id = :run",
        {"run": run_id},
    )
    assert [tuple(row) for row in stored_rows] == [(6, "cancelled")]

    # A stale expectation is refused; only the current cursor may move it.
    async with pg_schema.transaction(commit=True) as conn:
        stale = await conn.execute(CAS_UPDATE, params)
        fresh = await conn.execute(
            CAS_UPDATE,
            {**params, "expected_sequence": 6, "next_sequence": 7, "expected_status": "cancelled"},
        )
    assert (stale.rowcount, fresh.rowcount) == (0, 1)
    assert await pg_schema.count("agent_control_plane_aggregates") == 1


async def test_cursor_cas_does_not_touch_other_aggregates(pg_schema):
    """The predicate is (type, id) scoped, so a parallel aggregate is untouched."""
    await pg_schema.execute(
        "INSERT INTO agent_control_plane_aggregates "
        "(aggregate_type, aggregate_id, last_sequence, status) VALUES "
        "('agent_run', 'agr_one', 1, 'queued'), ('agent_run', 'agr_two', 1, 'queued')"
    )
    async with pg_schema.transaction(commit=True) as conn:
        result = await conn.execute(
            CAS_UPDATE,
            {
                "aggregate_type": "agent_run",
                "aggregate_id": "agr_one",
                "expected_sequence": 1,
                "next_sequence": 2,
                "expected_status": "queued",
                "target_status": "cancelled",
            },
        )
        rowcount = result.rowcount
    assert rowcount == 1
    rows = await pg_schema.rows(
        "SELECT aggregate_id, last_sequence FROM agent_control_plane_aggregates "
        "ORDER BY aggregate_id"
    )
    assert [tuple(row) for row in rows] == [("agr_one", 2), ("agr_two", 1)]
