"""Concurrency primitives for the PostgreSQL integration suite (MIG-INT-PG-01).

Nothing here sleeps to *create* a race.  Two mechanisms are used instead:

* :class:`Gate` — an ``asyncio`` handshake that orders two coroutines without
  any wall-clock dependency;
* :func:`advisory_gate` — a PostgreSQL **session-level** advisory lock held on
  its own connection, plus ``pg_advisory_xact_lock`` inside a worker's
  transaction.  The worker blocks in the database, not in Python, so the test
  can hold "this transaction has written but not yet committed" open for as
  long as it needs.

:func:`wait_until` exists for the few assertions that must *observe* a blocked
peer (for example "the second INSERT is waiting on the unique index").  It is a
bounded wait-for-condition over ``pg_stat_activity``, not a fixed delay: it
returns as soon as the condition holds and fails the test with an explicit
message when it never does.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import text

T = TypeVar("T")

DEFAULT_TIMEOUT = 10.0
"""Upper bound for every bounded wait; a real deadlock fails instead of hanging."""

POLL_INTERVAL = 0.02
"""Observation cadence for :func:`wait_until` (bounded condition polling)."""


# --------------------------------------------------------------------------- #
# in-process ordering
# --------------------------------------------------------------------------- #
class Gate:
    """Two-phase handshake between two coroutines.

    Usage: a *worker* calls :meth:`reached` as the last thing it does before the
    point the test wants to inspect, then ``await``\\ s :meth:`wait`; the test
    calls :meth:`wait_reached` and later :meth:`release`.  Neither side depends
    on elapsed time, so the ordering is the same on a fast laptop and on a
    loaded CI runner.
    """

    def __init__(self) -> None:
        self._reached = asyncio.Event()
        self._released = asyncio.Event()

    def reached(self) -> None:
        """Signal that the worker produced the state the test wanted to see."""
        self._reached.set()

    async def wait_reached(self) -> None:
        """Block until the worker signalled :meth:`reached`."""
        await self._reached.wait()

    async def wait(self) -> None:
        """Worker side: block until the test releases the gate."""
        await self._released.wait()

    def release(self) -> None:
        """Let the parked worker continue."""
        self._released.set()


# --------------------------------------------------------------------------- #
# database-side ordering
# --------------------------------------------------------------------------- #
@contextlib.asynccontextmanager
async def advisory_gate(
    engine,
    *,
    key: int,
) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    """Hold a PostgreSQL session advisory lock; yield a ``release`` callable.

    A worker transaction that calls ``pg_advisory_xact_lock(key)`` blocks inside
    the server until ``release`` runs, which makes "uncommitted write" a stable,
    observable state instead of a timing window.  The lock is released on exit
    even when the test fails.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        await conn.commit()
        released = False

        async def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await conn.commit()

        try:
            yield release
        finally:
            await release()


async def hold_until_advisory(conn, *, key: int) -> None:
    """Block the current transaction on ``key`` until :func:`advisory_gate` releases.

    ``pg_advisory_xact_lock`` is transaction-scoped, so the lock disappears with
    the transaction and cannot leak into a later test.
    """
    await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


