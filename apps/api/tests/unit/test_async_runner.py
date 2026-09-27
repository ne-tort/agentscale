"""Unit — Celery run_async disposes engine between loops."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.core.jobs.async_runner import run_async


@pytest.mark.asyncio
async def test_run_async_from_running_loop_disposes_engine() -> None:
    disposed: list[int] = []

    async def _dispose() -> None:
        disposed.append(1)

    async def _work() -> str:
        return "ok"

    with patch(
        "prodavan.infrastructure.persistence.database.dispose_engine",
        new=AsyncMock(side_effect=_dispose),
    ):
        result = await asyncio.to_thread(lambda: run_async(_work()))
    assert result == "ok"
    assert len(disposed) >= 2


def test_run_async_fresh_loop_disposes_engine() -> None:
    disposed: list[int] = []

    async def _dispose() -> None:
        disposed.append(1)

    async def _work() -> int:
        return 7

    with patch(
        "prodavan.infrastructure.persistence.database.dispose_engine",
        new=AsyncMock(side_effect=_dispose),
    ):
        assert run_async(_work()) == 7
    assert len(disposed) >= 2


def test_run_async_reuses_one_background_loop() -> None:
    """Sequential tasks share the persistent worker loop.

    Regression: a fresh loop per task made every loop-bound async resource
    (aiohttp session inside the agent-sandbox SDK) raise
    ``RuntimeError: Event loop is closed`` from the second task on.
    """
    loops: list[asyncio.AbstractEventLoop] = []

    async def _track() -> None:
        loops.append(asyncio.get_running_loop())

    run_async(_track())
    run_async(_track())
    assert loops[0] is loops[1]
    assert not loops[0].is_closed()


def test_run_async_loop_bound_resource_survives_across_calls() -> None:
    """A resource created on the background loop stays usable in later tasks."""
    holder: dict[str, object] = {}

    async def _create() -> None:
        holder["loop"] = asyncio.get_running_loop()
        holder["event"] = asyncio.Event()

    async def _use() -> bool:
        assert asyncio.get_running_loop() is holder["loop"]
        ev = holder["event"]
        assert isinstance(ev, asyncio.Event)
        ev.set()
        return ev.is_set()

    run_async(_create())
    assert run_async(_use()) is True
