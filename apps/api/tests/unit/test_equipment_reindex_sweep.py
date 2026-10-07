"""Unit tests — equipment catalog reindex sweep (stale `indexing` healing)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules import equipment_catalog_reindex_sweep as sweep


def _now() -> datetime:
    return datetime.now(UTC)


def test_stale_indexing_requires_status() -> None:
    assert not sweep._stale_indexing({"status": "ready"}, now=_now())
    assert not sweep._stale_indexing({"status": "error"}, now=_now())
    assert not sweep._stale_indexing({}, now=_now())


def test_stale_queued_row_is_stale() -> None:
    """queued без старта (потерянная задача/очередь) лечится как indexing."""
    started = _now() - timedelta(minutes=sweep.STALE_INDEXING_MINUTES + 1)
    assert sweep._stale_indexing({"status": "queued", "indexing_started_at": started.isoformat()}, now=_now())


def test_fresh_queued_row_is_not_stale() -> None:
    body = {"status": "queued", "indexing_started_at": _now().isoformat()}
    assert not sweep._stale_indexing(body, now=_now())


def test_stale_indexing_legacy_row_without_heartbeat() -> None:
    # Row created before the heartbeat feature: treated as stale once so it
    # gets re-enqueued and gains indexing_started_at.
    assert sweep._stale_indexing({"status": "indexing"}, now=_now())


def test_stale_indexing_fresh_heartbeat_is_not_stale() -> None:
    body = {"status": "indexing", "indexing_started_at": _now().isoformat()}
    assert not sweep._stale_indexing(body, now=_now())


def test_stale_indexing_old_heartbeat_is_stale() -> None:
    started = (_now() - timedelta(minutes=sweep.STALE_INDEXING_MINUTES + 1)).isoformat()
    assert sweep._stale_indexing({"status": "indexing", "indexing_started_at": started}, now=_now())


def test_stale_indexing_naive_timestamp_treated_as_utc() -> None:
    started = (_now() - timedelta(minutes=sweep.STALE_INDEXING_MINUTES + 5)).replace(tzinfo=None)
    assert sweep._stale_indexing({"status": "indexing", "indexing_started_at": started.isoformat()}, now=_now())


@pytest.mark.asyncio
async def test_sweep_reenqueues_stale_row(monkeypatch) -> None:
    """A stuck `indexing` row is re-enqueued and gets a fresh heartbeat."""
    enqueued: list[dict] = []

    def _enqueue(**kwargs):
        enqueued.append(kwargs)
        return {"enqueued": True}

    monkeypatch.setattr(sweep, "enqueue_or_run_index_equipment_catalog", _enqueue)
    monkeypatch.setattr(
        sweep,
        "resolve_equipment_catalog_tenancy",
        AsyncMock(return_value=("co1", "cab1", None)),
    )

    data_row = MagicMock()
    data_row.row_id = "row_stuck"
    data_row.body = {
        "status": "indexing",
        "source_kind": "local",
        "indexing_started_at": (_now() - timedelta(hours=3)).isoformat(),
    }
    inst = MagicMock()
    inst.id = "inst1"

    result_mock = MagicMock()
    result_mock.all.return_value = [(data_row, inst)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.commit = AsyncMock()

    out = await sweep.sweep_due_equipment_catalogs(session)
    assert out["requeued"] == 1
    assert len(enqueued) == 1
    assert enqueued[0]["row_id"] == "row_stuck"
    assert enqueued[0]["instance_id"] == "inst1"
    # heartbeat refreshed so the next sweep pass does not double-enqueue
    refreshed = datetime.fromisoformat(data_row.body["indexing_started_at"])
    assert abs((refreshed - _now()).total_seconds()) < 60


@pytest.mark.asyncio
async def test_sweep_marks_paused_stale_row_as_error(monkeypatch) -> None:
    enqueued: list[dict] = []

    def _enqueue(**kwargs):
        enqueued.append(kwargs)
        return {"enqueued": True}

    monkeypatch.setattr(sweep, "enqueue_or_run_index_equipment_catalog", _enqueue)
    monkeypatch.setattr(
        sweep,
        "resolve_equipment_catalog_tenancy",
        AsyncMock(return_value=("co1", "cab1", None)),
    )

    data_row = MagicMock()
    data_row.row_id = "row_paused_stuck"
    data_row.body = {
        "status": "indexing",
        "paused": True,
        "indexing_started_at": (_now() - timedelta(hours=5)).isoformat(),
    }
    inst = MagicMock()
    inst.id = "inst1"

    result_mock = MagicMock()
    result_mock.all.return_value = [(data_row, inst)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.commit = AsyncMock()

    out = await sweep.sweep_due_equipment_catalogs(session)
    assert out["requeued"] == 0
    assert not enqueued
    assert data_row.body["status"] == "error"
    assert "interrupted" in data_row.body["error"]
