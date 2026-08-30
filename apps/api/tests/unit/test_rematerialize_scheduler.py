"""Unit tests — cabinet rematerialize scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.projects.rematerialize_scheduler import (
    module_has_materialize_rules,
    schedule_cabinet_binding_change_rematerialize,
    schedule_cabinet_rematerialize,
)


@pytest.mark.asyncio
async def test_module_has_materialize_rules_product_modules() -> None:
    session = AsyncMock()
    assert await module_has_materialize_rules(session, module_id="mod_files") is True
    assert await module_has_materialize_rules(session, module_id="mod_mcp") is True


@pytest.mark.asyncio
async def test_schedule_cabinet_rematerialize_enqueues_projects() -> None:
    session = AsyncMock()
    with (
        patch(
            "prodavan.application.projects.rematerialize_scheduler.ProjectQuery"
        ) as query_cls,
        patch(
            "prodavan.application.projects.rematerialize_scheduler.request_rematerialize_project",
            AsyncMock(return_value={"enqueued": True}),
        ) as request,
    ):
        query_cls.return_value.list_ids = AsyncMock(return_value=["proj_a", "proj_b"])

        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_files"
        )

    assert out["scheduled"] == 2
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_schedule_skips_modules_without_materialize() -> None:
    session = AsyncMock()
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.module_has_materialize_rules",
        AsyncMock(return_value=False),
    ):
        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_other"
        )
    assert out["skipped"] is True
    assert out["scheduled"] == 0


@pytest.mark.asyncio
async def test_schedule_binding_change_rematerializes_all_projects() -> None:
    session = AsyncMock()
    with (
        patch(
            "prodavan.application.projects.rematerialize_scheduler.ProjectQuery"
        ) as query_cls,
        patch(
            "prodavan.application.projects.rematerialize_scheduler.request_rematerialize_project",
            AsyncMock(return_value={"enqueued": True}),
        ) as request,
    ):
        query_cls.return_value.list_ids = AsyncMock(return_value=["proj_a"])
        out = await schedule_cabinet_binding_change_rematerialize(
            session, cabinet_id="cab_1"
        )

    assert out["scheduled"] == 1
    request.assert_awaited_once()
    assert request.await_args.kwargs["source"] == "cabinet_binding"


@pytest.mark.asyncio
async def test_schedule_binding_change_via_bus_when_enabled() -> None:
    session = AsyncMock()
    with (
        patch(
            "prodavan.application.projects.rematerialize_scheduler.ProjectQuery"
        ) as query_cls,
        patch(
            "prodavan.application.projects.rematerialize_scheduler.request_rematerialize_project",
            AsyncMock(return_value={"enqueued": True, "via_bus": True, "project_id": "proj_a"}),
        ) as request,
    ):
        query_cls.return_value.list_ids = AsyncMock(return_value=["proj_a"])
        out = await schedule_cabinet_binding_change_rematerialize(
            session, cabinet_id="cab_1"
        )

    assert out["scheduled"] == 1
    assert out["enqueued"] == ["proj_a"]
    request.assert_awaited_once_with(
        "proj_a",
        cabinet_id="cab_1",
        module_id=None,
        source="cabinet_binding",
    )
