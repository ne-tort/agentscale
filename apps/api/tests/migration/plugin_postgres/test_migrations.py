"""Upgrade acceptance for fresh and historical plugin PostgreSQL databases.

Every test owns a random disposable database.  Only committed Alembic revisions
are executed (upgrade/check); the dormant pending 0802 inventory is never
registered, stamped, copied or applied.  Raw SQL below is DML only and always
binds values; no manual ALTER is used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from prodavan.infrastructure.persistence.repositories.plugins.artifacts import ConcurrentPublishError

from ..postgres_integration._support import sha256_of_text, sqlstate_of, violated_constraint
from ._fixtures import (
    DIGEST_A,
    DIGEST_B,
    HookedSessionFactory,
    PLUGIN_ID,
    insert_legacy_rollback,
    make_record,
    rollback_command,
    seed_source,
    stored_state,
    store_for,
)
from .conftest import (
    HEAD_REVISION,
    STAGE_PLATFORM,
    STAGE_ROLLBACK,
    alembic_run,
    assert_no_pending_0802_in_chain,
    head_revision,
)

pytestmark = [pytest.mark.integration, pytest.mark.plugin_postgres]

PLUGIN_TABLES = {
    "plugin_artifacts",
    "plugin_instances",
    "plugin_capability_grants",
    "plugin_audit_events",
    "plugin_event_deliveries",
    "plugin_feature_flags",
    "plugin_module_sources",
    "plugin_publication_approvals",
    "plugin_source_reservations",
    "plugin_routes",
    "plugin_artifact_lifecycle",
    "plugin_installation_scopes",
}

EXPECTED_LIFECYCLE_CONSTRAINTS = {
    "ck_plugin_artifact_lifecycle_operation",
    "ck_plugin_artifact_lifecycle_actor_nonempty",
    "ck_plugin_artifact_lifecycle_operation_token_nonempty",
    "fk_plugin_artifact_lifecycle_artifact",
    "uq_plugin_artifact_lifecycle_artifact_operation_token",
}


async def columns(db, table: str) -> dict[str, dict[str, Any]]:
    rows = await db.rows(
        """
        SELECT column_name, data_type, character_maximum_length,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :table
        ORDER BY ordinal_position
        """,
        {"table": table},
    )
    assert rows, f"table {table!r} was not created"
    return {
        row[0]: {
            "data_type": row[1],
            "character_maximum_length": row[2],
            "is_nullable": row[3],
            "column_default": row[4],
        }
        for row in rows
    }


async def constraint_names(db, table: str) -> set[str]:
    rows = await db.rows(
        """
        SELECT con.conname
        FROM pg_constraint AS con
        JOIN pg_class AS cls ON cls.oid = con.conrelid
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public' AND cls.relname = :table
        """,
        {"table": table},
    )
    return {row[0] for row in rows}


async def foreign_key_delete_rule(db, constraint: str) -> str:
    rows = await db.rows(
        """
        SELECT confdeltype::text
        FROM pg_constraint
        WHERE conname = :name AND conrelid = 'plugin_artifact_lifecycle'::regclass
        """,
        {"name": constraint},
    )
    assert len(rows) == 1, rows
    return rows[0][0]


def assert_upgrade_at_head() -> None:
    assert head_revision() == HEAD_REVISION
    assert_no_pending_0802_in_chain()


async def assert_head_tables(db) -> None:
    tables = await db.tables()
    missing = PLUGIN_TABLES - tables
    assert not missing, f"upgrade head did not create plugin tables: {missing}"
    assert await db.stamped_revision() == HEAD_REVISION


async def assert_lifecycle_shape(db) -> None:
    cols = await columns(db, "plugin_artifact_lifecycle")
    fingerprint = cols["command_fingerprint"]
    assert fingerprint["data_type"] == "character varying"
    assert fingerprint["character_maximum_length"] == 64
    assert fingerprint["is_nullable"] == "YES"
    assert fingerprint["column_default"] is None
    for column in ("id", "artifact_id", "operation", "actor", "detail", "operation_token"):
        assert cols[column]["is_nullable"] == "NO"
    assert cols["occurred_at"]["column_default"] == "now()"
    found = await constraint_names(db, "plugin_artifact_lifecycle")
    assert EXPECTED_LIFECYCLE_CONSTRAINTS <= found
    assert await foreign_key_delete_rule(db, "fk_plugin_artifact_lifecycle_artifact") == "r"


async def artifact_row(db, artifact_id: str) -> dict[str, Any]:
    rows = await db.rows(
        "SELECT * FROM plugin_artifacts WHERE id = :id", {"id": artifact_id}
    )
    assert len(rows) == 1
    return dict(rows[0]._mapping)


async def lifecycle_rows(db) -> list[dict[str, Any]]:
    rows = await db.rows("SELECT * FROM plugin_artifact_lifecycle ORDER BY id")
    return [dict(row._mapping) for row in rows]


async def seed_historical_artifact(db) -> None:
    """Insert a real 0801-shaped row before any 09xx additive column exists."""
    await db.execute(
        """
        INSERT INTO plugin_artifacts (
            id, plugin_id, version, oci_digest, image_ref, manifest,
            app_version_projection, cosign_verified, status, lifecycle_version,
            is_current, published_at
        ) VALUES (
            :id, :plugin_id, :version, :digest, :image_ref,
            CAST(:manifest AS jsonb), CAST(:projection AS jsonb), true, 'published',
            7, true, :published_at
        )
        """,
        {
            "id": "part_historical",
            "plugin_id": PLUGIN_ID,
            "version": "0.8.0",
            "digest": sha256_of_text("historical-0801"),
            "image_ref": "registry.example/plugin@" + sha256_of_text("historical-0801"),
            "manifest": '{"plugin_id":"mod_equipment","version":"0.8.0"}',
            "projection": '{"protocol":"0.8","legacy":true}',
            "published_at": datetime(2026, 11, 1, tzinfo=UTC),
        },
    )


async def expect_integrity_error(db, sql: str, params: dict, constraint: str, sqlstate: str) -> None:
    with pytest.raises(IntegrityError) as raised:
        await db.execute(sql, params)
    error = raised.value
    assert sqlstate_of(error) == sqlstate, str(error)
    assert violated_constraint(error) == constraint, str(error)


async def test_fresh_upgrade_to_0909_is_repeatable_and_drift_free(pg_disposable_db) -> None:
    db = pg_disposable_db
    assert_upgrade_at_head()
    assert await db.stamped_revision() is None

    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head_tables(db)
    tables_after_first_upgrade = await db.tables()

    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head_tables(db)
    assert await db.tables() == tables_after_first_upgrade

    await alembic_run(db.dsn, "check")
    await assert_lifecycle_shape(db)


async def test_lifecycle_defaults_constraints_and_foreign_key(pg_disposable_db) -> None:
    db = pg_disposable_db
    assert_upgrade_at_head()
    await alembic_run(db.dsn, "upgrade", "head")
    await assert_lifecycle_shape(db)
    await seed_source(db)

    engine: AsyncEngine = create_async_engine(db.dsn, poolclass=NullPool)
    try:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        record = make_record()
        await store.publish_async(record, evidence=record.verification, actor="ci")
    finally:
        await engine.dispose()

    artifact_rows = await db.rows(
        "SELECT id FROM plugin_artifacts WHERE plugin_id = :plugin", {"plugin": PLUGIN_ID}
    )
    assert len(artifact_rows) == 1
    artifact_id = artifact_rows[0][0]

    await db.execute(
        """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            'pal_defaults', :artifact_id, 'publish', 'defaults-actor',
            'defaults probe', 'publish:defaults'
        )
        """,
        {"artifact_id": artifact_id},
    )
    stored = await lifecycle_rows(db)
    assert len(stored) == 2  # durable publish fact + defaults probe
    defaults_row = next(row for row in stored if row["id"] == "pal_defaults")
    assert defaults_row["command_fingerprint"] is None
    assert defaults_row["occurred_at"] is not None

    orphan_sql = """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES ('pal_orphan', 'missing_artifact', 'publish', 'probe', 'probe', 'publish:orphan')
    """
    await expect_integrity_error(
        db, orphan_sql, {}, "fk_plugin_artifact_lifecycle_artifact", "23503"
    )

    duplicate_sql = """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            'pal_duplicate', :artifact_id, 'publish', 'probe', 'probe', 'publish:defaults'
        )
    """
    await expect_integrity_error(
        db, duplicate_sql, {"artifact_id": artifact_id},
        "uq_plugin_artifact_lifecycle_artifact_operation_token", "23505",
    )

    bad_operation_sql = """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            'pal_bad_operation', :artifact_id, 'unknown', 'probe', 'probe', 'publish:bad-operation'
        )
    """
    await expect_integrity_error(
        db, bad_operation_sql, {"artifact_id": artifact_id},
        "ck_plugin_artifact_lifecycle_operation", "23514",
    )

    empty_actor_sql = """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            'pal_empty_actor', :artifact_id, 'publish', '   ', 'probe', 'publish:empty-actor'
        )
    """
    await expect_integrity_error(
        db, empty_actor_sql, {"artifact_id": artifact_id},
        "ck_plugin_artifact_lifecycle_actor_nonempty", "23514",
    )

    empty_token_sql = """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            'pal_empty_token', :artifact_id, 'publish', 'probe', 'probe', ' '
        )
    """
    await expect_integrity_error(
        db, empty_token_sql, {"artifact_id": artifact_id},
        "ck_plugin_artifact_lifecycle_operation_token_nonempty", "23514",
    )

    assert len(await lifecycle_rows(db)) == 2


async def test_historical_0801_row_is_preserved_without_0901_backfill(pg_disposable_db) -> None:
    db = pg_disposable_db
    assert_upgrade_at_head()
    await alembic_run(db.dsn, "upgrade", STAGE_PLATFORM)
    assert await db.stamped_revision() == STAGE_PLATFORM
    await seed_historical_artifact(db)
    before = await artifact_row(db, "part_historical")

    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head_tables(db)
    await alembic_run(db.dsn, "upgrade", "head")
    await alembic_run(db.dsn, "check")

    after = await artifact_row(db, "part_historical")
    for key, value in before.items():
        assert after[key] == value, f"historical column {key!r} changed"
    assert after["source_id"] is None
    assert after["commit_sha"] is None
    assert after["backend_digest"] is None
    assert after["backend_image_ref"] is None
    assert after["signer_identity"] is None
    assert after["issuer"] is None
    assert after["run_url"] is None
    assert after["verification_evidence"] == {}
    assert after["status"] == "published"
    assert after["is_current"] is True
    assert await lifecycle_rows(db) == []


async def test_0907_legacy_rollback_is_typed_replay_conflict_at_0909(pg_disposable_db) -> None:
    db = pg_disposable_db
    assert_upgrade_at_head()
    await alembic_run(db.dsn, "upgrade", STAGE_ROLLBACK)
    assert await db.stamped_revision() == STAGE_ROLLBACK

    await seed_source(db)
    engine: AsyncEngine = create_async_engine(db.dsn, poolclass=NullPool)
    try:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        record_a = make_record("1.0.0", DIGEST_A)
        record_b = make_record("1.1.0", DIGEST_B)
        await store.add_async(record_a)
        await store.set_current_async(PLUGIN_ID, record_a.version)
        await store.add_async(record_b)
        await store.set_current_async(
            PLUGIN_ID, record_b.version, expected_version=record_a.version
        )
        await store.set_current_async(
            PLUGIN_ID, record_a.version, expected_version=record_b.version
        )
    finally:
        await engine.dispose()

    artifacts = await db.rows(
        "SELECT id, version, lifecycle_version FROM plugin_artifacts ORDER BY version"
    )
    by_version = {row[1]: (row[0], row[2]) for row in artifacts}
    assert set(by_version) == {"1.0.0", "1.1.0"}
    artifact_a_id, generation_a = by_version["1.0.0"]
    _artifact_b_id, generation_b = by_version["1.1.0"]
    assert (generation_a, generation_b) == (2, 1)

    await insert_legacy_rollback(
        db,
        row_id="pal_legacy_no_fp",
        artifact_id=artifact_a_id,
        source_version=record_b.version,
        source_generation=generation_b,
        actor="admin",
        detail="historical rollback without fingerprint",
    )
    artifacts_before_upgrade = [
        dict(row._mapping) for row in await db.rows("SELECT * FROM plugin_artifacts ORDER BY id")
    ]
    lifecycle_before_upgrade = await lifecycle_rows(db)

    await alembic_run(db.dsn, "upgrade", "head")
    await assert_head_tables(db)
    await alembic_run(db.dsn, "upgrade", "head")
    await alembic_run(db.dsn, "check")

    assert [
        dict(row._mapping) for row in await db.rows("SELECT * FROM plugin_artifacts ORDER BY id")
    ] == artifacts_before_upgrade
    legacy = await lifecycle_rows(db)
    assert len(legacy) == 1
    assert legacy[0]["id"] == "pal_legacy_no_fp"
    assert legacy[0]["command_fingerprint"] is None
    for key in ("id", "artifact_id", "operation", "actor", "detail", "occurred_at", "operation_token"):
        assert legacy[0][key] == lifecycle_before_upgrade[0][key]

    engine = create_async_engine(db.dsn, poolclass=NullPool)
    try:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        target = await store.by_digest_async(record_a.digest)
        assert target is not None and target.lifecycle_version == 2
        current_before = await store.current_async(PLUGIN_ID)
        assert current_before is not None and current_before.lifecycle_version == 2
        assert current_before.version == record_a.version

        from dataclasses import replace

        stale_source_view = replace(record_b, lifecycle_version=1)
        command = rollback_command(
            target=target,
            current=stale_source_view,
            reason="legacy replay",
        )
        before_attempt = await stored_state(engine)
        with pytest.raises(ConcurrentPublishError, match="fingerprint mismatch"):
            await store.rollback_async(
                target=target,
                command=command,
                actor="admin",
                detail="historical rollback without fingerprint",
            )
        assert await stored_state(engine) == before_attempt
        current_after = await store.current_async(PLUGIN_ID)
        assert current_after == current_before
    finally:
        await engine.dispose()
