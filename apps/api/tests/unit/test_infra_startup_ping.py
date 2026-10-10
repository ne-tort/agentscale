"""Unit tests — ретраи стартовых пингов инфраструктуры.

`OPENSEARCH_REQUIRED` / `REDIS_REQUIRED` на dev и в base-конфиге = true, поэтому
упавший пинг делает startup фатальным. Один мимолётный сбой зависимости ронял
API в crash-loop (на dev — `opensearch ping returned false`), и заодно
перезапустился agent-runtime в sandbox-поде, из-за чего проект потерял ещё и
проекционный bind. Ретраи переживают короткий сбой, сохраняя контракт «громко
падаем, если зависимости действительно нет».
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.core.infra.startup_ping import ping_with_retry, resolve_ping_retry


@pytest.mark.asyncio
async def test_first_success_does_not_retry() -> None:
    ping = AsyncMock(return_value=True)

    assert await ping_with_retry("x", ping, attempts=5, delay_sec=0) is True
    assert ping.await_count == 1


@pytest.mark.asyncio
async def test_retries_false_then_succeeds() -> None:
    ping = AsyncMock(side_effect=[False, False, True])

    assert await ping_with_retry("x", ping, attempts=5, delay_sec=0) is True
    assert ping.await_count == 3


@pytest.mark.asyncio
async def test_retries_exceptions_then_succeeds() -> None:
    ping = AsyncMock(side_effect=[ConnectionError("boom"), True])

    assert await ping_with_retry("x", ping, attempts=3, delay_sec=0) is True
    assert ping.await_count == 2


@pytest.mark.asyncio
async def test_gives_up_after_all_attempts() -> None:
    ping = AsyncMock(return_value=False)

    assert await ping_with_retry("x", ping, attempts=4, delay_sec=0) is False
    assert ping.await_count == 4


@pytest.mark.asyncio
async def test_none_result_counts_as_success() -> None:
    """redis/mongo отвечают None/True, а False — только OpenSearchStore.ping."""
    ping = AsyncMock(return_value=None)

    assert await ping_with_retry("x", ping, attempts=2, delay_sec=0) is True
    assert ping.await_count == 1


def test_resolve_ping_retry_falls_back_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.config.settings.settings.infra_startup_ping_attempts", 7
    )
    monkeypatch.setattr(
        "prodavan.config.settings.settings.infra_startup_ping_delay_sec", 0.5
    )

    assert resolve_ping_retry(None, None) == (7, 0.5)
    # явные значения важнее настроек
    assert resolve_ping_retry(2, 0.0) == (2, 0.0)
    # меньше одной попытки быть не может
    assert resolve_ping_retry(0, -1.0) == (1, 0.0)


# --- OpenSearchManager ----------------------------------------------------


def _store_with_ping(results: list) -> AsyncMock:
    store = AsyncMock()
    store.ping = AsyncMock(side_effect=results)
    store.aclose = AsyncMock(return_value=None)
    return store


@pytest.mark.asyncio
async def test_opensearch_survives_transient_ping_failure() -> None:
    from prodavan.core.infra.opensearch_manager import OpenSearchManager

    store = _store_with_ping([False, True])
    mgr = OpenSearchManager(
        url="http://os:9200",
        enabled=True,
        required=True,
        ping_attempts=3,
        ping_delay_sec=0,
    )

    with patch(
        "prodavan.core.infra.opensearch_manager.OpenSearchStore", return_value=store
    ):
        await mgr.startup()

    assert store.ping.await_count == 2
    # зависимость подключена, а не подменена in-memory заглушкой
    assert mgr.store is store
    assert mgr.enabled is True


@pytest.mark.asyncio
async def test_opensearch_still_fails_when_really_down() -> None:
    """Контракт сохранён: required=True и реально мёртвый индекс → startup падает."""
    from prodavan.core.infra.opensearch_manager import OpenSearchManager

    store = _store_with_ping([False, False, False])
    mgr = OpenSearchManager(
        url="http://os:9200",
        enabled=True,
        required=True,
        ping_attempts=3,
        ping_delay_sec=0,
    )

    with patch(
        "prodavan.core.infra.opensearch_manager.OpenSearchStore", return_value=store
    ):
        with pytest.raises(RuntimeError):
            await mgr.startup()

    assert store.ping.await_count == 3


@pytest.mark.asyncio
async def test_opensearch_degrades_when_not_required() -> None:
    from prodavan.application.search_index.adapters.memory_store import (
        InMemorySearchIndexStore,
    )
    from prodavan.core.infra.opensearch_manager import OpenSearchManager

    store = _store_with_ping([False, False])
    mgr = OpenSearchManager(
        url="http://os:9200",
        enabled=True,
        required=False,
        ping_attempts=2,
        ping_delay_sec=0,
    )

    with patch(
        "prodavan.core.infra.opensearch_manager.OpenSearchStore", return_value=store
    ):
        await mgr.startup()

    assert isinstance(mgr.store, InMemorySearchIndexStore)


# --- RedisManager ---------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_survives_transient_ping_failure() -> None:
    from prodavan.core.infra.redis_manager import RedisManager

    client = AsyncMock()
    client.ping = AsyncMock(side_effect=[ConnectionError("reset"), True])
    client.aclose = AsyncMock(return_value=None)
    fake_redis = AsyncMock()
    fake_redis.from_url = lambda *a, **k: client

    mgr = RedisManager("redis://r:6379/0", required=True, ping_attempts=3, ping_delay_sec=0)

    with patch.dict("sys.modules", {"redis.asyncio": fake_redis}):
        await mgr.startup()

    assert client.ping.await_count == 2
    assert mgr.client is client
