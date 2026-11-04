"""Criterion 5 — the deferrable reciprocal FK cycle of M05.

``m05_plugin_instances`` and ``scope_registry`` point at each other with
``DEFERRABLE INITIALLY DEFERRED`` composite foreign keys (revision
``2026110303``; validated separately by ``2026110308``).  That is the only shape
in which a plugin-instance registry row can be created at all: neither row can
exist first, so the pair has to be inserted inside one transaction and checked at
``COMMIT``.

Covered:

* both sides inserted in **one** transaction commit successfully, in either
  order (the cycle is not order-dependent);
* either side alone fails **at commit** with SQLSTATE 23503, and the failing
  transaction leaves no row behind — the deferral does not weaken the check,
  it only moves it;
* a tuple that resolves to the wrong instance is rejected at commit as well, so
  the composite key is not satisfied by "some row with the same tenant";
* ``SET CONSTRAINTS ... IMMEDIATE`` makes the same violation fail at the
  statement, which proves the constraint really is deferrable rather than
  simply unenforced;
* the constraints are introspected in ``pg_constraint`` and must be
  ``condeferrable`` + ``condeferred`` — a future revision that silently dropped
  the deferral would fail this test instead of only breaking production writes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ._seeds import (
    INSERT_PLUGIN_INSTANCE,
    INSERT_PLUGIN_SCOPE_REGISTRY,
    plugin_instance_values,
    plugin_scope_registry_values,
    seed_definition_parents,
)
from ._support import sqlstate_of

pytestmark = pytest.mark.integration

INSTANCE_ID = "pi_cycle_001"
REGISTRY_ID = "scope_cycle_001"
RECIPROCAL_FK_NAMES = (
    "fk_m05_instance_registry_tenant_link",
    "fk_m05_registry_instance_tenant_link",
)


async def _parents(pg_schema):
    """A quarantined tenant anchor: required by the staged M05 partition."""
    return await seed_definition_parents(pg_schema, owner_state="quarantined")


# --------------------------------------------------------------------------- #
# the cycle must be creatable in one transaction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("instance_first", [True, False])
async def test_both_sides_of_the_cycle_commit_in_one_transaction(pg_schema, instance_first: bool):
    parents = await _parents(pg_schema)
    instance = plugin_instance_values(parents, instance_id=INSTANCE_ID, registry_id=REGISTRY_ID)
    registry = plugin_scope_registry_values(
        parents, registry_id=REGISTRY_ID, instance_id=INSTANCE_ID
    )

    async with pg_schema.engine.connect() as conn:
        trans = await conn.begin()
        if instance_first:
            await conn.execute(INSERT_PLUGIN_INSTANCE, instance)
            await conn.execute(INSERT_PLUGIN_SCOPE_REGISTRY, registry)
        else:
            await conn.execute(INSERT_PLUGIN_SCOPE_REGISTRY, registry)
            await conn.execute(INSERT_PLUGIN_INSTANCE, instance)
        # Neither row can be committed alone; the pair is valid only together.
        await trans.commit()

    assert await pg_schema.count("m05_plugin_instances") == 1
    assert await pg_schema.count("scope_registry") == 1
    linked = await pg_schema.scalar(
        "SELECT scope_registry_id FROM m05_plugin_instances WHERE id = :id",
        {"id": INSTANCE_ID},
    )
    assert linked == REGISTRY_ID
    back = await pg_schema.scalar(
        "SELECT plugin_instance_id FROM scope_registry WHERE id = :id",
        {"id": REGISTRY_ID},
    )
    assert back == INSTANCE_ID


# --------------------------------------------------------------------------- #
# the deferral moves the check, it does not weaken it
# --------------------------------------------------------------------------- #
async def test_an_instance_without_its_registry_row_fails_at_commit(pg_schema):
    parents = await _parents(pg_schema)
    instance = plugin_instance_values(parents, instance_id=INSTANCE_ID, registry_id=REGISTRY_ID)

    async with pg_schema.engine.connect() as conn:
        trans = await conn.begin()
        # The INSERT is accepted: the constraint is deferred to COMMIT.
        await conn.execute(INSERT_PLUGIN_INSTANCE, instance)
        with pytest.raises(IntegrityError) as failure:
            await trans.commit()

    assert sqlstate_of(failure.value) == "23503", "a missing cycle partner must be a FK violation"
    assert "fk_m05_instance_registry_tenant_link" in str(failure.value)
    assert await pg_schema.count("m05_plugin_instances") == 0


async def test_a_registry_row_without_its_instance_fails_at_commit(pg_schema):
    parents = await _parents(pg_schema)
    registry = plugin_scope_registry_values(
        parents, registry_id=REGISTRY_ID, instance_id=INSTANCE_ID
    )

    async with pg_schema.engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(INSERT_PLUGIN_SCOPE_REGISTRY, registry)
        with pytest.raises(IntegrityError) as failure:
            await trans.commit()

    assert sqlstate_of(failure.value) == "23503"
    assert "fk_m05_registry_instance_tenant_link" in str(failure.value)
    assert await pg_schema.count("scope_registry") == 0


async def test_the_pair_must_resolve_to_the_same_instance(pg_schema):
    """A registry row of another instance cannot satisfy the composite key.

    ``(id, plugin_instance_id, tenant_id)`` is the referenced tuple, so pointing
    the instance at a registry row that names a *different* instance must fail at
    commit — the cycle is an identity proof, not a "some row exists" proof.
    """
    parents = await _parents(pg_schema)
    foreign_registry = "scope_cycle_other"
    registry = plugin_scope_registry_values(
        parents, registry_id=foreign_registry, instance_id="pi_other"
    )
    instance = plugin_instance_values(
        parents, instance_id=INSTANCE_ID, registry_id=foreign_registry
    )

    async with pg_schema.engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(INSERT_PLUGIN_SCOPE_REGISTRY, registry)
        await conn.execute(
            # The other side of the *other* instance is never inserted, and this
            # instance points at a registry row that names "pi_other".
            INSERT_PLUGIN_INSTANCE,
            {**instance, "plugin_instance_id": INSTANCE_ID, "scope_id": INSTANCE_ID},
        )
        with pytest.raises(IntegrityError) as failure:
            await trans.commit()

    assert sqlstate_of(failure.value) == "23503"
    assert await pg_schema.count("m05_plugin_instances") == 0
    assert await pg_schema.count("scope_registry") == 0


# --------------------------------------------------------------------------- #
# deferral is a real, introspectable property
# --------------------------------------------------------------------------- #
async def test_set_constraints_immediate_makes_the_same_violation_fail_earlier(pg_schema):
    parents = await _parents(pg_schema)
    instance = plugin_instance_values(parents, instance_id=INSTANCE_ID, registry_id=REGISTRY_ID)

    async with pg_schema.engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with pytest.raises(IntegrityError) as failure:
            await conn.execute(INSERT_PLUGIN_INSTANCE, instance)
        await trans.rollback()

    assert sqlstate_of(failure.value) == "23503"
    assert await pg_schema.count("m05_plugin_instances") == 0


async def test_the_reciprocal_constraints_are_deferrable_and_initially_deferred(pg_schema):
    """Schema-level proof that the deferral survived into this database."""
    rows = await pg_schema.rows(
        """
        SELECT c.conname, c.condeferrable, c.condeferred, c.convalidated
        FROM pg_constraint c
        WHERE c.conname = ANY(:names)
        ORDER BY c.conname
        """,
        {"names": list(RECIPROCAL_FK_NAMES)},
    )
    found = {
        name: {"condeferrable": deferrable, "condeferred": deferred, "convalidated": validated}
        for name, deferrable, deferred, validated in rows
    }
    assert set(found) == set(RECIPROCAL_FK_NAMES), f"missing reciprocal constraints: {found}"
    for name, flags in found.items():
        assert flags["condeferrable"] is True, f"{name} must be DEFERRABLE"
        assert flags["condeferred"] is True, f"{name} must be INITIALLY DEFERRED"
        # ``create_all`` builds them valid; revision 2026110308 is what makes a
        # migrated database agree.  Either way the constraint must be enforced.
        assert flags["convalidated"] is True, f"{name} must be validated by now"
