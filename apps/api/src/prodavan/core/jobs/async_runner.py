"""Async helpers for Celery sync workers (C-JOBS)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a sync Celery task.

    Creates a fresh event loop and disposes the shared async engine so
    connections are never bound to a previous loop (prefork workers).
    """

    async def _with_fresh_engine() -> T:
        from prodavan.infrastructure.persistence import database as db

        await db.dispose_engine()
        try:
            return await coro
        finally:
            await db.dispose_engine()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # Nested call from an already-running loop (e.g. eager tests in async).
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _with_fresh_engine()).result()
    return asyncio.run(_with_fresh_engine())
