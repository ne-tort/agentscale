"""Unit tests — Celery job Redis lock coalesce."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.core.jobs.locks import run_with_job_lock


@pytest.mark.asyncio
async def test_run_with_job_lock_skips_when_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.jobs.locks.get_redis_manager",
        lambda: SimpleNamespace(enabled=True),
    )

    async def _no_lock(*_a, **_k):
        return None

    monkeypatch.setattr("prodavan.core.jobs.locks.acquire_lock", _no_lock)
    called = False

    async def _fn():
        nonlocal called
        called = True
        return {"ok": True}

    out = await run_with_job_lock("trigger_drain", ttl_sec=10, fn=_fn)
    assert called is False
    assert out["skipped"] is True


@pytest.mark.asyncio
async def test_run_with_job_lock_runs_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prodavan.core.jobs.locks.get_redis_manager", lambda: None)

    async def _fn():
        return {"ok": True, "n": 1}

    out = await run_with_job_lock("trigger_drain", ttl_sec=10, fn=_fn)
    assert out == {"ok": True, "n": 1}
