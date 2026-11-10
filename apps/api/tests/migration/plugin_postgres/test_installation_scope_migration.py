"""2026110909 installation-scope DDL and no-backfill acceptance."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from ..postgres_integration._support import sqlstate_of, violated_constraint
from ._scope_fixtures import CABINET_ID, COMPANY_ID, PROJECT_ID, seed_ready_bootstrap
from .conftest import (
    HEAD_REVISION,
    STAGE_PRE_SCOPE,
    alembic_run,
    assert_no_pending_0802_in_chain,
    engine_for,
    head_revision,
)

pytestmark = [pytest.mark.integration, pytest.mark.plugin_postgres]

SCOPE_COLUMNS = {
    "plugin_instance_id": 80,
    "company_id": 40,
    "cabinet_id": 40,
    "project_id": 40,
}

SCOPE_CHECKS = {
    "ck_plugin_installation_scope_company_nonempty",
    "ck_plugin_installation_scope_cabinet_nonempty",
    "ck_plugin_installation_scope_project_nonempty",
}

SCOPE_FOREIGN_KEYS = {
    "fk_plugin_installation_scope_project": (
        "FOREIGN KEY (project_id, company_id, cabinet_id) "
        "REFERENCES projects(id, company_id, cabinet_id)",
        "r",
    ),
    "fk_plugin_installation_scope_instance": (
        "FOREIGN KEY (company_id, plugin_instance_id) "
        "REFERENCES plugin_instances(company_id, id)",
        "c",
    ),
    "fk_plugin_installation_scope_company": (
        "FOREIGN KEY (company_id) REFERENCES companies(id)",
        "c",
    ),
}


async def columns(db, table: str) -> dict[str, dict[str, Any]]:
    rows = await db.rows(
        """
        SELECT column_name, character_maximum_length, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :table
        ORDER BY ordinal_position
        """,
        {"table": table},
    )
    assert rows, table
    return {
        row[0]: {"length": row[1], "nullable": row[2], "default": row[3]}
        for row in rows
    }


async def constraints(db, table: str) -> dict[str, dict[str, Any]]:
    rows = await db.rows(
        """
        SELECT con.conname, con.contype::text, con.confdeltype::text,
               pg_get_constraintdef(con.oid)
        FROM pg_constraint AS con
        JOIN pg_class AS cls ON cls.oid = con.conrelid
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public' AND cls.relname = :table
        """,
        {"table": table},
    )
    return {
        row[0]: {"type": row[1], "delete": row[2], "definition": row[3]}
        for row in rows
    }


def normalized(value: str) -> str:
    return " ".join(value.split())


async def assert_installation_scope_shape(db) -> None:
    cols = await columns(db, "plugin_installation_scopes")
    assert set(cols) == {*SCOPE_COLUMNS, "created_at", "updated_at"}
    for name, length in SCOPE_COLUMNS.items():
        assert cols[name]["length"] == length
        assert cols[name]["nullable"] == "NO"
        assert cols[name]["default"] is None
    for name in ("created_at", "updated_at"):
        assert cols[name]["nullable"] == "NO"
        assert cols[name]["default"] == "now()"

    found = await constraints(db, "plugin_installation_scopes")
    assert found["plugin_installation_scopes_pkey"]["type"] == "p"
    assert "PRIMARY KEY (plugin_instance_id)" in normalized(
        found["plugin_installation_scopes_pkey"]["definition"]
    )
    assert SCOPE_CHECKS <= set(found)
    for field, constraint_name in (
        ("company_id", "ck_plugin_installation_scope_company_nonempty"),
        ("cabinet_id", "ck_plugin_installation_scope_cabinet_nonempty"),
        ("project_id", "ck_plugin_installation_scope_project_nonempty"),
    ):
        check = found[constraint_name]
        assert check["type"] == "c"
        definition = normalized(check["definition"])
        # Harness fix: pg_get_constraintdef deparses CHECKs with ::text casts
        # and parentheses, e.g. ((company_id)::text <> ''::text).  Compare a
        # canonical form without casts, parentheses or whitespace.
        compact = (
            definition.replace("::text", "")
            .replace("(", "")
            .replace(")", "")
            .replace(" ", "")
        )
        assert f"{field}<>''" in compact
        assert f"{field}=btrim{field}" in compact
    for name, (reference, delete_rule) in SCOPE_FOREIGN_KEYS.items():
        assert found[name]["type"] == "f"
        assert reference in normalized(found[name]["definition"])
        assert found[name]["delete"] == delete_rule

    project_anchor = (await constraints(db, "projects"))["uq_project_tenant_cabinet_anchor"]
    assert project_anchor["type"] == "u"
    assert "UNIQUE (id, company_id, cabinet_id)" in normalized(project_anchor["definition"])
    instance_anchor = (await constraints(db, "plugin_instances"))["uq_plugin_instance_tenant_anchor"]
    assert instance_anchor["type"] == "u"
    assert "UNIQUE (company_id, id)" in normalized(instance_anchor["definition"])

    for table, check_name, fk_name in (
        (
            "plugin_module_sources",
            "ck_plugin_module_source_scope_pair",
            "fk_plugin_module_source_project_scope",
        ),
        (
            "plugin_source_reservations",
            "ck_plugin_source_reservation_scope_pair",
            "fk_plugin_source_reservation_project_scope",
        ),
    ):
        pair_cols = await columns(db, table)
        for name in ("cabinet_id", "project_id"):
            assert pair_cols[name]["length"] == 40
            assert pair_cols[name]["nullable"] == "YES"
            assert pair_cols[name]["default"] is None
        pair_constraints = await constraints(db, table)
        assert pair_constraints[check_name]["type"] == "c"
        definition = normalized(pair_constraints[check_name]["definition"])
        # Same deparse-tolerant canonical comparison as above.
        compact = (
            definition.replace("::text", "")
            .replace("(", "")
            .replace(")", "")
            .replace(" ", "")
        )
        assert "cabinet_idISNULLANDproject_idISNULL" in compact
        assert "cabinet_idISNOTNULLANDproject_idISNOTNULL" in compact
        assert pair_constraints[fk_name]["type"] == "f"
        assert "FOREIGN KEY (project_id, company_id, cabinet_id)" in normalized(
            pair_constraints[fk_name]["definition"]
        )
        assert pair_constraints[fk_name]["delete"] == "r"


async def assert_head(db) -> None:
    assert head_revision() == HEAD_REVISION
    assert_no_pending_0802_in_chain()
    assert await db.stamped_revision() == HEAD_REVISION


async def seed_pre_scope_sources(db) -> dict[str, list[dict[str, Any]]]:
    # Three historical shapes must not be guessed into a parent binding:
    # legacy, random/non-deterministic and ambiguous source identities.
    candidates = (
        ("src_legacy", "co_legacy", 910001, "legacy/plugins"),
        ("src_random", "co_random", 910002, "random/plugins"),
        ("src_ambiguous", "co_ambiguous", 910003, "ambiguous/plugins"),
    )
    for index, (source_id, company_id, repo_id, full_name) in enumerate(candidates):
        await db.execute(
            """
            INSERT INTO plugin_module_sources (
                id, company_id, plugin_id, provider, repo_id, full_name, private,
                default_branch, installation_credential_ref, trusted_workflow_ref,
                status
            ) VALUES (
                :id, :company_id, 'mod_equipment', 'github_app', :repo_id, :full_name,
                true, 'main', :credential_ref, :workflow_ref, 'ready'
            )
            """,
            {
                "id": source_id,
                "company_id": company_id,
                "repo_id": repo_id,
                "full_name": full_name,
                "credential_ref": "secret_ref://ci/" + source_id,
                "workflow_ref": full_name + "/.github/workflows/publish.yml@" + "a" * 40,
            },
        )
        await db.execute(
            """
            INSERT INTO plugin_source_reservations (
                id, company_id, plugin_id, provider, expected_full_name,
                expected_private, expected_default_branch,
                installation_credential_ref, trusted_workflow_ref, attempt_id,
                create_in_progress, status
            ) VALUES (
                :id, :company_id, 'mod_equipment', 'github_app', :full_name,
                true, 'main', :credential_ref, :workflow_ref, :attempt_id,
                true, 'provisioning'
            )
            """,
            {
                "id": "res_" + source_id,
                "company_id": company_id,
                "full_name": full_name,
                "credential_ref": "secret_ref://ci/" + source_id,
                "workflow_ref": full_name + "/.github/workflows/publish.yml@" + "a" * 40,
                "attempt_id": "attempt_" + str(index),
            },
        )
    return {
        table: [
            dict(row._mapping)
            for row in await db.rows("SELECT * FROM " + table + " ORDER BY id")
        ]
        for table in ("plugin_module_sources", "plugin_source_reservations")
    }


async def assert_old_rows_unchanged_and_scope_null(db, before) -> None:
    for table, old_rows in before.items():
        new_rows = [
            dict(row._mapping)
            for row in await db.rows("SELECT * FROM " + table + " ORDER BY id")
        ]
        assert len(new_rows) == len(old_rows) == 3
        for old, new in zip(old_rows, new_rows, strict=True):
            for key, value in old.items():
                assert new[key] == value, (table, key)
            assert new["cabinet_id"] is None
            assert new["project_id"] is None
    assert int(await db.scalar("SELECT count(*) FROM plugin_installation_scopes") or 0) == 0


async def test_fresh_0909_installation_scope_shape_is_repeatable(pg_disposable_db) -> None:
    db = pg_disposable_db
    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head(db)
    await assert_installation_scope_shape(db)
    await alembic_run(db.dsn, "upgrade", "head")
    await alembic_run(db.dsn, "check")
    await assert_head(db)
    await assert_installation_scope_shape(db)


async def test_historical_0908_scope_candidates_have_no_0909_backfill(pg_disposable_db) -> None:
    db = pg_disposable_db
    await alembic_run(db.dsn, "upgrade", STAGE_PRE_SCOPE)
    assert await db.stamped_revision() == STAGE_PRE_SCOPE
    before = await seed_pre_scope_sources(db)

    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head(db)
    await alembic_run(db.dsn, "upgrade", "head")
    await alembic_run(db.dsn, "check")
    await assert_old_rows_unchanged_and_scope_null(db, before)
    await assert_installation_scope_shape(db)


async def expect_check_violation(db, sql: str, params: dict, constraint: str) -> None:
    with pytest.raises(IntegrityError) as raised:
        await db.execute(sql, params)
    assert sqlstate_of(raised.value) == "23514"
    assert violated_constraint(raised.value) == constraint


async def test_0909_source_and_reservation_scope_pairs_fail_closed(pg_disposable_db) -> None:
    db = pg_disposable_db
    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head(db)
    common = {
        "company_id": "co_pair",
        "credential_ref": "secret_ref://ci/scope-pair",
        "workflow_ref": "pair/plugins/.github/workflows/publish.yml@" + "a" * 40,
    }
    await expect_check_violation(
        db,
        """
        INSERT INTO plugin_module_sources (
            id, company_id, plugin_id, cabinet_id, provider, repo_id, full_name,
            private, default_branch, installation_credential_ref,
            trusted_workflow_ref, status
        ) VALUES (
            'src_bad_pair', :company_id, 'mod_equipment', 'cab_without_project',
            'github_app', 910099, 'pair/plugins', true, 'main', :credential_ref,
            :workflow_ref, 'ready'
        )
        """,
        common,
        "ck_plugin_module_source_scope_pair",
    )
    await expect_check_violation(
        db,
        """
        INSERT INTO plugin_source_reservations (
            id, company_id, plugin_id, project_id, provider, expected_full_name,
            expected_private, expected_default_branch, installation_credential_ref,
            trusted_workflow_ref, attempt_id, create_in_progress, status
        ) VALUES (
            'res_bad_pair', :company_id, 'mod_equipment', 'prj_without_cabinet',
            'github_app', 'pair/plugins', true, 'main', :credential_ref,
            :workflow_ref, 'attempt_bad_pair', true, 'provisioning'
        )
        """,
        common,
        "ck_plugin_source_reservation_scope_pair",
    )
    assert int(await db.scalar("SELECT count(*) FROM plugin_module_sources") or 0) == 0
    assert int(await db.scalar("SELECT count(*) FROM plugin_source_reservations") or 0) == 0


async def test_0909_installation_scope_nonempty_checks_reject_blank_values(pg_disposable_db) -> None:
    db = pg_disposable_db
    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head(db)
    async with engine_for(db.dsn) as engine:
        seeded = await seed_ready_bootstrap(engine)
    base = {
        "plugin_instance_id": seeded.plugin_instance_id,
        "company_id": COMPANY_ID,
        "cabinet_id": CABINET_ID,
        "project_id": PROJECT_ID,
    }
    insert_sql = """
        INSERT INTO plugin_installation_scopes (
            plugin_instance_id, company_id, cabinet_id, project_id
        ) VALUES (
            :plugin_instance_id, :company_id, :cabinet_id, :project_id
        )
    """
    assert int(await db.scalar("SELECT count(*) FROM plugin_installation_scopes") or 0) == 0

    for field, constraint_name in (
        ("company_id", "ck_plugin_installation_scope_company_nonempty"),
        ("cabinet_id", "ck_plugin_installation_scope_cabinet_nonempty"),
        ("project_id", "ck_plugin_installation_scope_project_nonempty"),
    ):
        for invalid in ("", "   "):
            params = {**base, field: invalid}
            await expect_check_violation(db, insert_sql, params, constraint_name)
            assert int(
                await db.scalar("SELECT count(*) FROM plugin_installation_scopes") or 0
            ) == 0

    # All six failed INSERTs rolled back: no durable installation binding remains.
    assert await db.rows("SELECT * FROM plugin_installation_scopes") == []
