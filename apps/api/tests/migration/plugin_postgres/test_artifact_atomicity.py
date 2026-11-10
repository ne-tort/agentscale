"""Real PostgreSQL races and failure atomicity for plugin artifacts."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.infrastructure.persistence.repositories.plugins._schema import PluginRepositoryUnavailable
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import ConcurrentPublishError

from ._fixtures import (
    DIGEST_A,
    DIGEST_B,
    HookedSessionFactory,
    PLUGIN_ID,
    assert_full_artifact_row,
    assert_full_domain_record,
    make_draft,
    make_record,
    rollback_command,
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


async def publish_pair(first_store, second_store, first, second) -> list[Any]:
    results = await asyncio.wait_for(
        asyncio.gather(
            first_store.publish_async(
                first, evidence=first.verification,
                actor="same-actor", detail="same-first-insert",
            ),
            second_store.publish_async(
                second, evidence=second.verification,
                actor="same-actor", detail="same-first-insert",
            ),
            return_exceptions=True,
        ),
        timeout=30,
    )
    return list(results)


async def assert_single_publication(engine: AsyncEngine, store, expected) -> None:
    state = await stored_state(engine)
    assert len(state["artifacts"]) == 1
    assert len(state["lifecycle"]) == 1
    assert_full_artifact_row(state["artifacts"][0], expected, current=True, generation=1)
    lifecycle = state["lifecycle"][0]
    assert lifecycle["artifact_id"] == state["artifacts"][0]["id"]
    assert lifecycle["operation"] == "publish"
    assert lifecycle["actor"] == "same-actor"
    assert lifecycle["detail"] == "same-first-insert"
    assert lifecycle["operation_token"] == "publish:" + state["artifacts"][0]["id"]
    assert lifecycle["command_fingerprint"] is None
    stored = await store.by_digest_async(expected.digest)
    assert stored is not None
    assert_full_domain_record(stored, expected, generation=1)


async def test_concurrent_first_insert_exact_replay(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        first_factory = HookedSessionFactory(engine)
        second_factory = HookedSessionFactory(engine)
        barrier = asyncio.Barrier(2)
        first_factory.first_insert_barrier = barrier
        second_factory.first_insert_barrier = barrier
        first_store = store_for(first_factory)
        second_store = store_for(second_factory)
        record = make_record()

        results = await publish_pair(first_store, second_store, record, record)

        assert all(isinstance(result, PluginArtifactRecord) for result in results), results
        await assert_single_publication(engine, first_store, record)


async def test_concurrent_first_insert_semantic_mismatch_is_typed(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        first_factory = HookedSessionFactory(engine)
        second_factory = HookedSessionFactory(engine)
        barrier = asyncio.Barrier(2)
        first_factory.first_insert_barrier = barrier
        second_factory.first_insert_barrier = barrier
        first_store = store_for(first_factory)
        second_store = store_for(second_factory)
        original = make_record()
        changed = make_record(manifest_override={
            "plugin_id": PLUGIN_ID,
            "version": original.version,
            "entrypoint": "plugin.changed:OtherApp",
            "semantically_different": True,
        })
        assert changed.digest == original.digest
        assert changed.manifest_fingerprint != original.manifest_fingerprint

        results = await publish_pair(first_store, second_store, original, changed)

        successes = [result for result in results if isinstance(result, PluginArtifactRecord)]
        conflicts = [result for result in results if isinstance(result, Exception)]
        assert len(successes) == 1, results
        assert len(conflicts) == 1, results
        assert isinstance(conflicts[0], ConcurrentPublishError), conflicts[0]
        winner = successes[0]
        expected = original if winner.manifest == original.manifest else changed
        await assert_single_publication(engine, first_store, expected)


async def test_publish_lifecycle_failure_rolls_back_artifact_and_current(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        record = make_record()
        factory.fail_lifecycle(actor="fail-publish", operation="publish")

        with pytest.raises(PluginRepositoryUnavailable, match="atomic publication failed"):
            await store.publish_async(
                record, evidence=record.verification,
                actor="fail-publish", detail="injected publish failure",
            )

        assert await stored_state(engine) == {"artifacts": [], "lifecycle": []}


async def test_promote_lifecycle_failure_restores_complete_draft(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        published = make_record()
        draft = make_draft(published)
        await store.add_async(draft)
        before = await stored_state(engine)
        assert len(before["artifacts"]) == 1 and before["lifecycle"] == []
        factory.fail_lifecycle(actor="fail-promote", operation="promote")

        with pytest.raises(PluginRepositoryUnavailable, match="atomic publication failed"):
            await store.promote_async(
                published, evidence=published.verification,
                actor="fail-promote", detail="injected promote failure",
            )

        assert await stored_state(engine) == before


async def test_rollback_lifecycle_failure_restores_full_stored_state(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = HookedSessionFactory(engine)
        store = store_for(factory)
        record_a = make_record("1.0.0", DIGEST_A)
        record_b = make_record("1.1.0", DIGEST_B)
        await store.publish_async(record_a, evidence=record_a.verification, actor="ci-a")
        await store.publish_async(record_b, evidence=record_b.verification, actor="ci-b")
        target = await store.by_digest_async(record_a.digest)
        current = await store.current_async(PLUGIN_ID)
        assert target is not None and current is not None
        command = rollback_command(target=target, current=current, reason="failure injection")
        before = await stored_state(engine)
        factory.fail_lifecycle(actor="admin", operation="rollback")

        with pytest.raises(PluginRepositoryUnavailable, match="atomic rollback failed"):
            await store.rollback_async(
                target=target, command=command,
                actor="admin", detail="injected rollback failure",
            )

        assert await stored_state(engine) == before
