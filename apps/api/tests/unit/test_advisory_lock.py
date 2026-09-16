"""Unit tests — PG advisory lock helper (audit POD-P2a)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.core.infra.advisory_lock import (
    advisory_lock,
    release_advisory_lock,
    try_advisory_lock,
)


def _session_with_lock_result(held: bool) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = held
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_advisory_lock_acquires_and_releases() -> None:
    session = _session_with_lock_result(held=True)
    async with advisory_lock(session, "prodavan.pod_reconcile") as held:
        assert held is True
    # acquire + release = two executes
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_advisory_lock_reports_not_held() -> None:
    session = _session_with_lock_result(held=False)
    async with advisory_lock(session, "prodavan.pod_reconcile") as held:
        assert held is False
    # acquire attempted, no release (was not held)
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_advisory_lock_releases_on_exception() -> None:
    session = _session_with_lock_result(held=True)
    with pytest.raises(RuntimeError, match="boom"):
        async with advisory_lock(session, "prodavan.pod_reconcile"):
            raise RuntimeError("boom")
    # acquire + release even on exception
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_try_and_release_advisory_lock() -> None:
    session = _session_with_lock_result(held=True)
    assert await try_advisory_lock(session, "prodavan.pod_reconcile") is True
    await release_advisory_lock(session, "prodavan.pod_reconcile")
    assert session.execute.await_count == 2
