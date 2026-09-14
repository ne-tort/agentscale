"""Unit tests — ModuleBindKind SoT resolve, lock flags, and defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.module_instance_service import (
    OWNER_CABINET,
    OWNER_COMPANY,
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.domain.errors import AppError
from prodavan.domain.modules import (
    GLOBAL_DEFAULT_PROJECT_MODULES,
    LOCAL_DEFAULT_PROJECT_MODULES,
    ModuleBindKind,
    default_cabinet_bind_kind,
    default_child_may_edit,
    default_project_bind_kind,
)


def test_module_bind_kind_enum() -> None:
    assert ModuleBindKind.LOCAL == "local"
    assert ModuleBindKind.GLOBAL == "global"


def test_default_bind_helpers() -> None:
    assert default_project_bind_kind("mod_prompts") == ModuleBindKind.GLOBAL
    assert default_project_bind_kind("mod_mcp") == ModuleBindKind.GLOBAL
    assert default_project_bind_kind("mod_files") == ModuleBindKind.GLOBAL
    assert default_project_bind_kind("mod_equipment") == ModuleBindKind.GLOBAL
    assert default_project_bind_kind("mod_custom") == ModuleBindKind.GLOBAL
    assert default_cabinet_bind_kind("mod_prompts") == ModuleBindKind.LOCAL
    assert default_cabinet_bind_kind("mod_files") == ModuleBindKind.LOCAL
    assert default_cabinet_bind_kind("mod_equipment") == ModuleBindKind.LOCAL
    assert default_child_may_edit(ModuleBindKind.LOCAL) is True
    assert default_child_may_edit(ModuleBindKind.GLOBAL) is False
    assert default_child_may_edit("local") is True
    assert LOCAL_DEFAULT_PROJECT_MODULES == frozenset()
    assert GLOBAL_DEFAULT_PROJECT_MODULES == frozenset({"mod_prompts", "mod_mcp", "mod_files"})


@pytest.mark.asyncio
async def test_resolve_sot_follows_global_project_bind() -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(cabinet_id="cab_1"))
    svc = ModuleInstanceService(session)
    cab_inst = SimpleNamespace(id="minst_cab", module_id="mod_prompts")
    mp = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    mp_result = MagicMock()
    mp_result.scalar_one_or_none.return_value = mp

    async def get_inst(**kwargs):
        if kwargs.get("owner_kind") == OWNER_PROJECT:
            return None
        if kwargs.get("owner_kind") == OWNER_CABINET:
            return cab_inst
        return None

    with patch.object(svc, "get_instance", AsyncMock(side_effect=get_inst)):
        session.execute = AsyncMock(return_value=mp_result)
        out = await svc.resolve_sot_instance(
            module_id="mod_prompts",
            owner_kind=OWNER_PROJECT,
            owner_id="proj_1",
        )
    assert out is cab_inst


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


@pytest.mark.asyncio
async def test_resolve_sot_global_cabinet_walks_to_company() -> None:
    session = MagicMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(owner_company_id="co_1", company_id="co_1")
    )
    svc = ModuleInstanceService(session)
    company_inst = SimpleNamespace(id="minst_co", module_id="mod_prompts")
    mc = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mc
    session.execute = AsyncMock(return_value=result)

    async def get_inst(**kwargs):
        if kwargs.get("owner_kind") == OWNER_CABINET:
            return None
        if kwargs.get("owner_kind") == OWNER_COMPANY:
            return company_inst
        return None

    with patch.object(svc, "get_instance", AsyncMock(side_effect=get_inst)):
        out = await svc.resolve_sot_instance(
            module_id="mod_prompts",
            owner_kind=OWNER_CABINET,
            owner_id="cab_1",
        )
    assert out is company_inst


@pytest.mark.asyncio
async def test_sot_may_edit_matrix() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)

    # Local leaf instance → always editable.
    with patch.object(svc, "get_instance", AsyncMock(return_value=SimpleNamespace(id="x"))):
        assert await svc.sot_may_edit(
            module_id="mod_equipment", owner_kind=OWNER_PROJECT, owner_id="p1"
        )

    # Global locked MP → not editable without local leaf.
    locked = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = locked
    session.execute = AsyncMock(return_value=result)
    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        assert not await svc.sot_may_edit(
            module_id="mod_prompts", owner_kind=OWNER_PROJECT, owner_id="p1"
        )

    # Global unlocked MP → editable shared SoT.
    unlocked = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=True)
    result.scalar_one_or_none.return_value = unlocked
    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        assert await svc.sot_may_edit(
            module_id="mod_prompts", owner_kind=OWNER_PROJECT, owner_id="p1"
        )

    # Global locked MC → not editable.
    mc_locked = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result.scalar_one_or_none.return_value = mc_locked
    with patch.object(svc, "get_instance", AsyncMock(return_value=None)):
        assert not await svc.sot_may_edit(
            module_id="mod_prompts", owner_kind=OWNER_CABINET, owner_id="cab_1"
        )


@pytest.mark.asyncio
async def test_ensure_project_instance_rejects_global_bind() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)
    mp = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mp
    session.execute = AsyncMock(return_value=result)

    with (
        patch.object(svc, "get_instance", AsyncMock(return_value=None)),
        pytest.raises(AppError) as ei,
    ):
        await svc.ensure_project_instance(project_id="proj_1", module_id="mod_prompts")
    assert ei.value.status == 422


@pytest.mark.asyncio
async def test_ensure_company_instance_global_creates_platform_sot() -> None:
    session = MagicMock()
    svc = ModuleInstanceService(session)
    grant = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = grant
    session.execute = AsyncMock(return_value=result)
    platform = SimpleNamespace(id="minst_plat")

    with (
        patch.object(svc, "get_instance", AsyncMock(return_value=None)),
        patch.object(svc, "ensure_platform_instance", AsyncMock(return_value=platform)) as ensure_plat,
    ):
        out = await svc.ensure_company_instance(company_id="co_1", module_id="mod_x")

    assert out is platform
    ensure_plat.assert_awaited_once_with(module_id="mod_x")


@pytest.mark.asyncio
async def test_ensure_cabinet_instance_global_uses_company_sot() -> None:
    session = MagicMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(owner_company_id="co_1", company_id="co_1")
    )
    svc = ModuleInstanceService(session)
    mc = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = mc
    session.execute = AsyncMock(return_value=result)
    parent = SimpleNamespace(id="minst_plat")

    with (
        patch.object(svc, "get_instance", AsyncMock(return_value=None)),
        patch.object(svc, "ensure_company_instance", AsyncMock(return_value=parent)) as ensure_co,
    ):
        out = await svc.ensure_cabinet_instance(cabinet_id="cab_1", module_id="mod_prompts")

    assert out is parent
    ensure_co.assert_awaited_once_with(company_id="co_1", module_id="mod_prompts")


@pytest.mark.asyncio
async def test_sot_for_project_local_uses_ensure_not_recursion() -> None:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    session = AsyncMock()
    svc = ProjectRuntimeModuleService.__new__(ProjectRuntimeModuleService)
    svc._session = session
    svc._instances = MagicMock()
    leaf = SimpleNamespace(id="minst_local")
    svc._instances.ensure_project_instance = AsyncMock(return_value=leaf)
    binding = SimpleNamespace(bind_kind=ModuleBindKind.LOCAL, child_may_edit=True)

    with patch(
        "prodavan.application.projects.project_runtime_module_service.ModuleBindingService"
    ) as bind_cls:
        bind_cls.return_value.get_project_binding = AsyncMock(return_value=binding)
        out = await svc._sot_for_project(
            project_id="proj_1", module_id="mod_equipment", write=True
        )

    assert out is leaf
    svc._instances.ensure_project_instance.assert_awaited_once_with(
        project_id="proj_1", module_id="mod_equipment"
    )


@pytest.mark.asyncio
async def test_sot_for_project_global_locked_forbids_write() -> None:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    session = AsyncMock()
    svc = ProjectRuntimeModuleService.__new__(ProjectRuntimeModuleService)
    svc._session = session
    svc._instances = MagicMock()
    binding = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=False)

    with (
        patch(
            "prodavan.application.projects.project_runtime_module_service.ModuleBindingService"
        ) as bind_cls,
        pytest.raises(AppError) as ei,
    ):
        bind_cls.return_value.get_project_binding = AsyncMock(return_value=binding)
        await svc._sot_for_project(project_id="proj_1", module_id="mod_prompts", write=True)

    assert ei.value.status == 403
    assert "read-only" in (ei.value.detail or "")


@pytest.mark.asyncio
async def test_sot_for_project_global_unlocked_resolves_parent() -> None:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    session = AsyncMock()
    svc = ProjectRuntimeModuleService.__new__(ProjectRuntimeModuleService)
    svc._session = session
    svc._instances = MagicMock()
    parent = SimpleNamespace(id="minst_cab")
    svc._instances.resolve_sot_instance = AsyncMock(return_value=parent)
    binding = SimpleNamespace(bind_kind=ModuleBindKind.GLOBAL, child_may_edit=True)

    with patch(
        "prodavan.application.projects.project_runtime_module_service.ModuleBindingService"
    ) as bind_cls:
        bind_cls.return_value.get_project_binding = AsyncMock(return_value=binding)
        out = await svc._sot_for_project(
            project_id="proj_1", module_id="mod_prompts", write=True
        )

    assert out is parent
    svc._instances.resolve_sot_instance.assert_awaited_once()
