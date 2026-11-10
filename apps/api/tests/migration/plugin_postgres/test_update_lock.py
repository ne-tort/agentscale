"""Distributed PostgreSQL transaction lock acceptance on retained pools."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import PluginUpdateLockLease
from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
    PluginUpdateLockError,
    SqlPluginUpdateLock,
)

from ._fixtures import DIGEST_A, DIGEST_B
from .conftest import HEAD_REVISION, alembic_run, engine_for

pytestmark = [pytest.mark.integration, pytest.mark.plugin_postgres]

LOCK_SCOPE = {
    "company_id": "co_a",
    "plugin_id": "mod_equipment",
    "plugin_instance_id": "instance-a",
}


async def prepare_head(db) -> None:
    await alembic_run(db.dsn, "upgrade", "head")
    assert await db.stamped_revision() == HEAD_REVISION


def acquire_kwargs(*, digest=DIGEST_A, **overrides):
    values = {
        **LOCK_SCOPE,
        "expected_manifest_version": "1.2.0",
        "expected_artifact_digest": digest,
    }
    values.update(overrides)
    return values


async def assert_advisory_locks(db, *, expected: int) -> None:
    count = int(await db.scalar(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    ) or 0)
    assert count == expected


async def test_two_independent_process_factories_contend_for_one_lock(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as first_engine:
        async with engine_for(db.dsn) as second_engine:
            first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
            second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
            first_lock = SqlPluginUpdateLock(
                session_factory=first_factory, owner_factory=lambda: "owner-process-a"
            )
            second_lock = SqlPluginUpdateLock(
                session_factory=second_factory, owner_factory=lambda: "owner-process-b"
            )

            first_lease = await first_lock.acquire(**acquire_kwargs(digest=DIGEST_A))
            assert isinstance(first_lease, PluginUpdateLockLease)
            assert first_lease.owner_token == "owner-process-a"
            await assert_advisory_locks(db, expected=1)

            second_lease = await second_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
            assert second_lease is None
            await assert_advisory_locks(db, expected=1)

            public_copy = PluginUpdateLockLease(
                company_id=first_lease.company_id,
                plugin_id=first_lease.plugin_id,
                plugin_instance_id=first_lease.plugin_instance_id,
                expected_manifest_version=first_lease.expected_manifest_version,
                expected_artifact_digest=first_lease.expected_artifact_digest,
                owner_token=first_lease.owner_token,
            )
            with pytest.raises(PluginUpdateLockError, match="owner/binding"):
                await first_lock.release(public_copy)
            with pytest.raises(PluginUpdateLockError, match="owner/binding"):
                await second_lock.release(first_lease)
            await assert_advisory_locks(db, expected=1)

            await first_lock.release(first_lease)
            await assert_advisory_locks(db, expected=0)
            second_lease = await second_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
            assert second_lease is not None
            assert second_lease.owner_token == "owner-process-b"
            await second_lock.release(second_lease)
            await assert_advisory_locks(db, expected=0)


async def test_direct_close_rolls_back_xact_and_pool_reuse_has_no_leaked_lock(
    pg_disposable_db,
) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        checkout_ids: list[int] = []

        @event.listens_for(engine.sync_engine, "checkout")
        def record_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
            checkout_ids.append(id(dbapi_connection))

        first_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "close-owner",
        )
        second_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "after-close-owner",
        )
        lease = await first_lock.acquire(**acquire_kwargs())
        assert lease is not None
        await assert_advisory_locks(db, expected=1)

        # Deliberate direct close: pool check-in must rollback the open xact.
        # Normal saga cancellation below uses only the public release method.
        await lease._session.close()
        await assert_advisory_locks(db, expected=0)
        retry = await second_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        await second_lock.release(retry)
        await assert_advisory_locks(db, expected=0)
        assert len(checkout_ids) >= 2
        assert len(set(checkout_ids)) == 1  # actual retained production connection


async def test_cancelled_saga_uses_public_release_in_finally(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as first_engine:
        async with engine_for(db.dsn) as second_engine:
            first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
            second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
            first_lock = SqlPluginUpdateLock(
                session_factory=first_factory, owner_factory=lambda: "cancel-owner"
            )
            second_lock = SqlPluginUpdateLock(
                session_factory=second_factory, owner_factory=lambda: "retry-owner"
            )
            acquired = asyncio.Event()

            async def update_saga() -> None:
                lease = await first_lock.acquire(**acquire_kwargs())
                assert lease is not None
                try:
                    acquired.set()
                    await asyncio.Event().wait()
                finally:
                    await first_lock.release(lease)

            task = asyncio.create_task(update_saga())
            await acquired.wait()
            await assert_advisory_locks(db, expected=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await assert_advisory_locks(db, expected=0)

            retry_lease = await second_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
            assert retry_lease is not None
            await second_lock.release(retry_lease)
            await assert_advisory_locks(db, expected=0)


async def test_rollback_failure_invalidates_connection_and_retry_uses_new_physical_connection(
    pg_disposable_db,
) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        close_requested = False
        checkout_ids: list[int] = []
        invalidate_ids: list[int] = []

        @event.listens_for(engine.sync_engine, "checkout")
        def record_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
            checkout_ids.append(id(dbapi_connection))

        @event.listens_for(engine.sync_engine, "invalidate")
        def record_invalidate(dbapi_connection, connection_record, exception) -> None:
            invalidate_ids.append(id(dbapi_connection))

        class FailingRollbackSession(AsyncSession):
            async def rollback(self) -> None:
                raise RuntimeError("injected rollback failure")

            async def close(self) -> None:
                nonlocal close_requested
                close_requested = True
                await super().close()

        def failing_factory() -> AsyncSession:
            return FailingRollbackSession(engine, expire_on_commit=False)

        lock = SqlPluginUpdateLock(session_factory=failing_factory, owner_factory=lambda: "owner")
        lease = await lock.acquire(**acquire_kwargs())
        assert lease is not None
        await assert_advisory_locks(db, expected=1)
        first_physical = checkout_ids[0]

        with pytest.raises(PluginUpdateLockError, match="release failed"):
            await lock.release(lease)
        assert close_requested
        assert invalidate_ids == [first_physical]
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        assert checkout_ids[-1] != first_physical  # invalidated old conn cannot be reused
        await retry_lock.release(retry)
        await assert_advisory_locks(db, expected=0)


async def test_close_failure_invalidates_connection_and_retry_uses_new_physical_connection(
    pg_disposable_db,
) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        cleanup = {"rollback": 0, "close": 0}
        checkout_ids: list[int] = []
        # Harness instrumentation: strong refs stop CPython from recycling a
        # freed dbapi_connection id into a false physical-connection match.
        checkout_refs: list[object] = []
        invalidate_ids: list[int] = []

        @event.listens_for(engine.sync_engine, "checkout")
        def record_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
            checkout_refs.append(dbapi_connection)
            checkout_ids.append(id(dbapi_connection))

        @event.listens_for(engine.sync_engine, "invalidate")
        def record_invalidate(dbapi_connection, connection_record, exception) -> None:
            invalidate_ids.append(id(dbapi_connection))

        class FailingCloseSession(AsyncSession):
            async def rollback(self) -> None:
                cleanup["rollback"] += 1
                await super().rollback()

            async def close(self) -> None:
                cleanup["close"] += 1
                raise RuntimeError("injected close failure")

        lock = SqlPluginUpdateLock(
            session_factory=lambda: FailingCloseSession(engine, expire_on_commit=False),
            owner_factory=lambda: "owner",
        )
        lease = await lock.acquire(**acquire_kwargs())
        assert lease is not None
        first_physical = checkout_ids[0]

        with pytest.raises(PluginUpdateLockError, match="release failed"):
            await lock.release(lease)
        assert cleanup == {"rollback": 1, "close": 1}
        assert invalidate_ids == [first_physical]
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        assert checkout_ids[-1] != first_physical
        await retry_lock.release(retry)
        await assert_advisory_locks(db, expected=0)


async def test_invalidate_failure_uses_real_detach_and_retry_uses_new_physical_connection(
    pg_disposable_db,
) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        cleanup = {"rollback": 0, "close": 0, "invalidate": 0, "detach": 0}
        checkout_ids: list[int] = []
        # Harness instrumentation: strong refs stop CPython from recycling a
        # freed dbapi_connection id into a false physical-connection match.
        checkout_refs: list[object] = []

        @event.listens_for(engine.sync_engine, "checkout")
        def record_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
            checkout_refs.append(dbapi_connection)
            checkout_ids.append(id(dbapi_connection))

        class InvalidateFailingConnection:
            """Test-only proxy; the detach fallback discards the real physical connection."""

            def __init__(self, delegate) -> None:
                self.delegate = delegate

            async def invalidate(self) -> None:
                cleanup["invalidate"] += 1
                raise RuntimeError("injected invalidate failure before pool mutation")

            async def detach(self) -> None:
                cleanup["detach"] += 1
                # Harness fix: AsyncConnection has no detach(), and a bare sync
                # Connection.detach() leaves the physical connection open with
                # its xact locks held (verified against real PostgreSQL).  The
                # double performs the real physical discard through the sync
                # public API: sync Connection.invalidate() really closes the
                # physical connection so PostgreSQL releases every advisory lock.
                self.delegate.sync_connection.invalidate()

        class FailingRollbackAndInvalidateSession(AsyncSession):
            async def connection(self):
                return InvalidateFailingConnection(await super().connection())

            async def rollback(self) -> None:
                cleanup["rollback"] += 1
                raise RuntimeError("injected rollback failure")

            async def close(self) -> None:
                cleanup["close"] += 1
                await super().close()

        lock = SqlPluginUpdateLock(
            session_factory=lambda: FailingRollbackAndInvalidateSession(
                engine, expire_on_commit=False
            ),
            owner_factory=lambda: "owner",
        )
        lease = await lock.acquire(**acquire_kwargs())
        assert lease is not None
        first_physical = checkout_ids[0]

        with pytest.raises(PluginUpdateLockError, match="release failed"):
            await lock.release(lease)
        assert cleanup == {"rollback": 1, "close": 1, "invalidate": 1, "detach": 1}
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        assert checkout_ids[-1] != first_physical  # real detach cannot return old conn
        await retry_lock.release(retry)
        await assert_advisory_locks(db, expected=0)


async def test_acquire_sql_failure_closes_and_second_factory_acquires(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        closed = False

        class FailingExecuteSession(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                raise RuntimeError("injected acquire SQL failure")

            async def close(self) -> None:
                nonlocal closed
                closed = True
                await super().close()

        lock = SqlPluginUpdateLock(
            session_factory=lambda: FailingExecuteSession(engine, expire_on_commit=False),
            owner_factory=lambda: "owner",
        )
        with pytest.raises(PluginUpdateLockError, match="acquisition"):
            await lock.acquire(**acquire_kwargs())
        assert closed
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        await retry_lock.release(retry)


async def test_pre_sql_cancelled_acquire_observes_cleanup_before_second_factory_acquires(
    pg_disposable_db,
) -> None:
    """Pre-SQL only: the driver never receives the statement."""

    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        started = asyncio.Event()
        cleanup = {"rollback": 0, "close": 0}
        invalidated: list[object] = []

        @event.listens_for(engine.sync_engine, "invalidate")
        def record_invalidate(dbapi_connection, connection_record, exception) -> None:
            invalidated.append(dbapi_connection)

        class BlockingExecuteSession(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                # Deliberately no super().execute: this is only the pre-SQL scope.
                started.set()
                await asyncio.Event().wait()

            async def rollback(self) -> None:
                cleanup["rollback"] += 1
                await super().rollback()

            async def close(self) -> None:
                cleanup["close"] += 1
                await super().close()

        lock = SqlPluginUpdateLock(
            session_factory=lambda: BlockingExecuteSession(engine, expire_on_commit=False),
            owner_factory=lambda: "owner",
        )
        task = asyncio.create_task(lock.acquire(**acquire_kwargs()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup == {"rollback": 1, "close": 1}
        assert invalidated == []  # successful rollback needs no discard
        await assert_advisory_locks(db, expected=0)

        # The second factory is allowed to acquire only after observed cleanup.
        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        await retry_lock.release(retry)


async def test_post_sql_before_adapter_response_cancelled_acquire_cleans_real_xact(
    pg_disposable_db,
) -> None:
    """SQL completed in the driver, but the adapter has not received its result."""

    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        sql_returned = asyncio.Event()
        cleanup = {"rollback": 0, "close": 0}
        invalidated: list[object] = []

        @event.listens_for(engine.sync_engine, "invalidate")
        def record_invalidate(dbapi_connection, connection_record, exception) -> None:
            invalidated.append(dbapi_connection)

        class AfterSqlBlockingSession(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                result = await super().execute(statement, params, **kwargs)
                sql_returned.set()
                await asyncio.Event().wait()  # adapter result remains uncertain
                return result

            async def rollback(self) -> None:
                cleanup["rollback"] += 1
                await super().rollback()

            async def close(self) -> None:
                cleanup["close"] += 1
                await super().close()

        lock = SqlPluginUpdateLock(
            session_factory=lambda: AfterSqlBlockingSession(engine, expire_on_commit=False),
            owner_factory=lambda: "owner",
        )
        task = asyncio.create_task(lock.acquire(**acquire_kwargs()))
        await sql_returned.wait()
        await assert_advisory_locks(db, expected=1)  # SQL really acquired the xact lock
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup == {"rollback": 1, "close": 1}
        assert invalidated == []
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        await retry_lock.release(retry)
        await assert_advisory_locks(db, expected=0)


async def test_cancelled_release_waits_for_rollback_and_does_not_leak_pool_lock(
    pg_disposable_db,
) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        rollback_started = asyncio.Event()
        allow_rollback = asyncio.Event()

        class BlockingRollbackSession(AsyncSession):
            async def rollback(self) -> None:
                rollback_started.set()
                await allow_rollback.wait()
                await super().rollback()

        lock = SqlPluginUpdateLock(
            session_factory=lambda: BlockingRollbackSession(engine, expire_on_commit=False),
            owner_factory=lambda: "owner",
        )
        lease = await lock.acquire(**acquire_kwargs())
        assert lease is not None
        release_task = asyncio.create_task(lock.release(lease))
        await rollback_started.wait()
        release_task.cancel()
        await asyncio.sleep(0)
        assert not release_task.done()  # cleanup is still deliberately blocked
        allow_rollback.set()
        with pytest.raises(asyncio.CancelledError):
            await release_task
        await assert_advisory_locks(db, expected=0)

        retry_lock = SqlPluginUpdateLock(
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            owner_factory=lambda: "retry-owner",
        )
        retry = await retry_lock.acquire(**acquire_kwargs(digest=DIGEST_B))
        assert retry is not None
        await retry_lock.release(retry)


async def test_no_cross_tenant_or_installation_collision(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as first_engine:
        async with engine_for(db.dsn) as second_engine:
            first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
            second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
            first_lock = SqlPluginUpdateLock(
                session_factory=first_factory, owner_factory=lambda: "owner-a"
            )
            second_lock = SqlPluginUpdateLock(
                session_factory=second_factory, owner_factory=lambda: "owner-b"
            )
            first_lease = await first_lock.acquire(**acquire_kwargs())
            assert first_lease is not None

            other_tenant = await second_lock.acquire(**acquire_kwargs(company_id="co_b"))
            assert other_tenant is not None
            other_instance = await second_lock.acquire(**acquire_kwargs(plugin_instance_id="instance-b"))
            assert other_instance is not None
            other_plugin = await second_lock.acquire(**acquire_kwargs(plugin_id="mod_other"))
            assert other_plugin is not None
            await assert_advisory_locks(db, expected=4)

            for lease in (other_plugin, other_instance, other_tenant, first_lease):
                owner = second_lock if lease is not first_lease else first_lock
                await owner.release(lease)
            await assert_advisory_locks(db, expected=0)
