"""Owner module data delete stays on the caller's instance (no child cascade)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.modules.module_instance_service import (
    OWNER_COMPANY,
    OWNER_PLATFORM,
    PLATFORM_OWNER_ID,
)
from prodavan.application.modules.owner_module_data_service import OwnerModuleDataService
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_platform_delete_targets_platform_instance_only() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    svc = OwnerModuleDataService(session)
    platform = SimpleNamespace(id="minst_platform")

    with (
        patch.object(svc._instances, "ensure_platform_instance", AsyncMock(return_value=platform)) as ensure,
        patch.object(svc._instances, "delete_data_row", AsyncMock(return_value=True)) as delete,
        patch.object(svc._instances, "ensure_company_instance", AsyncMock()) as ensure_company,
    ):
        out = await svc.delete_data_row(
            owner_kind=OWNER_PLATFORM,
            owner_id=PLATFORM_OWNER_ID,
            module_id="mod_1",
            table_slug="mcp_packages",
            row_id="row_a",
        )

    assert out["deleted"] is True
    assert out["instance_id"] == "minst_platform"
    ensure.assert_awaited_once_with(module_id="mod_1")
    delete.assert_awaited_once_with(
        instance_id="minst_platform",
        table_slug="mcp_packages",
        row_id="row_a",
    )
    ensure_company.assert_not_awaited()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_company_delete_targets_company_instance_only() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    svc = OwnerModuleDataService(session)
    company = SimpleNamespace(id="minst_company")

    with (
        patch.object(svc._instances, "ensure_company_instance", AsyncMock(return_value=company)) as ensure,
        patch.object(svc._instances, "delete_data_row", AsyncMock(return_value=True)) as delete,
        patch.object(svc._instances, "ensure_platform_instance", AsyncMock()) as ensure_platform,
    ):
        out = await svc.delete_data_row(
            owner_kind=OWNER_COMPANY,
            owner_id="co_1",
            module_id="mod_1",
            table_slug="equipment_dbs",
            row_id="row_b",
        )

    assert out["instance_id"] == "minst_company"
    assert out["owner_id"] == "co_1"
    ensure.assert_awaited_once_with(company_id="co_1", module_id="mod_1")
    delete.assert_awaited_once_with(
        instance_id="minst_company",
        table_slug="equipment_dbs",
        row_id="row_b",
    )
    ensure_platform.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_missing_row_raises_not_found() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    svc = OwnerModuleDataService(session)
    platform = SimpleNamespace(id="minst_platform")

    with (
        patch.object(svc._instances, "ensure_platform_instance", AsyncMock(return_value=platform)),
        patch.object(svc._instances, "delete_data_row", AsyncMock(return_value=False)),
    ):
        with pytest.raises(AppError) as ei:
            await svc.delete_data_row(
                owner_kind=OWNER_PLATFORM,
                owner_id=PLATFORM_OWNER_ID,
                module_id="mod_1",
                table_slug="mcp_packages",
                row_id="missing",
            )

    assert ei.value.status == 404
    session.commit.assert_not_awaited()
