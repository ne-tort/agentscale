"""Criterion 6 — the partial unique index on active canonical grants.

``dynamic_app_capability_grants`` carries ``uq_cap_grant_active_identity``, a
unique index over the whole authorization identity with the predicate
``revoked_at IS NULL``.  Its contract is exactly two-sided:

* while a grant is **active** the identity is unique — an identical second grant
  is impossible, and a concurrent one loses with SQLSTATE 23505;
* once the first grant is **revoked** (``revoked_at`` set) the same identity may
  be granted again, which is how grant rotation works without deleting history.

Both sides are asserted, plus a concurrency case with a forced interleaving and a
negative case proving the index is not over-broad.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ._seeds import (
    INSERT_GRANT,
    grant_values,
    seed_definition_parents,
    seed_tenant_scope_registry,
)
from ._support import advisory_gate, blocked_backends, hold_until_advisory, sqlstate_of

pytestmark = pytest.mark.integration

INDEX_NAME = "uq_cap_grant_active_identity"

REVOKE_GRANT = text(
    """
    UPDATE dynamic_app_capability_grants
       SET revoked_at = now(),
           revoked_by = 'pgint-fixture',
           revoke_reason = 'rotation'
     WHERE id = :id
    """
)


async def _scaffold(pg_schema):
    """Definition/version/registry parents for an active tenant-scope grant."""
    parents = await seed_definition_parents(pg_schema, owner_state="active")
    registry_id = await seed_tenant_scope_registry(pg_schema, parents)
    return parents, registry_id


async def _insert_grant(pg_schema, values) -> None:
    async with pg_schema.engine.begin() as conn:
        await conn.execute(INSERT_GRANT, values)


# --------------------------------------------------------------------------- #
# the index exists with the predicate we expect
# --------------------------------------------------------------------------- #
async def test_the_active_identity_index_is_partial_on_revoked_at(pg_schema):
    definition = await pg_schema.scalar(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = :schema AND indexname = :name",
        {"schema": pg_schema.name, "name": INDEX_NAME},
    )
    assert definition is not None, f"{INDEX_NAME} is missing from {pg_schema.name}"
    assert "UNIQUE INDEX" in definition
    assert "WHERE (revoked_at IS NULL)" in definition, definition


# --------------------------------------------------------------------------- #
# two active grants are impossible
# --------------------------------------------------------------------------- #
async def test_two_active_grants_with_the_same_identity_collide(pg_schema):
    parents, registry_id = await _scaffold(pg_schema)
    first = grant_values(parents, registry_id=registry_id, idempotency_key="grant-a")
    second = grant_values(parents, registry_id=registry_id, idempotency_key="grant-b")

    await _insert_grant(pg_schema, first)
    with pytest.raises(IntegrityError) as failure:
        await _insert_grant(pg_schema, second)

    assert sqlstate_of(failure.value) == "23505"
    assert INDEX_NAME in str(failure.value)
    assert await pg_schema.count("dynamic_app_capability_grants") == 1
    active = await pg_schema.scalar(
        "SELECT count(*) FROM dynamic_app_capability_grants WHERE revoked_at IS NULL"
    )
    assert active == 1


async def test_concurrent_identical_grants_have_exactly_one_winner(pg_schema):
    """Forced interleaving at the unique index, not a sequential retry.

    The first transaction inserts the grant and then parks on an advisory lock,
    so its index entry is written but not committed.  The second insert blocks on
    that entry; the test waits until a backend is observed waiting on the grant
    INSERT, then releases the first.  The second must fail with 23505.
    """
    parents, registry_id = await _scaffold(pg_schema)
    first = grant_values(parents, registry_id=registry_id, idempotency_key="grant-race-a")
    second = grant_values(parents, registry_id=registry_id, idempotency_key="grant-race-b")
    gate_key = 891_003

    async with advisory_gate(pg_schema.engine, key=gate_key) as release_gate:
        winner_inserted = asyncio.Event()

        async def winner() -> None:
            async with pg_schema.transaction(commit=True) as conn:
                await conn.execute(INSERT_GRANT, first)
                winner_inserted.set()
                await hold_until_advisory(conn, key=gate_key)

        async def loser() -> None:
            async with pg_schema.transaction(commit=True) as conn:
                await conn.execute(INSERT_GRANT, second)

        winner_task = asyncio.create_task(winner())
        await winner_inserted.wait()
        loser_task = asyncio.create_task(loser())
        await blocked_backends(
            pg_schema,
            needle="INSERT INTO dynamic_app_capability_grants",
            at_least=1,
        )
        assert not loser_task.done(), "the loser must wait on the winner's index entry"
        await release_gate()
        await winner_task
        with pytest.raises(IntegrityError) as failure:
            await loser_task

    assert sqlstate_of(failure.value) == "23505"
    survivors = await pg_schema.rows(
        "SELECT id, idempotency_key FROM dynamic_app_capability_grants"
    )
    assert survivors == [(first["id"], "grant-race-a")]


# --------------------------------------------------------------------------- #
# after revocation the identity may be granted again
# --------------------------------------------------------------------------- #
async def test_a_revoked_grant_frees_the_identity(pg_schema):
    parents, registry_id = await _scaffold(pg_schema)
    first = grant_values(parents, registry_id=registry_id, idempotency_key="grant-rotate-1")
    second = grant_values(parents, registry_id=registry_id, idempotency_key="grant-rotate-2")
    third = grant_values(parents, registry_id=registry_id, idempotency_key="grant-rotate-3")

    await _insert_grant(pg_schema, first)
    await pg_schema.execute(REVOKE_GRANT, {"id": first["id"]})

    # Same identity, new row: allowed because the old one is no longer active.
    await _insert_grant(pg_schema, second)
    assert await pg_schema.count("dynamic_app_capability_grants") == 2
    assert (
        await pg_schema.scalar(
            "SELECT count(*) FROM dynamic_app_capability_grants WHERE revoked_at IS NULL"
        )
        == 1
    )

    # Rotation can repeat: revoke the second, insert a third.
    await pg_schema.execute(REVOKE_GRANT, {"id": second["id"]})
    await _insert_grant(pg_schema, third)
    assert await pg_schema.count("dynamic_app_capability_grants") == 3
    active = await pg_schema.rows(
        "SELECT id FROM dynamic_app_capability_grants WHERE revoked_at IS NULL"
    )
    assert active == [(third["id"],)]


async def test_a_failed_second_grant_leaves_the_first_active(pg_schema):
    """The losing insert must not disturb the live grant in any way."""
    parents, registry_id = await _scaffold(pg_schema)
    first = grant_values(parents, registry_id=registry_id, idempotency_key="grant-keep")
    second = grant_values(parents, registry_id=registry_id, idempotency_key="grant-lose")

    await _insert_grant(pg_schema, first)
    with pytest.raises(IntegrityError):
        await _insert_grant(pg_schema, second)

    row = (
        await pg_schema.rows(
            "SELECT id, revoked_at, grant_generation, writer_generation "
            "FROM dynamic_app_capability_grants"
        )
    )[0]
    assert row == (first["id"], None, 1, 1)

    # And the identity is still free only after an explicit revocation.
    await pg_schema.execute(REVOKE_GRANT, {"id": first["id"]})
    await _insert_grant(pg_schema, second)
    assert await pg_schema.count("dynamic_app_capability_grants") == 2


# --------------------------------------------------------------------------- #
# the index is not over-broad
# --------------------------------------------------------------------------- #
async def test_grants_differing_in_identity_are_both_allowed(pg_schema):
    """Uniqueness is over the identity tuple, so a different capability coexists."""
    parents, registry_id = await _scaffold(pg_schema)
    read_grant = grant_values(parents, registry_id=registry_id, idempotency_key="grant-read")
    other_capability = {
        **grant_values(parents, registry_id=registry_id, idempotency_key="grant-write"),
        "capability_key": "equipment.write",
    }
    other_scope_subject = {
        **grant_values(parents, registry_id=registry_id, idempotency_key="grant-other-subject"),
        "subject_id": "emp_pgint_other",
    }

    await _insert_grant(pg_schema, read_grant)
    await _insert_grant(pg_schema, other_capability)
    await _insert_grant(pg_schema, other_scope_subject)

    assert await pg_schema.count("dynamic_app_capability_grants") == 3
    assert (
        await pg_schema.scalar(
            "SELECT count(*) FROM dynamic_app_capability_grants WHERE revoked_at IS NULL"
        )
        == 3
    )
