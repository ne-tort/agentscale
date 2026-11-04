"""Criterion 1 — concurrent idempotent reservation of one control-plane command.

Two requests carrying the same ``Idempotency-Key`` must not both reserve the
command: exactly one returns ``applied`` and the other replays the *original*
receipt, with no second run, event, outbox row or queue intent.

Two angles are covered:

* :func:`test_concurrent_duplicate_submit_yields_one_applied_and_one_replay`
  drives the real ``DurableAgentControlPlane`` + ``SqlAlchemyAgentControlPlane
  Repository`` over two independent connections.  ``_valid_scope`` takes
  ``FOR UPDATE`` locks on the tenant/project/cabinet rows, so the two callers
  serialise *before* the receipt: the loser then finds the committed receipt
  and replays it without writing anything.  That is the production replay path
  and it must produce ``duplicate``, not a second write.
* :func:`test_receipt_primary_key_arbitrates_the_race` forces the interleaving
  the first test cannot reach: the winner's transaction stays *open* after
  inserting the receipt (parked on an advisory lock) while the loser's INSERT
  blocks on the primary key.  This is the case the repository handles in its
  ``IntegrityError`` -> "read the winner's receipt" branch, and it is what keeps
  the reservation single-winner even when the receipt was not visible yet.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from prodavan.application.agent_control_plane import ControlPlaneWriteStatus
from prodavan.domain.agent.control_plane import AgentRunCreate

from ._seeds import CONTROL_PLANE_TABLES, NOW, control_plane, linkage, seed_scope
from ._support import advisory_gate, blocked_backends, hold_until_advisory

pytestmark = pytest.mark.integration

IDEMPOTENCY_KEY = "submit-once"
TRACE_ID = f"agent-command:{IDEMPOTENCY_KEY}"

#: The production receipt row.  ``scope_key`` is the repository's deterministic
#: digest of (tenant, project, operation, run, approval, aggregates, key); this
#: test only needs *a* key shared by both writers, because the arbiter under test
#: is the primary key, not the digest derivation.
INSERT_RECEIPT = text(
    """
    INSERT INTO agent_control_plane_commands
        (scope_key, tenant_id, project_id, operation, run_id, approval_id,
         command_idempotency_key, command_digest, status, result_payload)
    VALUES
        (:scope_key, :tenant_id, :project_id, 'agent_run.create', :run_id, NULL,
         :idempotency_key, :digest, :status, CAST(:result_payload AS jsonb))
    """
)


def _create_command(scope) -> AgentRunCreate:
    return AgentRunCreate(
        project_id=scope.project_id,
        session_id=None,
        requested_by_employee_id=scope.employee_id,
        idempotency_key=IDEMPOTENCY_KEY,
        input_payload={"prompt": "concurrent reservation"},
    )


# --------------------------------------------------------------------------- #
# real path
# --------------------------------------------------------------------------- #
async def test_concurrent_duplicate_submit_yields_one_applied_and_one_replay(pg_schema):
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    command = _create_command(scope)
    # Both callers must present the same linkage: the trace id is part of the
    # command digest, so a different trace would be a payload conflict instead
    # of a replay.  Production derives it from the idempotency key.
    link = linkage(scope, trace_id=TRACE_ID)

    first, second = await asyncio.gather(
        service.submit_run(command, linkage=link, occurred_at=NOW),
        service.submit_run(command, linkage=link, occurred_at=NOW),
    )

    statuses = sorted(result.status.value for result in (first, second))
    assert statuses == ["applied", "duplicate"], f"expected exactly one winner, got {statuses}"

    winner = first if first.status is ControlPlaneWriteStatus.APPLIED else second
    replay = second if winner is first else first

    # The replay restores the original receipt: same run, same event identity,
    # and every event is flagged as a duplicate.
    assert replay.run is not None and winner.run is not None
    assert replay.run.id == winner.run.id
    assert [item.event_id for item in replay.event_results] == [
        item.event_id for item in winner.event_results
    ]
    assert all(item.duplicate for item in replay.event_results)
    assert not any(item.duplicate for item in winner.event_results)

    # Exactly one of every durable artefact: the key reserved the command.  The
    # loser writes nothing at all — it returns the winner's receipt, so even the
    # receipt table keeps a single row.
    counts = await pg_schema.counts(*CONTROL_PLANE_TABLES)
    assert counts == {
        "agent_runs": 1,
        "agent_control_plane_run_scopes": 1,
        "agent_control_plane_aggregates": 1,
        "agent_control_plane_events": 1,
        "agent_control_plane_outbox_events": 1,
        "agent_control_plane_enqueue_intents": 1,
        "agent_control_plane_commands": 1,
    }
    assert await pg_schema.rows("SELECT status FROM agent_control_plane_commands") == [("applied",)]

    # The queue intent points at the single committed event, not at two events.
    intent_event = await pg_schema.scalar(
        "SELECT event_id FROM agent_control_plane_enqueue_intents"
    )
    event_ids = [row[0] for row in await pg_schema.rows("SELECT event_id FROM agent_control_plane_events")]
    assert intent_event in event_ids
    assert len(event_ids) == 1


async def test_replay_is_not_conflict_when_a_second_request_repeats_the_same_key(pg_schema):
    """Sequential replay is the baseline the concurrent case must match."""
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    command = _create_command(scope)
    link = linkage(scope, trace_id=TRACE_ID)

    first = await service.submit_run(command, linkage=link, occurred_at=NOW)
    replay = await service.submit_run(command, linkage=link, occurred_at=NOW)

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert replay.run.id == first.run.id
    # A repeated key with a *different* command body is a conflict, never a
    # silent overwrite of the original receipt.
    conflicting = await service.submit_run(
        command.model_copy(update={"input_payload": {"prompt": "different"}}),
        linkage=link,
        occurred_at=NOW,
    )
    assert conflicting.status is ControlPlaneWriteStatus.CONFLICT
    assert conflicting.reason == "idempotency_key_payload_conflict"
    assert await pg_schema.count("agent_control_plane_events") == 1


# --------------------------------------------------------------------------- #
# forced interleaving at the receipt itself
# --------------------------------------------------------------------------- #
async def test_receipt_primary_key_arbitrates_the_race(pg_schema):
    """The loser of the receipt INSERT must observe a duplicate key, not a duplicate row.

    Deterministic construction, no sleeps:

    1. transaction A inserts the receipt and then parks on an advisory lock, so
       its row is written but not committed;
    2. the test observes in ``pg_stat_activity`` that a backend is blocked on the
       receipt INSERT — that backend is B, which is waiting on A's index entry;
    3. releasing the advisory lock commits A, and B fails with SQLSTATE 23505.

    Step 2 is what makes this an interleaving test instead of a sequential one:
    the assertion only passes once B is genuinely inside PostgreSQL waiting on
    the unique index.
    """
    scope = await seed_scope(pg_schema)
    scope_key = "a" * 64
    gate_key = 891_001
    receipt = {
        "scope_key": scope_key,
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "run_id": "agr_receipt_race",
        "idempotency_key": IDEMPOTENCY_KEY,
        "digest": "b" * 64,
        "status": "in_progress",
        "result_payload": json.dumps({"status": "applied"}),
    }

    async with advisory_gate(pg_schema.engine, key=gate_key) as release_gate:
        holder_written = asyncio.Event()

        async def winner() -> None:
            async with pg_schema.transaction(commit=True) as conn:
                await conn.execute(INSERT_RECEIPT, receipt)
                holder_written.set()
                await hold_until_advisory(conn, key=gate_key)

        async def loser() -> None:
            async with pg_schema.transaction() as conn:
                await conn.execute(INSERT_RECEIPT, {**receipt, "status": "in_progress"})

        winner_task = asyncio.create_task(winner())
        await holder_written.wait()

        loser_task = asyncio.create_task(loser())
        # Deterministic proof that the loser reached the index, not a delay.
        await blocked_backends(
            pg_schema,
            needle="INSERT INTO agent_control_plane_commands",
            at_least=1,
        )
        assert not loser_task.done(), "the loser must still be waiting on the winner's index entry"

        await release_gate()
        await winner_task
        with pytest.raises(IntegrityError) as failure:
            await loser_task

    sqlstate = getattr(failure.value.orig, "sqlstate", None) or getattr(
        failure.value.orig, "pgcode", None
    )
    assert sqlstate == "23505", f"expected a unique violation, got {sqlstate}"

    # Exactly one committed receipt, and it is the winner's.
    rows = await pg_schema.rows(
        "SELECT scope_key, status FROM agent_control_plane_commands"
    )
    assert rows == [(scope_key, "in_progress")]
    assert await pg_schema.count("agent_control_plane_events") == 0
