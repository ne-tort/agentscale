"""Install/uninstall module runtime data in cabinet PG schema."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow


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
