"""PostgreSQL advisory lock helpers (P0/P1 distributed coordination).

Used by periodic workers (pod reconcile, trigger drain, …) so that only one
API process runs a given job at a time, even on multi-replica deployments.

Unlike the Redis-based ``run_with_job_lock`` (which silently runs without
locking when Redis is disabled), advisory locks live in PostgreSQL — the
system of record — so they stay effective when Redis is down or absent.

Typical usage::

    async with advisory_lock(session, "prodavan.pod_reconcile") as held:
        if not held:
            return {"skipped": True, "reason": "lock_held"}
        ... do work ...

The lock is session-level: it is released on ``pg_advisory_unlock`` or when
the session/transaction ends (commit/rollback/close), so a crashed worker
does not hold the lock indefinitely.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _lock_key_sql(key: str) -> str:
    """Return a SQL expression for the advisory lock key derived from ``key``.

    Uses ``hashtext`` so the lock name is a stable string key (not a magic
    int). ``hashtext('…')`` returns int4; ``pg_try_advisory_lock`` accepts a
    single bigint or two int4 keys — a single int4 arg is promoted.
    """
    # key is a literal string interpolated into SQL; it must be a trusted
    # module-level constant (callers pass static names). Quote it defensively.
    safe = key.replace("'", "''")
    return f"hashtext('{safe}')"


@asynccontextmanager
async def advisory_lock(
    session: AsyncSession,
    key: str,
) -> AsyncIterator[bool]:
    """Try to acquire a session-level PG advisory lock.

    Yields ``True`` if the lock was acquired, ``False`` if another process
    holds it. The lock is released on exit (or when the session ends).
    """
    key_sql = _lock_key_sql(key)
    locked = await session.execute(text(f"SELECT pg_try_advisory_lock({key_sql})"))
    held = bool(locked.scalar())
    try:
        yield held
    finally:
        if held:
            await session.execute(text(f"SELECT pg_advisory_unlock({key_sql})"))


async def try_advisory_lock(session: AsyncSession, key: str) -> bool:
    """One-shot acquire (caller must release via ``release_advisory_lock``).

    Prefer ``advisory_lock`` context manager unless the lock lifetime must
    span multiple sessions / transactions.
    """
    key_sql = _lock_key_sql(key)
    locked = await session.execute(text(f"SELECT pg_try_advisory_lock({key_sql})"))
    return bool(locked.scalar())


async def release_advisory_lock(session: AsyncSession, key: str) -> None:
    key_sql = _lock_key_sql(key)
    await session.execute(text(f"SELECT pg_advisory_unlock({key_sql})"))


__all__: list[str] = ["advisory_lock", "try_advisory_lock", "release_advisory_lock"]


# Silence unused-import analyzers for the re-exported Any type alias.
_ = Any
