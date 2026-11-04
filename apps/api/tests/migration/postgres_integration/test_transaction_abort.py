"""Criterion 3 — an aborted transaction writes nothing at all.

The control plane commits one ``ControlPlaneAtomicWrite``: run state, aggregate
cursor, domain event, outbox projection, queue intent and the idempotency
receipt travel together.  If any of those statements fails, the durable result
must be *nothing*:

* no run row, so the work did not happen;
* no event and no intent, so nothing is dispatched for work that did not happen;
* **and no committed receipt**, so the same ``Idempotency-Key`` can be retried
  and applied later instead of being permanently blocked by a half-written
  reservation.

The failure is injected with a real PostgreSQL trigger (``RAISE EXCEPTION``),
not a mocked adapter: the assertion is about transaction boundaries in the
database, which a mock cannot observe.  Each parametrised case fails a different
member of the atomic write, and every case asserts the same all-or-nothing
outcome.

The retry half (:func:`test_a_failed_write_can_be_retried_with_the_same_key`)
is what distinguishes "rolled back" from "already answered": after the trigger
is dropped, the identical command must return ``applied``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from prodavan.application.agent_control_plane import ControlPlaneWriteError, ControlPlaneWriteStatus
from prodavan.domain.agent.control_plane import AgentRunCreate

from ._seeds import CONTROL_PLANE_TABLES, NOW, control_plane, linkage, seed_scope
from ._support import sqlstate_of

pytestmark = pytest.mark.integration

#: The three tables whose failure is a "downstream write failed" abort.  Failing
#: any one of them must roll back the other two plus the run and the receipt.
ATOMIC_WRITE_TABLES = (
    "agent_control_plane_events",
    "agent_control_plane_outbox_events",
    "agent_control_plane_enqueue_intents",
)

FAILURE_MESSAGE = "MIG-INT-PG-01 injected write failure"


def _create_command(scope, *, key: str) -> AgentRunCreate:
    return AgentRunCreate(
        project_id=scope.project_id,
        session_id=None,
        requested_by_employee_id=scope.employee_id,
        idempotency_key=key,
        input_payload={"prompt": "abort"},
    )


async def _install_failure_trigger(pg_schema, table: str) -> None:
    """Make any INSERT into ``table`` raise, inside this isolated schema only."""
    function_name = f"pgint_fail_{table}"
    await pg_schema.execute(
        f"""
        CREATE OR REPLACE FUNCTION {function_name}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{FAILURE_MESSAGE} (in {table})';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    await pg_schema.execute(
        f"CREATE TRIGGER trg_{function_name} BEFORE INSERT ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
    )


async def _drop_failure_trigger(pg_schema, table: str) -> None:
    """Remove the injected failure so the same schema can be reused by the test."""
    function_name = f"pgint_fail_{table}"
    await pg_schema.execute(f"DROP TRIGGER trg_{function_name} ON {table}")
    await pg_schema.execute(f"DROP FUNCTION {function_name}()")


@pytest.mark.parametrize("failing_table", ATOMIC_WRITE_TABLES)
async def test_failure_inside_the_atomic_write_rolls_back_state_event_and_intent(
    pg_schema, failing_table: str
):
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    key = f"abort-{failing_table}"

    await _install_failure_trigger(pg_schema, failing_table)
    try:
        with pytest.raises(ControlPlaneWriteError) as failure:
            await service.submit_run(
                _create_command(scope, key=key),
                linkage=linkage(scope, trace_id=f"agent-command:{key}"),
                occurred_at=NOW,
            )
    finally:
        await _drop_failure_trigger(pg_schema, failing_table)

    # The adapter wraps the driver error; the original message must survive so an
    # operator can tell which write aborted.
    assert FAILURE_MESSAGE in str(failure.value.__cause__ or failure.value)

    counts = await pg_schema.counts(*CONTROL_PLANE_TABLES)
    assert counts == dict.fromkeys(CONTROL_PLANE_TABLES, 0), (
        f"a failed write left rows behind: {counts}"
    )


async def test_a_failed_write_can_be_retried_with_the_same_key(pg_schema):
    """Rollback must also roll back the reservation, so the retry is `applied`.

    A reservation that survived an aborted transaction would answer the retry
    with ``duplicate`` and a receipt whose ``result_payload`` is NULL — the
    repository raises ``RuntimeError`` for exactly that shape.  This test pins
    the positive outcome instead: no receipt, therefore a clean retry.
    """
    scope = await seed_scope(pg_schema)
    service = control_plane(pg_schema, scope)
    key = "abort-then-retry"
    link = linkage(scope, trace_id=f"agent-command:{key}")

    await _install_failure_trigger(pg_schema, "agent_control_plane_outbox_events")
    try:
        with pytest.raises(ControlPlaneWriteError):
            await service.submit_run(_create_command(scope, key=key), linkage=link, occurred_at=NOW)
    finally:
        await _drop_failure_trigger(pg_schema, "agent_control_plane_outbox_events")

    assert await pg_schema.count("agent_control_plane_commands") == 0

    applied = await service.submit_run(_create_command(scope, key=key), linkage=link, occurred_at=NOW)
    assert applied.status is ControlPlaneWriteStatus.APPLIED
    assert applied.run is not None

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
    # The retry is a normal replay target from now on.
    replay = await service.submit_run(_create_command(scope, key=key), linkage=link, occurred_at=NOW)
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE


async def test_a_constraint_violation_rolls_back_the_whole_transaction(pg_schema):
    """A statement rejected by a *check* constraint aborts its transaction.

    ``agent_run_leases`` enforces the all-or-nothing lease triple
    (``ck_agent_run_lease_token_pair``).  A half-lease written in the same
    transaction as a control-plane receipt must abort both, which is the
    database-level property the atomic-write contract depends on.
    """
    scope = await seed_scope(pg_schema)
    with pytest.raises(IntegrityError) as failure:
        async with pg_schema.transaction(commit=False) as conn:
            await conn.execute(
                text(
                    "INSERT INTO agent_control_plane_commands "
                    "(scope_key, tenant_id, project_id, operation, run_id, "
                    " command_idempotency_key, command_digest, status) "
                    "VALUES (:scope_key, :tenant, :project, 'agent_run.create', "
                    " 'agr_missing_parent', 'abort-key', :digest, 'in_progress')"
                ),
                {
                    "scope_key": "c" * 64,
                    "tenant": scope.tenant_id,
                    "project": scope.project_id,
                    "digest": "d" * 64,
                },
            )
            await conn.execute(
                # lease_owner NULL with a lease_token violates the pair check.
                text(
                    "INSERT INTO agent_run_leases "
                    "(run_id, tenant_id, project_id, lease_owner, lease_token) "
                    "VALUES ('agr_missing_parent', :tenant, :project, NULL, 'tok')"
                ),
                {"tenant": scope.tenant_id, "project": scope.project_id},
            )

    assert sqlstate_of(failure.value) in {"23514", "23503"}, (
        f"expected a check/FK violation, got {sqlstate_of(failure.value)}"
    )
    assert await pg_schema.count("agent_control_plane_commands") == 0
    assert await pg_schema.count("agent_run_leases") == 0
