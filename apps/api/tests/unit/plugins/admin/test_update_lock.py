"""Offline contract tests for transaction-scoped plugin update lock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from prodavan.application.plugins.admin.contracts import PluginUpdateLockLease
from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
    PluginUpdateLockError,
    SqlPluginUpdateLock,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@dataclass
class _Result:
    value: bool

    def scalar(self) -> bool:
        return self.value


class _Backend:
    def __init__(self) -> None:
        self.held: dict[str, object] = {}

    def release(self, session: object) -> None:
        for key, owner in list(self.held.items()):
            if owner is session:
                del self.held[key]


class _Connection:
    def __init__(self, backend: _Backend, session: object) -> None:
        self.backend = backend
        self.session = session
        self.invalidated = False
        self.invalidate_calls = 0
        self.fail_invalidate = False
        self.detached = False
        self.detach_calls = 0

    async def invalidate(self) -> None:
        self.invalidate_calls += 1
        if self.fail_invalidate:
            raise RuntimeError("injected invalidate failure")
        self.invalidated = True
        self.backend.release(self.session)

    async def detach(self) -> None:
        self.detach_calls += 1
        self.detached = True
        self.backend.release(self.session)


class _Session:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend
        self.fail_execute = False
        self.fail_rollback = False
        self.fail_close = False
        self.block_execute = False
        self.entered = asyncio.Event()
        self.closed = False
        self.close_calls = 0
        self.rollback_calls = 0
        self.sql_sent = False
        self.statements: list[tuple[str, dict]] = []
        self._connection = _Connection(backend, self)

    async def begin(self) -> None:
        return None

    async def connection(self) -> _Connection:
        return self._connection

    async def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, dict(params)))
        self.entered.set()
        if self.block_execute:
            await asyncio.Event().wait()
        if self.fail_execute:
            raise RuntimeError("injected SQL failure")
        self.sql_sent = True
        key = params["lock_key"]
        if "pg_try_advisory_xact_lock" not in sql:
            raise AssertionError(sql)
        if key in self.backend.held:
            return _Result(False)
        self.backend.held[key] = self
        return _Result(True)

    async def rollback(self) -> None:
        self.rollback_calls += 1
        if self.fail_rollback:
            raise RuntimeError("injected rollback failure")
        self.backend.release(self)

    async def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("injected close failure")
        # SQLAlchemy pool check-in rolls back an open xact before reuse.
        self.backend.release(self)
        self.closed = True


class _Factory:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session(self.backend)
        self.sessions.append(session)
        return session


def _kwargs(digest: str = DIGEST_A) -> dict:
    return {
        "company_id": "co_a",
        "plugin_id": "mod_equipment",
        "plugin_instance_id": "instance-a",
        "expected_manifest_version": "1.2.0",
        "expected_artifact_digest": digest,
    }


async def test_sql_lock_is_shared_and_release_requires_exact_owned_lease() -> None:
    backend = _Backend()
    factory = _Factory(backend)
    first = SqlPluginUpdateLock(session_factory=factory, owner_factory=lambda: "owner-a")
    second = SqlPluginUpdateLock(session_factory=factory, owner_factory=lambda: "owner-b")

    lease = await first.acquire(**_kwargs())
    assert isinstance(lease, PluginUpdateLockLease)
    assert lease.owner_token == "owner-a"
    assert await second.acquire(**_kwargs(DIGEST_B)) is None
    assert len(backend.held) == 1
    assert factory.sessions[1].closed

    public_copy = PluginUpdateLockLease(
        company_id=lease.company_id,
        plugin_id=lease.plugin_id,
        plugin_instance_id=lease.plugin_instance_id,
        expected_manifest_version=lease.expected_manifest_version,
        expected_artifact_digest=lease.expected_artifact_digest,
        owner_token=lease.owner_token,
    )
    with pytest.raises(PluginUpdateLockError, match="owner/binding"):
        await first.release(public_copy)
    with pytest.raises(PluginUpdateLockError, match="owner/binding"):
        await second.release(lease)
    assert len(backend.held) == 1

    await first.release(lease)
    assert factory.sessions[0].closed
    assert factory.sessions[0].rollback_calls == 1
    assert backend.held == {}
    acquired_by_second = await second.acquire(**_kwargs(DIGEST_B))
    assert acquired_by_second is not None
    assert acquired_by_second.owner_token == "owner-b"
    await second.release(acquired_by_second)


async def test_sql_lock_closes_on_acquire_error_and_invalidates_on_rollback_error() -> None:
    backend = _Backend()
    session = _Session(backend)
    session.fail_execute = True

    class OneSessionFactory:
        def __call__(self):
            return session

    lock = SqlPluginUpdateLock(session_factory=OneSessionFactory(), owner_factory=lambda: "owner")
    with pytest.raises(PluginUpdateLockError, match="acquisition"):
        await lock.acquire(**_kwargs())
    assert session.closed
    assert backend.held == {}

    session.fail_execute = False
    lease = await lock.acquire(**_kwargs())
    assert lease is not None
    session.fail_rollback = True
    with pytest.raises(PluginUpdateLockError, match="release failed"):
        await lock.release(lease)
    assert session._connection.invalidated
    assert session.closed
    assert backend.held == {}


async def test_close_failure_invalidates_connection_instead_of_pooling() -> None:
    backend = _Backend()
    session = _Session(backend)

    class OneSessionFactory:
        def __call__(self):
            return session

    lock = SqlPluginUpdateLock(session_factory=OneSessionFactory(), owner_factory=lambda: "owner")
    lease = await lock.acquire(**_kwargs())
    assert lease is not None
    session.fail_close = True
    with pytest.raises(PluginUpdateLockError, match="release failed"):
        await lock.release(lease)
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert session._connection.invalidate_calls == 1
    assert session._connection.invalidated
    assert session._connection.detach_calls == 0
    assert not session.closed  # the injected close itself failed
    assert backend.held == {}


async def test_invalidate_failure_detaches_instead_of_pooling_locked_connection() -> None:
    backend = _Backend()
    session = _Session(backend)

    class OneSessionFactory:
        def __call__(self):
            return session

    lock = SqlPluginUpdateLock(session_factory=OneSessionFactory(), owner_factory=lambda: "owner")
    lease = await lock.acquire(**_kwargs())
    assert lease is not None
    session.fail_rollback = True
    session._connection.fail_invalidate = True
    with pytest.raises(PluginUpdateLockError, match="release failed"):
        await lock.release(lease)
    assert session._connection.invalidate_calls == 1
    assert session._connection.detach_calls == 1
    assert session._connection.detached
    assert session.close_calls == 1
    assert session.closed
    assert backend.held == {}


async def test_pre_sql_cancelled_acquire_finishes_cleanup_before_cancellation_escapes() -> None:
    backend = _Backend()
    session = _Session(backend)
    session.block_execute = True

    class OneSessionFactory:
        # Harness fix: the generic _Factory creates a NEW session per acquire,
        # so the prepared block_execute/entered session was never used by the
        # lock and entered.wait() deadlocked.  Acquire must use this exact
        # prepared session (same OneSessionFactory pattern as the other tests).
        def __call__(self):
            return session

    lock = SqlPluginUpdateLock(session_factory=OneSessionFactory(), owner_factory=lambda: "owner")
    task = asyncio.create_task(lock.acquire(**_kwargs()))
    await session.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Explicit pre-SQL scope: the double never reached sql_sent. This test does
    # not claim coverage for an after-SQL/lost-response ambiguity.
    assert not session.sql_sent
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert session._connection.invalidate_calls == 0
    assert session._connection.detach_calls == 0
    assert session.closed
    assert backend.held == {}

    # Only after those cleanup observations may another factory acquire.
    other_factory = _Factory(backend)
    other = SqlPluginUpdateLock(session_factory=other_factory, owner_factory=lambda: "other")
    lease = await other.acquire(**_kwargs(DIGEST_B))
    assert lease is not None
    await other.release(lease)


async def test_sql_lock_uses_xact_lock_and_rollback_not_session_unlock() -> None:
    factory = _Factory(_Backend())
    lock = SqlPluginUpdateLock(session_factory=factory, owner_factory=lambda: "owner")
    lease = await lock.acquire(**_kwargs())
    assert lease is not None
    session = factory.sessions[0]
    acquire_sql, acquire_params = session.statements[0]
    assert "pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))" in acquire_sql
    assert "pg_try_advisory_lock(" not in acquire_sql
    assert acquire_params["lock_key"].startswith("prodavan.plugin.update:")
    await lock.release(lease)
    assert session.rollback_calls == 1
    assert all("pg_advisory_unlock" not in sql for sql, _ in session.statements)
