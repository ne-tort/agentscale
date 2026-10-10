"""Ретраи для стартовых пингов инфраструктуры.

`required=True` (так на dev и в base-конфиге: `OPENSEARCH_REQUIRED`,
`REDIS_REQUIRED`) делает упавший пинг фатальным для всего startup приложения.
Тогда ОДИН мимолётный сбой зависимости роняет API в crash-loop — на dev это
произошло на `opensearch ping returned false`, и заодно перезапустился
agent-runtime в sandbox-поде, из-за чего проект потерял ещё и проектный bind
(MCP-инструменты отвечали «PRODAVAN_* are required»).

Ретраи сохраняют контракт «громко падаем, если зависимость действительно
недоступна», но переживают короткие сбои. Бюджет на старте есть: startupProbe
API-пода даёт 60 × 5 с.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


def resolve_ping_retry(
    attempts: int | None, delay_sec: float | None
) -> tuple[int, float]:
    """Значения из аргументов либо из `INFRA_STARTUP_PING_*`."""
    from prodavan.config.settings import settings

    resolved_attempts = (
        settings.infra_startup_ping_attempts if attempts is None else attempts
    )
    resolved_delay = (
        settings.infra_startup_ping_delay_sec if delay_sec is None else delay_sec
    )
    return max(1, int(resolved_attempts)), max(0.0, float(resolved_delay))


async def ping_with_retry(
    name: str,
    ping: Callable[[], Awaitable[Any]],
    *,
    attempts: int,
    delay_sec: float,
) -> bool:
    """True, если пинг в итоге прошёл. Не бросает — решение принимает вызывающий.

    Успех = «не бросило И вернуло не False»: redis/mongo отвечают исключением
    на недоступность, а OpenSearchStore.ping() возвращает False.
    """
    total = max(1, int(attempts))
    pause = max(0.0, float(delay_sec))
    for attempt in range(1, total + 1):
        try:
            result = await ping()
            if result is not False:
                if attempt > 1:
                    logger.info("%s: ping ok on attempt %s/%s", name, attempt, total)
                return True
            logger.warning("%s: ping returned false (attempt %s/%s)", name, attempt, total)
        except Exception as exc:  # noqa: BLE001 - пинг может упасть по-разному
            logger.warning(
                "%s: ping failed (attempt %s/%s): %s", name, attempt, total, exc
            )
        if attempt < total and pause > 0:
            await asyncio.sleep(pause)
    return False
