"""Idempotent platform bootstrap — basic cabinet + default module bindings."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.platform.bootstrap_config import (
    BOOTSTRAP_MARKER_KEY,
    CAB_BASIC_ID,
    DEFAULT_BASIC_MODULE_IDS,
)
from prodavan.domain.cabinets import CabinetOwnerScope, CabinetStatus, schema_name_for_instance
from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
from prodavan.domain.modules import ModuleCompanyGrantScope
from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import ModuleRow
from prodavan.infrastructure.persistence.models.platform import PlatformBootstrapRow

logger = logging.getLogger(__name__)


class PlatformBootstrapService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provisioner = SchemaProvisioner()
        self._bindings = ModuleBindingService(session)

    async def ensure_bootstrapped(self) -> dict:
        """Run once per environment — creates cab_basic and binds product modules."""
        existing = await self._session.get(PlatformBootstrapRow, BOOTSTRAP_MARKER_KEY)
        if existing is not None:
            return {"status": "already_bootstrapped", "cabinet_id": CAB_BASIC_ID}

        cab = await self._ensure_basic_cabinet()
        await self._ensure_product_module_scopes()
        module_ids = await self._bindings.list_module_ids_for_cabinet(cab.id)
        merged = list(dict.fromkeys([*module_ids, *DEFAULT_BASIC_MODULE_IDS]))
        await self._bindings.replace_module_bindings_for_cabinet(cab.id, merged)

        self._session.add(PlatformBootstrapRow(key=BOOTSTRAP_MARKER_KEY))
        await self._session.commit()
        logger.info("platform bootstrap complete cabinet=%s modules=%s", cab.id, merged)
        return {"status": "bootstrapped", "cabinet_id": cab.id, "module_ids": merged}

    async def _ensure_basic_cabinet(self) -> CabinetInstanceRow:
        row = await self._session.get(CabinetInstanceRow, CAB_BASIC_ID)
        if row is not None:
            if row.company_grant_scope != CabinetCompanyGrantScope.ALL:
                row.company_grant_scope = CabinetCompanyGrantScope.ALL
            if row.base_template != "basic":
                row.base_template = "basic"
            await self._session.flush()
            return row

        row = CabinetInstanceRow(
            id=CAB_BASIC_ID,
            name="Базовый",
            schema_name="pending",
            owner_employee_id=None,
            company_id=None,
            owner_scope=CabinetOwnerScope.PLATFORM,
            owner_company_id=None,
            base_template="basic",
            company_grant_scope=CabinetCompanyGrantScope.ALL,
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        await self._session.flush()
        return row

    async def _ensure_product_module_scopes(self) -> None:
        for module_id in DEFAULT_BASIC_MODULE_IDS:
            row = await self._session.get(ModuleRow, module_id)
            if row is None:
                continue
            if row.company_grant_scope != ModuleCompanyGrantScope.ALL:
                row.company_grant_scope = ModuleCompanyGrantScope.ALL
        await self._session.flush()

    async def apply_default_modules_for_cabinet(self, cabinet_id: str, *, base_template: str) -> None:
        """Auto-bind product modules when cabinet uses basic/base template."""
        if base_template not in ("basic", "base"):
            return
        current = await self._bindings.list_module_ids_for_cabinet(cabinet_id)
        merged = list(dict.fromkeys([*current, *DEFAULT_BASIC_MODULE_IDS]))
        if merged == current:
            return
        await self._bindings.replace_module_bindings_for_cabinet(cabinet_id, merged)
