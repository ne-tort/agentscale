"""Full A→B→A→B→A rollback generation/replay acceptance on PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prodavan.application.dynamic_app_registry.contracts import rollback_command_fingerprint
from prodavan.infrastructure.persistence.repositories.plugins._schema import PluginRepositoryUnavailable

from ._fixtures import (
    DIGEST_A,
    DIGEST_B,
    HookedSessionFactory,
    NOW,
    PLUGIN_ID,
    artifact_by_version,
    assert_full_artifact_row,
    assert_full_domain_record,
    make_record,
    rollback_command,
    rollback_token,
    seed_source,
    stored_state,
    store_for,
)
from .conftest import HEAD_REVISION, alembic_run, engine_for

pytestmark = [pytest.mark.integration, pytest.mark.plugin_postgres]


async def prepare_head(db) -> None:
    await alembic_run(db.dsn, "upgrade", "head")
    assert await db.stamped_revision() == HEAD_REVISION
    await seed_source(db)


def replay_fingerprint(command, source_generation: int) -> str:
    """The exact production fingerprint after source-generation CAS resolution."""
    return rollback_command_fingerprint(
        replace(command, expected_current_lifecycle_version=source_generation)
    )


def assert_exact_lifecycle(
    state: dict[str, list[dict[str, Any]]],
    expected_rows: list[dict[str, Any]],
) -> None:
    actual_rows = state["lifecycle"]
    actual_by_detail = {row["detail"]: row for row in actual_rows}
    assert len(actual_rows) == len(expected_rows)
    assert set(actual_by_detail) == {row["detail"] for row in expected_rows}
    for expected in expected_rows:
        actual = actual_by_detail[expected["detail"]]
        assert isinstance(actual["id"], str) and actual["id"]
        assert actual == {**expected, "id": actual["id"]}


def assert_cycle_state(
    state: dict[str, list[dict[str, Any]]],
    record_a,
    record_b,
    *,
    current_version: str,
    generation_a: int,
    generation_b: int,
    lifecycle_count: int,
) -> None:
    assert len(state["artifacts"]) == 2
    assert len(state["lifecycle"]) == lifecycle_count
    assert_full_artifact_row(
        artifact_by_version(state, record_a.version), record_a,
        current=current_version == record_a.version, generation=generation_a,
    )
    assert_full_artifact_row(
        artifact_by_version(state, record_b.version), record_b,
        current=current_version == record_b.version, generation=generation_b,
    )
    assert sum(row["is_current"] for row in state["artifacts"]) == 1
    rollback_rows = [row for row in state["lifecycle"] if row["operation"] == "rollback"]
    for row in rollback_rows:
        assert isinstance(row["command_fingerprint"], str)
        assert len(row["command_fingerprint"]) == 64
    publish_rows = [row for row in state["lifecycle"] if row["operation"] == "publish"]
    assert all(row["command_fingerprint"] is None for row in publish_rows)


async def rollback_step(store, engine: AsyncEngine, *, target_record, reason: str, detail: str):
    target = await store.by_digest_async(target_record.digest)
    current = await store.current_async(PLUGIN_ID)
    assert target is not None and target.lifecycle_version is not None
    assert current is not None and current.digest != target.digest
    command = rollback_command(target=target, current=current, reason=reason)
    before = await stored_state(engine)
    returned = await store.rollback_async(
        target=target, command=command, actor="admin", detail=detail,
    )
    expected_generation = target.lifecycle_version + 1
    assert_full_domain_record(returned, target_record, generation=expected_generation)
    after = await stored_state(engine)
    assert len(after["lifecycle"]) == len(before["lifecycle"]) + 1
    return command, returned, after


async def test_rollback_cycle_and_same_generation_lost_response_replay(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        record_a = make_record("1.0.0", DIGEST_A)
        record_b = make_record("1.1.0", DIGEST_B)

        await store.publish_async(record_a, evidence=record_a.verification, actor="ci-a")
        state_a = await stored_state(engine)
        assert len(state_a["artifacts"]) == 1
        assert len(state_a["lifecycle"]) == 1
        assert_full_artifact_row(state_a["artifacts"][0], record_a, current=True, generation=1)

        await store.publish_async(record_b, evidence=record_b.verification, actor="ci-b")
        state_b = await stored_state(engine)
        assert_cycle_state(
            state_b, record_a, record_b,
            current_version=record_b.version, generation_a=1, generation_b=1, lifecycle_count=2,
        )

        _first_command, _first_return, state_aba = await rollback_step(
            store, engine, target_record=record_a,
            reason="cycle first B to A", detail="rollback to A first",
        )
        assert_cycle_state(
            state_aba, record_a, record_b,
            current_version=record_a.version, generation_a=2, generation_b=1, lifecycle_count=3,
        )

        _second_command, _second_return, state_abab = await rollback_step(
            store, engine, target_record=record_b,
            reason="cycle A to B", detail="rollback to B",
        )
        assert_cycle_state(
            state_abab, record_a, record_b,
            current_version=record_b.version, generation_a=2, generation_b=2, lifecycle_count=4,
        )

        target_a = await store.by_digest_async(record_a.digest)
        current_b = await store.current_async(PLUGIN_ID)
        assert target_a is not None and target_a.lifecycle_version == 2
        assert current_b is not None and current_b.lifecycle_version == 2
        final_command = rollback_command(
            target=target_a, current=current_b, reason="cycle second B to A",
        )
        factory.lose_next_commit_response()
        with pytest.raises(PluginRepositoryUnavailable, match="atomic rollback failed"):
            await store.rollback_async(
                target=target_a, command=final_command, actor="admin",
                detail="rollback to A on the new B generation",
            )

        # COMMIT was durable; only its response was lost. Assert the complete
        # committed generation and immutable lifecycle fields before replay.
        committed = await stored_state(engine)
        assert_cycle_state(
            committed, record_a, record_b,
            current_version=record_a.version, generation_a=3, generation_b=2, lifecycle_count=5,
        )
        final_rows = [
            row for row in committed["lifecycle"]
            if row["detail"] == "rollback to A on the new B generation"
        ]
        assert len(final_rows) == 1
        assert final_rows[0]["operation"] == "rollback"
        assert final_rows[0]["actor"] == "admin"
        assert final_rows[0]["command_fingerprint"] is not None

        artifact_a = artifact_by_version(committed, record_a.version)
        artifact_b = artifact_by_version(committed, record_b.version)
        assert_exact_lifecycle(committed, [
            {
                "artifact_id": artifact_a["id"], "operation": "publish", "actor": "ci-a",
                "detail": "publish " + record_a.image_ref, "occurred_at": NOW,
                "operation_token": "publish:" + artifact_a["id"], "command_fingerprint": None,
            },
            {
                "artifact_id": artifact_b["id"], "operation": "publish", "actor": "ci-b",
                "detail": "publish " + record_b.image_ref, "occurred_at": NOW,
                "operation_token": "publish:" + artifact_b["id"], "command_fingerprint": None,
            },
            {
                "artifact_id": artifact_a["id"], "operation": "rollback", "actor": "admin",
                "detail": "rollback to A first", "occurred_at": NOW,
                "operation_token": rollback_token(artifact_a["id"], record_b.version, 1),
                "command_fingerprint": replay_fingerprint(_first_command, 1),
            },
            {
                "artifact_id": artifact_b["id"], "operation": "rollback", "actor": "admin",
                "detail": "rollback to B", "occurred_at": NOW,
                "operation_token": rollback_token(artifact_b["id"], record_a.version, 2),
                "command_fingerprint": replay_fingerprint(_second_command, 2),
            },
            {
                "artifact_id": artifact_a["id"], "operation": "rollback", "actor": "admin",
                "detail": "rollback to A on the new B generation", "occurred_at": NOW,
                "operation_token": rollback_token(artifact_a["id"], record_b.version, 2),
                "command_fingerprint": replay_fingerprint(final_command, 2),
            },
        ])

        replayed = await store.rollback_async(
            target=target_a, command=final_command, actor="admin",
            detail="rollback to A on the new B generation",
        )
        assert_full_domain_record(replayed, record_a, generation=3)
        assert await stored_state(engine) == committed

        rollback_a_rows = [
            row for row in committed["lifecycle"]
            if row["operation"] == "rollback" and row["artifact_id"] == artifact_a["id"]
        ]
        assert len(rollback_a_rows) == 2
        assert rollback_a_rows[0]["operation_token"] != rollback_a_rows[1]["operation_token"]
