"""Unit tests — воркер обязан поднимать Redis.

Без Redis-менеджера в процессе воркера `get_redis_manager()` возвращает None,
поэтому:
- `run_with_job_lock` МОЛЧА выполняет задачу без блокировки;
- `cache_set` возвращает False, и `bump_pod_bridge_generation` в strict-режиме
  бросает «cannot persist pod bridge generation; revocation unavailable».

Второе ломало `rematerialize_project`: джоба падала на первом шаге, workspace
проекта не пересобирался, и под продолжал жить со старым
`.prodavan/config.yaml` (на dev это выглядело как 401 «Invalid token» после
смены провайдера, хотя в БД провайдер уже новый).
"""

from __future__ import annotations

import pytest

import prodavan.core.infra.redis_manager as redis_manager_module
from prodavan.core.jobs.tasks import ensure_worker_redis


class _FakeRedis:
    def __init__(self, url=None, required=None) -> None:
        self.url = url
        self.required = required
        self.enabled = bool(url)
        self.started = False

    async def startup(self) -> None:
        self.started = True


@pytest.mark.asyncio
async def test_worker_bootstrap_starts_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakeRedis] = []

    def _factory(**kwargs) -> _FakeRedis:
        mgr = _FakeRedis(**kwargs)
        created.append(mgr)
        return mgr

    monkeypatch.setattr(redis_manager_module, "RedisManager", _factory)
    monkeypatch.setattr(redis_manager_module, "get_redis_manager", lambda: None)
    monkeypatch.setattr("prodavan.config.settings.settings.redis_url", "redis://r:6379/0")

    await ensure_worker_redis()

    assert len(created) == 1
    assert created[0].started is True
    assert created[0].url == "redis://r:6379/0"
    # required=False: воркер не должен падать целиком, когда Redis недоступен, —
    # иначе встанут все периодические задачи (trigger_drain, idle_pause, …)
    assert created[0].required is False


@pytest.mark.asyncio
async def test_worker_redis_bootstrap_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Менеджер уже поднят (повторный сигнал/тест) — второй не создаём."""
    created: list[dict] = []
    monkeypatch.setattr(
        redis_manager_module, "RedisManager", lambda **kw: created.append(kw) or object()
    )
    monkeypatch.setattr(redis_manager_module, "get_redis_manager", lambda: object())

    await ensure_worker_redis()

    assert created == []
