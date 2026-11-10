"""PostgreSQL transaction-scoped distributed plugin update lock.

A dedicated AsyncSession owns one explicit transaction for the whole saga.
The transaction-scoped advisory lock is released by rollback, never by returning
a still-locked physical connection to the pool. Failed cleanup invalidates that
connection so PostgreSQL drops it and releases every remaining advisory lock.

Cleanup-failure invalidation is staged.  An open session connection is
invalidated directly; a driver-side invalidate failure falls back to detach,
then to sync Connection.invalidate() on the still-open sync connection (an
AsyncConnection has no detach(), and a bare sync detach() leaves the physical
connection open with its xact locks held).  When the session already returned
the physical connection to the pool (a successful rollback followed by a
close/invalidate failure), Connection methods are invalid on the closed
Connection, so the owning engine re-checks out a physical connection and
invalidates it while checked out.  QueuePool returns the most recently
checked-in connection first, so in the single-connection case this is exactly
the lease connection; invalidating any other idle pooled connection is
harmless, and a completed rollback has already released the xact locks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from prodavan.application.plugins.admin.contracts import PluginUpdateLockLease


class PluginUpdateLockError(RuntimeError):
    """The database did not confirm acquisition or complete owned cleanup."""


@dataclass(frozen=True, slots=True)
class _SqlPluginUpdateLockLease(PluginUpdateLockLease):
    _session: AsyncSession = field(compare=False, repr=False)
    _connection: AsyncConnection = field(compare=False, repr=False)
    _lock_key: str = field(compare=False, repr=False)
    _adapter: Any = field(compare=False, repr=False)


async def _finish_cleanup(cleanup: Awaitable[None]) -> None:
    """Finish cleanup before propagating any task cancellation.

    shield alone would let the caller continue while the physical connection
    is still locked. The loop waits for the independent cleanup task, then
    re-raises cancellation (or the cleanup failure, which is never hidden).
    """

    task = asyncio.ensure_future(cleanup)
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if not task.done():
                continue
            # Propagate a cleanup exception; otherwise finish with cancellation.
            await task
            break
    if cancelled:
        raise asyncio.CancelledError


async def _release_resources(
    session: AsyncSession,
    connection: AsyncConnection | None,
) -> None:
    """Rollback and close, invalidating the physical connection on any failure."""

    failure: BaseException | None = None
    invalidated = False

    async def invalidate() -> None:
        nonlocal connection, invalidated
        if invalidated:
            return
        if connection is None:
            connection = await session.connection()
        try:
            await connection.invalidate()
            invalidated = True
        except BaseException:
            # Discard is the last line of defense: detach removes this physical
            # connection from the pool even when driver-side invalidate fails.
            try:
                await connection.detach()
                invalidated = True
            except BaseException:
                sync_conn = getattr(connection, "sync_connection", None)
                if sync_conn is not None and not sync_conn.closed:
                    # AsyncConnection has no detach(), and a bare sync detach()
                    # leaves the physical connection open with its xact locks
                    # held.  Invalidate the still-open sync Connection instead:
                    # the physical connection is really closed and PostgreSQL
                    # drops every remaining advisory lock.
                    sync_conn.invalidate()
                    invalidated = True
                else:
                    # After a successful rollback the session already returned
                    # the physical connection to the pool and the Connection is
                    # closed, so invalidate()/detach() only raise.  Re-checkout
                    # via the owning engine and invalidate that physical
                    # connection while checked out (QueuePool returns the most
                    # recently checked-in connection first).  The completed
                    # rollback already released the xact locks, so this can
                    # never leave a pooled connection holding locks.
                    fresh = await connection.engine.connect()
                    try:
                        await fresh.invalidate()
                        invalidated = True
                    finally:
                        await fresh.close()

    try:
        await session.rollback()
    except BaseException as exc:
        failure = exc
        try:
            await invalidate()
        except BaseException as invalidate_exc:
            failure = invalidate_exc
    try:
        await session.close()
    except BaseException as close_exc:
        if failure is None:
            failure = close_exc
            try:
                await invalidate()
            except BaseException as invalidate_exc:
                failure = invalidate_exc
    if failure is not None:
        raise failure


class SqlPluginUpdateLock:
    """One-shot PostgreSQL xact advisory lock scoped to one installation."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        owner_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(session_factory):
            raise PluginUpdateLockError("update lock requires an AsyncSession factory")
        self._session_factory = session_factory
        self._owner_factory = owner_factory or (lambda: token_urlsafe(32))

    @staticmethod
    def _key(*, company_id: str, plugin_id: str, plugin_instance_id: str) -> str:
        # JSON length-delimits every component; the expected digest deliberately
        # does not participate: A→B and A→C contend for the same installation.
        return "prodavan.plugin.update:" + json.dumps(
            [company_id, plugin_id, plugin_instance_id],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    async def acquire(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
    ) -> PluginUpdateLockLease | None:
        lease = PluginUpdateLockLease(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            expected_manifest_version=expected_manifest_version,
            expected_artifact_digest=expected_artifact_digest,
            owner_token=self._owner_factory(),
        )
        key = self._key(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
        )
        session = self._session_factory()
        connection: AsyncConnection | None = None
        try:
            await session.begin()
            connection = await session.connection()
            result = await session.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": key},
            )
            acquired = result.scalar() is True
        except BaseException as exc:
            try:
                await _finish_cleanup(_release_resources(session, connection))
            except asyncio.CancelledError:
                raise
            except Exception as cleanup_exc:
                raise PluginUpdateLockError(
                    "distributed update lock acquisition cleanup failed"
                ) from cleanup_exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise PluginUpdateLockError("distributed update lock acquisition failed") from exc
        if not acquired:
            try:
                await _finish_cleanup(_release_resources(session, connection))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise PluginUpdateLockError("contended update lock cleanup failed") from exc
            return None
        assert connection is not None
        return _SqlPluginUpdateLockLease(
            **{
                name: getattr(lease, name)
                for name in PluginUpdateLockLease.__dataclass_fields__
            },
            _session=session,
            _connection=connection,
            _lock_key=key,
            _adapter=self,
        )

    async def release(self, lease: PluginUpdateLockLease) -> None:
        if not isinstance(lease, _SqlPluginUpdateLockLease) or lease._adapter is not self:
            raise PluginUpdateLockError("update lock release owner/binding mismatch")
        try:
            # xact locks end with the transaction. Rollback is the release proof;
            # close alone is never relied on for a pooled production connection.
            await _finish_cleanup(
                _release_resources(lease._session, lease._connection)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PluginUpdateLockError("distributed update lock release failed") from exc


__all__ = ["PluginUpdateLockError", "SqlPluginUpdateLock"]
