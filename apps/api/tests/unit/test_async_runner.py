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
