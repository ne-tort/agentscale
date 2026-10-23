"""Beat sweep: удаление истёкших строк «Интернет товары» (web_products).

TTL: 72 часа с момента добавления/правки. Истёкшие строки удаляются, чтобы
товар, найденный агентом в магазине, не жил в базе вечно и не «залипал» в
подборе с устаревшей ценой.

Почему sweep, а не TTL на уровне БД: строка лежит в `module_instance_data_rows`
(общая таблица всех модулей) — SQL-триггер по `table_slug` задевал бы другие
модули. Sweep фильтрует строго по `table_slug='web_products'`.

Идемпотентно: повторный проход по уже удалённым строкам ничего не делает.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.persistence.models.modules import (
    ModuleInstanceDataRow,
    ModuleInstanceRow,
)

logger = logging.getLogger(__name__)

_MODULE_ID = "mod_equipment"
_WEB_PRODUCTS_SLUG = "web_products"

# 72 часа — требование продукта: найденный в интернете товар живёт трое суток
# с момента добавления/последней правки (правка продлевает срок).
WEB_PRODUCTS_TTL = timedelta(hours=72)


def _row_timestamp(body: dict[str, Any]) -> datetime | None:
    """Момент, от которого считается TTL: updated_at → created_at → web_* поля.

    Правка строки через MCP обновляет `updated_at`, поэтому «перезапись
    данных» автоматически продлевает срок жизни.
    """
    for key in ("updated_at", "created_at", "added_at"):
        raw = body.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


async def sweep_expired_web_products(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, Any]:
    """Удалить строки web_products старше TTL. Возвращает счётчики."""
    now = now or datetime.now(UTC)
    cutoff = now - WEB_PRODUCTS_TTL
    scanned = 0
    deleted = 0
    kept_no_ts = 0

    result = await session.execute(
        select(ModuleInstanceDataRow)
        .join(
            ModuleInstanceRow,
            ModuleInstanceRow.id == ModuleInstanceDataRow.instance_id,
        )
        .where(
            ModuleInstanceDataRow.table_slug == _WEB_PRODUCTS_SLUG,
            ModuleInstanceRow.module_id == _MODULE_ID,
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        scanned += 1
        body = row.body if isinstance(row.body, dict) else {}
        ts = _row_timestamp(body)
        if ts is None:
            # Без метки времени нельзя судить о возрасте — не удаляем молча,
            # но считаем: такие строки должны быть видны в логе.
            kept_no_ts += 1
            continue
        if ts <= cutoff:
            await session.delete(row)
            deleted += 1

    if deleted:
        await session.commit()

    stats = {
        "scanned": scanned,
        "deleted": deleted,
        "kept_without_timestamp": kept_no_ts,
        "ttl_hours": int(WEB_PRODUCTS_TTL.total_seconds() // 3600),
    }
    if deleted or kept_no_ts:
        logger.info("web_products_ttl_sweep %s", stats)
    return stats
