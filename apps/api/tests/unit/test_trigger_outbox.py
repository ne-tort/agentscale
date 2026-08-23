"""Unit tests — trigger outbox retry / fail semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.projects import TriggerStatus


@pytest.mark.asyncio
async def test_release_for_retry_sets_backoff_when_attempts_remain() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    gate = ProjectTriggerService(session)
    row = SimpleNamespace(
        status=TriggerStatus.QUEUED,
        attempts=2,
        lease_until=datetime.now(UTC),
        leased_by="w1",
        available_at=None,
        last_error=None,
    )
    with patch("prodavan.application.projects.trigger_service.settings") as settings:
        settings.trigger_outbox_max_attempts = 5
        settings.trigger_outbox_backoff_sec = 3.0
        retried = await gate.release_for_retry(row, reason="boom")

    assert retried is True
    assert row.status == TriggerStatus.QUEUED
    assert row.lease_until is None
    assert row.leased_by is None
    assert row.last_error == "boom"
    assert row.available_at is not None
    assert row.available_at > datetime.now(UTC) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_release_for_retry_fails_at_max_attempts() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    gate = ProjectTriggerService(session)
    row = SimpleNamespace(
        status=TriggerStatus.QUEUED,
        attempts=5,
        lease_until=datetime.now(UTC),
        leased_by="w1",
        available_at=None,
        last_error=None,
    )
    with patch("prodavan.application.projects.trigger_service.settings") as settings:
        settings.trigger_outbox_max_attempts = 5
        settings.trigger_outbox_backoff_sec = 3.0
        retried = await gate.release_for_retry(row, reason="give up")

    assert retried is False
    assert row.status == TriggerStatus.FAILED
    assert row.last_error == "give up"
    assert row.lease_until is None


@pytest.mark.asyncio
async def test_mark_done_clears_lease() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    gate = ProjectTriggerService(session)
    row = SimpleNamespace(
        status=TriggerStatus.QUEUED,
        lease_until=datetime.now(UTC),
        leased_by="w1",
        available_at=datetime.now(UTC),
        last_error="x",
    )
    await gate.mark_done(row)
    assert row.status == TriggerStatus.DONE
    assert row.lease_until is None
    assert row.leased_by is None
    assert row.available_at is None
    assert row.last_error is None
