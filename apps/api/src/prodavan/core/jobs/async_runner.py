"""Async helpers for Celery sync workers (C-JOBS)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _background_loop() -> asyncio.AbstractEventLoop:
    """One persistent event loop per worker process.

    Async clients bind to the loop they are first used on: aiohttp sessions
    (kubernetes_asyncio inside the agent-sandbox SDK), httpx pools, asyncpg.
    With a fresh ``asyncio.run`` loop per task every resource created by an
    earlier task died with ``RuntimeError: Event loop is closed`` — observed
    live: the first pod_reconcile pass after worker start worked, every
    subsequent pass failed on the shared SDK client. A daemon-thread loop
    keeps loop-bound resources valid for the whole worker process lifetime.
    """
    global _bg_loop
    with _bg_loop_lock:
        if _bg_loop is None or _bg_loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="celery-async-loop", daemon=True
            )
            thread.start()
            _bg_loop = loop
        return _bg_loop


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a sync Celery task.

    Schedules onto the process-wide background loop (see ``_background_loop``)
    and disposes the shared async engine around the call so DB connections
    are never shared across tasks (prefork workers).
    """

    async def _with_fresh_engine() -> T:
        from prodavan.infrastructure.persistence import database as db

        await db.dispose_engine()
        try:
            return await coro
        finally:
            await db.dispose_engine()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running = False
    else:
        running = True
    if running:
        # Nested call from an already-running loop (e.g. eager tests in
        # async): cannot touch the background loop from here synchronously.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _with_fresh_engine()).result()
    future = asyncio.run_coroutine_threadsafe(_with_fresh_engine(), _background_loop())
    return future.result()
