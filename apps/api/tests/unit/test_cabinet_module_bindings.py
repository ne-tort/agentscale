"""Unit — cabinet company grants may be empty; module↔cabinet replace."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.cabinets.grant_service import CabinetGrantService
from prodavan.application.modules.module_binding_service import ModuleBindingService


@pytest.mark.asyncio
async def test_replace_company_grants_allows_empty() -> None:
    session = AsyncMock()
    existing = MagicMock()
    existing.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=existing)
    inst = SimpleNamespace(company_id="co_1")
    session.get = AsyncMock(return_value=inst)

    svc = CabinetGrantService(session)
    result = await svc.replace_company_grants("cab_1", [])
    assert result == []
    assert inst.company_id is None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_list_module_ids_for_cabinet() -> None:
    session = AsyncMock()
    q = MagicMock()
    q.scalars.return_value.all.return_value = ["mod_a", "mod_b"]
    session.execute = AsyncMock(return_value=q)
    svc = ModuleBindingService(session)
    ids = await svc.list_module_ids_for_cabinet("cab_1")
    assert ids == ["mod_a", "mod_b"]
