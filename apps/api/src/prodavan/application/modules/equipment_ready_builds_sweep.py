"""Beat sweep: автообновление каталога «Готовые сборки» (WAVE11).

Требование «динамическая автообновляемая по цене база сборок» не выполняется
одними пользовательскими триггерами: цены меняются в каталоге поставщика, а не
в наших строках, поэтому запись в ``ready_builds`` может не происходить днями.
Sweep периодически резолвит ключи пула в свежие лучшие цены.

Обход идёт по **инстансам модуля**, а не по проектам: таблицы каталога
``chats: all`` (строки с ``session_id = NULL``), тенант для OpenSearch не
нужен — индекс выводится из ``catalog_id`` строки каталога, поиск идёт с
``apply_tenant_filter=False`` (как в пайплайне позиций).

ACL проекта намеренно не используется: это системная фоновая задача, а не
пользовательский запрос. Записи идут через ``ModuleInstanceService`` — тот же
слой, что и у ``ModuleRowIO``, поэтому валидация колонок сохраняется.
Как и другие sweep'ы проекта, пишем напрямую, минуя сервисный ACL.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_offers_service import MODULE_ID
from prodavan.application.modules.equipment_ready_builds_service import (
    BUILDS_TABLE,
    POOL_TABLE,
    ReadyBuildsPipelineService,
)
from prodavan.infrastructure.persistence.models.modules import (
    ModuleInstanceDataRow,
    ModuleInstanceRow,
)

logger = logging.getLogger(__name__)


class _InstanceRowIO:
    """Минимальный IO для sweep: те же методы, что использует пайплайн каталога.

    Пайплайн каталога читает ``list`` и пишет ``update`` — больше ничего,
    поэтому полный ``ModuleRowIO`` (с проектным ACL) здесь избыточен.
    """

    def __init__(self, session: AsyncSession, *, instance_id: str) -> None:
        self._session = session
        self._instance_id = instance_id

    async def list(self, table_slug: str) -> list[dict[str, Any]]:
        from prodavan.application.modules.module_instance_service import (
            ModuleInstanceService,
        )

        rows = await ModuleInstanceService(self._session).list_data_rows(
            instance_id=self._instance_id, table_slug=table_slug, session_id=None
        )
        return [
            {"module_id": MODULE_ID, "instance_id": self._instance_id, **row}
            for row in rows
        ]

    async def list_project_wide(self, table_slug: str) -> list[dict[str, Any]]:
        # Каталог — chats: all, проектной ширины не существует.
        return await self.list(table_slug)

    async def update(
        self, table_slug: str, row_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        from prodavan.application.modules.module_instance_service import (
            ModuleInstanceService,
        )

        return await ModuleInstanceService(self._session).upsert_data_row(
            instance_id=self._instance_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            session_id=None,
        )

    async def create(self, table_slug: str, body: dict[str, Any]) -> dict[str, Any]:
        from prodavan.application.modules.module_instance_service import (
            ModuleInstanceService,
        )

        return await ModuleInstanceService(self._session).create_data_row(
            instance_id=self._instance_id,
            table_slug=table_slug,
            body=body,
            session_id=None,
        )

    async def delete(self, table_slug: str, row_id: str) -> bool:
        from prodavan.application.modules.module_instance_service import (
            ModuleInstanceService,
        )

        return await ModuleInstanceService(self._session).delete_data_row(
            instance_id=self._instance_id, table_slug=table_slug, row_id=row_id
        )


async def _catalog_instance_ids(session: AsyncSession) -> list[str]:
    """Инстансы mod_equipment, где есть хоть одна строка каталога сборок."""
    result = await session.execute(
        select(ModuleInstanceDataRow.instance_id)
        .join(
            ModuleInstanceRow,
            ModuleInstanceRow.id == ModuleInstanceDataRow.instance_id,
        )
        .where(
            ModuleInstanceDataRow.table_slug.in_([POOL_TABLE, BUILDS_TABLE]),
            ModuleInstanceRow.module_id == MODULE_ID,
        )
        .distinct()
    )
    return [str(row[0]) for row in result.all() if row[0]]


async def sweep_ready_builds(session: AsyncSession) -> dict[str, Any]:
    """Прогон пайплайна каталога по всем инстансам, где каталог непустой."""
    stats: dict[str, Any] = {"instances": 0, "updated": 0, "failed": 0}
    instance_ids = await _catalog_instance_ids(session)
    stats["instances"] = len(instance_ids)
    for instance_id in instance_ids:
        io = _InstanceRowIO(session, instance_id=instance_id)
        try:
            result = await ReadyBuildsPipelineService(session=session).run(io)
        except Exception:
            stats["failed"] += 1
            logger.exception(
                "ready_builds sweep failed instance=%s", instance_id
            )
            continue
        touched = sum(
            int(result.get(k) or 0)
            for k in ("items_updated", "slots_updated", "builds_updated", "groups_updated")
        )
        if touched:
            stats["updated"] += 1
        logger.info(
            "ready_builds sweep instance=%s items=%s builds=%s touched=%s unresolved=%s",
            instance_id,
            result.get("items"),
            result.get("builds"),
            touched,
            result.get("unresolved_slots"),
        )
    return stats
