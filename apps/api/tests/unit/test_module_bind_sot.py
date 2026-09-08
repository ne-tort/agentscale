"""Unit tests for ModuleBindKind SoT resolve helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.module_instance_service import (
    OWNER_CABINET,
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.domain.modules import ModuleBindKind


@pytest.mark.asyncio
async def test_resolve_sot_follows_global_project_bind() -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(cabinet_id="cab_1"))
    svc = ModuleInstanceService(session)
    cab_inst = SimpleNamespace(id="minst_cab", module_id="mod_prompts")

    mp = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    mp_result = MagicMock()
    mp_result.scalar_one_or_none.return_value = mp

    async def execute_side_effect(stmt, *args, **kwargs):
        # First call in resolve for project local get_instance uses execute
        return mp_result

    session.execute = AsyncMock(side_effect=execute_side_effect)

    with (
        patch.object(svc, "get_instance", AsyncMock(side_effect=[None, cab_inst])),
        patch.object(
            svc,
            "resolve_sot_instance",
            wraps=svc.resolve_sot_instance,
        ) as _,
    ):
        # Manual path: get_instance project None, then MP global, then cabinet resolve
        async def get_inst(**kwargs):
            if kwargs.get("owner_kind") == OWNER_PROJECT:
                return None
            if kwargs.get("owner_kind") == OWNER_CABINET:
                return cab_inst
            return None

        with patch.object(svc, "get_instance", AsyncMock(side_effect=get_inst)):
            # MP query
            session.execute = AsyncMock(return_value=mp_result)
            out = await ModuleInstanceService.resolve_sot_instance(
                svc,
                module_id="mod_prompts",
                owner_kind=OWNER_PROJECT,
                owner_id="proj_1",
            )
    assert out is cab_inst


@pytest.mark.asyncio
async def test_sot_may_edit_false_for_locked_global() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)
    mp = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mp
    session.execute = AsyncMock(return_value=result)

    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        ok = await svc.sot_may_edit(
            module_id="mod_prompts",
            owner_kind=OWNER_PROJECT,
            owner_id="proj_1",
        )
    assert ok is False


def test_module_bind_kind_enum() -> None:
    assert ModuleBindKind.LOCAL == "local"
    assert ModuleBindKind.GLOBAL == "global"


@pytest.mark.asyncio
async def test_resolve_sot_local_project_without_instance_returns_none() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)
    mp = SimpleNamespace(bind_kind=ModuleBindKind.LOCAL, child_may_edit=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mp
    session.execute = AsyncMock(return_value=result)

    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        out = await svc.resolve_sot_instance(
            module_id="mod_equipment",
            owner_kind=OWNER_PROJECT,
            owner_id="proj_1",
        )
    assert out is None


@pytest.mark.asyncio
async def test_resolve_sot_local_cabinet_without_instance_returns_none() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)
    mc = SimpleNamespace(bind_kind=ModuleBindKind.LOCAL, child_may_edit=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mc
    session.execute = AsyncMock(return_value=result)

    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        out = await svc.resolve_sot_instance(
            module_id="mod_equipment",
            owner_kind=OWNER_CABINET,
            owner_id="cab_1",
        )
    assert out is None
