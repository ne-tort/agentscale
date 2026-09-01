"""Unit tests — trigger outbox retry / fail semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.projects import ProjectStatus, TriggerStatus


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


@pytest.mark.asyncio
async def test_claim_by_id_leases_when_project_active() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    now = datetime.now(UTC)
    row = SimpleNamespace(
        id="trg_1",
        project_id="proj_1",
        status=TriggerStatus.QUEUED,
        attempts=0,
        available_at=None,
        lease_until=None,
        leased_by=None,
        last_error="old",
    )
    project = SimpleNamespace(status=ProjectStatus.ACTIVE)
    result = AsyncMock()
    result.scalar_one_or_none = lambda: row
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=project)
    gate = ProjectTriggerService(session)
    with patch("prodavan.application.projects.trigger_service.settings") as settings:
        settings.trigger_outbox_lease_sec = 30
        claimed = await gate.claim_by_id(trigger_id="trg_1", worker_id="kafka_disp")
    assert claimed is row
    assert row.attempts == 1
    assert row.leased_by == "kafka_disp"
    assert row.lease_until is not None
    assert row.lease_until > now
    assert row.last_error is None


@pytest.mark.asyncio
async def test_claim_by_id_skips_paused_project() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    row = SimpleNamespace(
        id="trg_2",
        project_id="proj_2",
        kind="chat.message",
        status=TriggerStatus.QUEUED,
        attempts=0,
        available_at=None,
        lease_until=None,
        leased_by=None,
        last_error=None,
    )
    project = SimpleNamespace(status=ProjectStatus.PAUSED)
    result = AsyncMock()
    result.scalar_one_or_none = lambda: row
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=project)
    gate = ProjectTriggerService(session)
    with patch("prodavan.application.projects.trigger_service.settings") as settings:
        settings.trigger_outbox_lease_sec = 30
        claimed = await gate.claim_by_id(trigger_id="trg_2")
    assert claimed is None
    assert row.attempts == 0
