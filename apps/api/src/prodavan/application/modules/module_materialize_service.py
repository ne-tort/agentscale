"""Install/uninstall module runtime data in cabinet PG schema."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow

_TABLE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROW_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _parse_seed_items(body: Any) -> list[dict[str, Any]]:
    """Accept `{items:[...]}` or bare list of seed row defs."""
    if body is None:
        return []
    raw = body.get("items") if isinstance(body, dict) else body
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        table_slug = item.get("table_slug")
        row_id = item.get("row_id")
        row_body = item.get("body", {})
        if not isinstance(table_slug, str) or not _TABLE_SLUG_RE.match(table_slug):
            continue
        if not isinstance(row_id, str) or not _ROW_ID_RE.match(row_id):
            continue
        if not isinstance(row_body, dict):
            continue
        out.append({"table_slug": table_slug, "row_id": row_id, "body": row_body})
    return out


class ModuleMaterializeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provisioner = SchemaProvisioner()

    async def install(self, *, cabinet_id: str, module_id: str) -> None:
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return
        await self._provisioner.ensure_data_layer(self._session, schema_name=inst.schema_name)
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.module_installations (module_id)
                VALUES (:module_id)
                ON CONFLICT (module_id) DO NOTHING
                """
            ),
            {"module_id": module_id},
        )
        await self._apply_seed_rows(schema_name=inst.schema_name, module_id=module_id)

    async def _apply_seed_rows(self, *, schema_name: str, module_id: str) -> None:
        """Copy optional meta slug `seed_rows` into module_data_rows (idempotent upsert by PK)."""
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "seed_rows",
            )
        )
        body = q.scalar_one_or_none()
        items = _parse_seed_items(body)
        if not items:
            return
        qschema = qident(schema_name)
        for item in items:
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.module_data_rows
                        (module_id, table_slug, row_id, body, created_by, created_at, updated_at)
                    VALUES
                        (:module_id, :table_slug, :row_id, CAST(:body AS jsonb),
                         'module_seed', now(), now())
                    ON CONFLICT (module_id, table_slug, row_id) DO NOTHING
                    """
                ),
                {
                    "module_id": module_id,
                    "table_slug": item["table_slug"],
                    "row_id": item["row_id"],
                    "body": json.dumps(item["body"]),
                },
            )

    async def uninstall(self, *, cabinet_id: str, module_id: str) -> None:
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(f"DELETE FROM {qschema}.module_data_rows WHERE module_id = :module_id"),
            {"module_id": module_id},
        )
        await self._session.execute(
            text(f"DELETE FROM {qschema}.module_installations WHERE module_id = :module_id"),
            {"module_id": module_id},
        )

    async def uninstall_all_for_module(self, *, module_id: str, cabinet_ids: list[str]) -> None:
        for cabinet_id in cabinet_ids:
            await self.uninstall(cabinet_id=cabinet_id, module_id=module_id)