# --------------------------------------------------------------------------- #
# bounded observation
# --------------------------------------------------------------------------- #
async def wait_until(
    probe: Callable[[], Awaitable[T]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
    description: str = "condition",
) -> T:
    """Poll ``probe`` until it returns a truthy value or the deadline passes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last: Any = None
    while True:
        last = await probe()
        if last:
            return last
        if loop.time() >= deadline:
            raise AssertionError(
                f"timed out after {timeout:g}s waiting for {description} (last value: {last!r})"
            )
        await asyncio.sleep(interval)


async def blocked_backends(
    harness,
    *,
    needle: str,
    at_least: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    """Wait until ``at_least`` backends are waiting on a lock for ``needle``.

    ``needle`` is matched against ``pg_stat_activity.query`` so the count is
    scoped to the statement this test injected; the schema isolation of the
    suite keeps the whole database to itself, but the filter keeps the assertion
    honest even on a shared server.
    """
    sql = text(
        """
        SELECT count(*)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
          AND wait_event_type = 'Lock'
          AND query ILIKE '%' || :needle || '%'
        """
    )

    async def probe() -> int:
        async with harness.engine.connect() as conn:
            count = int(await conn.scalar(sql, {"needle": needle}) or 0)
        return count if count >= at_least else 0

    return await wait_until(
        probe,
        timeout=timeout,
        description=f"{at_least} backend(s) blocked on {needle!r}",
    )


# --------------------------------------------------------------------------- #
# small value helpers
# --------------------------------------------------------------------------- #
def sha256_digest(value: Any) -> str:
    """``sha256:<64 hex>`` over a canonical JSON encoding of ``value``.

    The schema constrains every digest column with
    ``~ '^sha256:[0-9a-f]{64}$'``, so the suite derives real digests instead of
    pasting plausible-looking constants.
    """
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def sha256_of_text(value: str) -> str:
    """``sha256:<64 hex>`` over a literal string (artefact digests)."""
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _cause_chain(error: Exception, *, limit: int = 8) -> list[Exception]:
    """The exception followed by its ``__cause__``/``__context__`` chain.

    SQLAlchemy's asyncpg dialect rewraps the driver error twice
    (``asyncpg.exceptions.ForeignKeyViolationError`` ->
    ``AsyncAdapt_asyncpg_dbapi.IntegrityError`` -> ``sqlalchemy.exc.IntegrityError``),
    and only the *innermost* object carries ``constraint_name`` and, for some
    error classes, ``sqlstate``.  Reading only ``error.orig`` therefore loses the
    constraint name, which is why the chain is walked here.
    """
    chain: list[Exception] = []
    seen: set[int] = set()
    node: BaseException | None = error
    while isinstance(node, Exception) and len(chain) < limit and id(node) not in seen:
        seen.add(id(node))
        chain.append(node)
        node = node.__cause__ or node.__context__
    return chain


def sqlstate_of(error: Exception) -> str | None:
    """Best-effort SQLSTATE for an SQLAlchemy ``DBAPIError``.

    ``asyncpg`` exposes ``sqlstate``; older/other drivers only expose ``pgcode``.
    Reading both keeps the assertions explicit about *which* PostgreSQL error was
    expected (23505 unique, 23503 foreign key, 23514 check) instead of matching on
    a message that the driver may localise or reformat.  The whole cause chain is
    inspected, because SQLAlchemy's rewrap layer does not forward every attribute.
    """
    for node in [getattr(error, "orig", error), *_cause_chain(error)]:
        value = getattr(node, "sqlstate", None) or getattr(node, "pgcode", None)
        if value:
            return str(value)
    return None


_CONSTRAINT_IN_MESSAGE = re.compile(r'constraint "([^"]+)"')


def violated_constraint(error: Exception) -> str:
    """The constraint/index a server-side violation named, if any.

    Preferred over matching the exception text: PostgreSQL translates messages
    with ``lc_messages``, but the structured ``constraint_name`` field is not
    translated.  It lives on the **innermost** ``asyncpg`` error, so the whole
    cause chain is searched; the message is only a last-resort fallback for a
    driver that does not expose the field at all.
    """
    for node in [_ for _ in _cause_chain(error)]:
        structured = getattr(node, "constraint_name", None)
        if structured:
            return str(structured)
    for node in _cause_chain(error):
        match = _CONSTRAINT_IN_MESSAGE.search(str(node))
        if match:
            return match.group(1)
    return str(getattr(error, "orig", error))


__all__ = [
    "DEFAULT_TIMEOUT",
    "Gate",
    "POLL_INTERVAL",
    "advisory_gate",
    "blocked_backends",
    "hold_until_advisory",
    "sha256_digest",
    "sha256_of_text",
    "sqlstate_of",
    "violated_constraint",
    "wait_until",
]
