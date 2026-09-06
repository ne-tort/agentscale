"""Unit tests for module instance copy-on-bind + cutover SoT contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from prodavan.application.modules.module_instance_service import (
    OWNER_CABINET,
    OWNER_PROJECT,
    ModuleInstanceService,
    row_applies_to_project,
)
from prodavan.application.modules.module_materialize_service import ModuleMaterializeService


def test_row_applies_to_project_empty_means_all() -> None:
    assert row_applies_to_project({}, "prj_1")
    assert row_applies_to_project({"project_ids": []}, "prj_1")
    assert row_applies_to_project({"project_ids": ["prj_1"]}, "prj_1")
    assert not row_applies_to_project({"project_ids": ["prj_other"]}, "prj_1")


@pytest.mark.asyncio
async def test_fork_instance_returns_existing_without_recopy() -> None:
    session = AsyncMock()
    svc = ModuleInstanceService(session)
    parent = SimpleNamespace(id="minst_parent", module_id="mod_1")
    existing = SimpleNamespace(id="minst_child", module_id="mod_1")

    with (
        patch.object(svc, "get_instance", AsyncMock(return_value=existing)) as get,
        patch.object(svc, "_copy_instance_meta", AsyncMock()) as copy_meta,
        patch.object(svc, "_copy_instance_data", AsyncMock()) as copy_data,
    ):
        out = await svc.fork_instance(
            parent=parent, owner_kind=OWNER_PROJECT, owner_id="prj_1"
        )

    assert out is existing
    get.assert_awaited_once()
    copy_meta.assert_not_awaited()
    copy_data.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_fork_instance_copies_meta_and_data_when_missing() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = ModuleInstanceService(session)
    parent = SimpleNamespace(id="minst_parent", module_id="mod_1")

    with (
        patch.object(svc, "get_instance", AsyncMock(return_value=None)),
        patch.object(svc, "_copy_instance_meta", AsyncMock()) as copy_meta,
        patch.object(svc, "_copy_instance_data", AsyncMock()) as copy_data,
    ):
        child = await svc.fork_instance(
            parent=parent,
            owner_kind=OWNER_CABINET,
            owner_id="cab_1",
            project_id_filter=None,
        )

    assert child.owner_kind == OWNER_CABINET
    assert child.owner_id == "cab_1"
    assert child.parent_instance_id == "minst_parent"
    assert child.module_id == "mod_1"
    session.add.assert_called_once()
    copy_meta.assert_awaited_once_with(src_id="minst_parent", dst_id=child.id)
    copy_data.assert_awaited_once_with(
        src_id="minst_parent", dst_id=child.id, project_id_filter=None
    )


@pytest.mark.asyncio
async def test_fork_isolates_mutations_via_deepcopy_on_copy() -> None:
    """Child data body must not share dict identity with parent row body."""
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = ModuleInstanceService(session)
    shared_body = {"name": "A", "project_ids": ["prj_1"]}
    parent_row = SimpleNamespace(
        table_slug="prompt_profiles",
        row_id="prof_1",
        body=shared_body,
        created_by="seed",
    )

    upsert = AsyncMock()
    with (
        patch.object(svc, "list_all_data_rows", AsyncMock(return_value=[parent_row])),
        patch.object(svc, "upsert_data_row", upsert),
    ):
        await svc._copy_instance_data(
            src_id="minst_parent",
            dst_id="minst_child",
            project_id_filter="prj_1",
        )

    upsert.assert_awaited_once()
    kwargs = upsert.await_args.kwargs
    assert kwargs["instance_id"] == "minst_child"
    assert kwargs["body"]["name"] == "A"
    assert kwargs["body"]["project_ids"] == []
    assert kwargs["body"] is not shared_body
    shared_body["name"] = "MUTATED"
    assert kwargs["body"]["name"] == "A"


@pytest.mark.asyncio
async def test_delete_cabinet_module_instances_cascades_project_leaves() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    svc = ModuleInstanceService(session)

    cab = SimpleNamespace(id="minst_cab")
    prj = SimpleNamespace(id="minst_prj")
    projects_q = MagicMock()
    projects_q.all.return_value = [("prj_1",), ("prj_2",)]

    async def get_instance(*, owner_kind: str, owner_id: str, module_id: str):
        if owner_kind == OWNER_CABINET:
            return cab
        if owner_kind == OWNER_PROJECT and owner_id == "prj_1":
            return prj
        return None

    session.execute = AsyncMock(return_value=projects_q)

    with patch.object(svc, "get_instance", side_effect=get_instance):
        deleted = await svc.delete_cabinet_module_instances(
            cabinet_id="cab_1", module_id="mod_1"
        )

    assert deleted == 2
    assert session.delete.call_count == 2
    session.delete.assert_has_calls([call(prj), call(cab)], any_order=False)


@pytest.mark.asyncio
async def test_uninstall_deletes_module_instances() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(schema_name="cab_inst_x"))
    session.execute = AsyncMock()
    svc = ModuleMaterializeService.__new__(ModuleMaterializeService)
    svc._session = session

    with patch(
        "prodavan.application.modules.module_instance_service.ModuleInstanceService"
    ) as cls:
        delete = AsyncMock(return_value=3)
        cls.return_value.delete_cabinet_module_instances = delete
        await svc.uninstall(cabinet_id="cab_1", module_id="mod_1")
        delete.assert_awaited_once_with(cabinet_id="cab_1", module_id="mod_1")


@pytest.mark.asyncio
async def test_set_profile_writes_project_leaf_not_cabinet() -> None:
    from prodavan.application.project_service.module_settings import ProjectModuleSettingsService

    session = AsyncMock()
    session.flush = AsyncMock()
    svc = ProjectModuleSettingsService.__new__(ProjectModuleSettingsService)
    svc._session = session
    svc._instances = MagicMock()
    leaf = SimpleNamespace(id="minst_leaf")
    svc._instances.ensure_project_instance = AsyncMock(return_value=leaf)
    svc._instances.upsert_data_row = AsyncMock()

    profiles = [
        {"row_id": "p1", "body": {"name": "One", "is_default": True, "project_ids": ["x"]}},
        {"row_id": "p2", "body": {"name": "Two", "is_default": False}},
    ]
    await svc._apply_profile_on_instance(
        project_id="prj_1",
        module_id="mod_prompts",
        profile_table="prompt_profiles",
        profile_id="p2",
        profiles=profiles,
    )

    svc._instances.ensure_project_instance.assert_awaited_once_with(
        project_id="prj_1", module_id="mod_prompts"
    )
    assert svc._instances.upsert_data_row.await_count == 2
    bodies = [c.kwargs["body"] for c in svc._instances.upsert_data_row.await_args_list]
    assert bodies[0]["is_default"] is False
    assert bodies[0]["project_ids"] == []
    assert bodies[1]["is_default"] is True
    for c in svc._instances.upsert_data_row.await_args_list:
        assert c.kwargs["instance_id"] == "minst_leaf"


@pytest.mark.asyncio
async def test_cabinet_write_targets_cabinet_instance() -> None:
    from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService

    session = AsyncMock()
    session.commit = AsyncMock()
    svc = CabinetModuleService.__new__(CabinetModuleService)
    svc._session = session
    svc._access = MagicMock()
    svc._access.require_access = AsyncMock()
    svc._instances = MagicMock()
    cab_inst = SimpleNamespace(id="minst_cab")
    svc._instances.ensure_cabinet_instance = AsyncMock(return_value=cab_inst)
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    created = {"row_id": "row_1", "table_slug": "notes", "body": {"t": 1}}
    svc._instances.create_data_row = AsyncMock(return_value=created)
    svc._instances.get_data_row = AsyncMock(return_value=created)
    svc._require_module_binding = AsyncMock()
    svc._schedule_rematerialize = AsyncMock(return_value={})
    svc._maybe_run_row_actions = AsyncMock()

    principal = SimpleNamespace(sub="u1")
    with (
        patch(
            "prodavan.application.cabinets.cabinet_module_service.merge_column_defaults",
            side_effect=lambda **kw: kw["body"],
        ),
        patch(
            "prodavan.application.cabinets.cabinet_module_service.validate_row_with_columns",
            side_effect=lambda **kw: kw["body"],
        ),
    ):
        out = await svc.create_data_row(
            cabinet_id="cab_1",
            module_id="mod_1",
            table_slug="notes",
            body={"t": 1},
            principal=principal,
            employee=None,
        )

    svc._instances.ensure_cabinet_instance.assert_awaited_once_with(
        cabinet_id="cab_1", module_id="mod_1"
    )
    svc._instances.create_data_row.assert_awaited_once()
    assert svc._instances.create_data_row.await_args.kwargs["instance_id"] == "minst_cab"
    assert out["instance_id"] == "minst_cab"


def test_upsert_skips_platform_refresh_when_instances_table_missing() -> None:
    from prodavan.application.platform import product_module_upsert as upsert_mod

    conn = MagicMock()
    with (
        patch.object(upsert_mod, "PRODUCT_MODULES", [("mod_x", "X", {"tabs": []})]),
        patch.object(upsert_mod, "_upsert_one") as one,
        patch.object(upsert_mod, "_has_table", return_value=False) as has,
        patch.object(upsert_mod, "_refresh_platform_instance_meta") as refresh,
    ):
        n = upsert_mod.upsert_product_modules(conn)

    assert n == 1
    one.assert_called_once()
    has.assert_called_once_with(conn, "module_instances")
    refresh.assert_not_called()


def test_upsert_refreshes_platform_meta_when_instances_exist() -> None:
    from prodavan.application.platform import product_module_upsert as upsert_mod

    conn = MagicMock()
    with (
        patch.object(upsert_mod, "PRODUCT_MODULES", [("mod_x", "X", {"tabs": []})]),
        patch.object(upsert_mod, "_upsert_one"),
        patch.object(upsert_mod, "_has_table", return_value=True),
        patch.object(upsert_mod, "_refresh_platform_instance_meta") as refresh,
    ):
        upsert_mod.upsert_product_modules(conn)

    refresh.assert_called_once_with(conn, module_id="mod_x", slugs={"tabs": []})
