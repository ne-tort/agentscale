"""Chat scope on the CABINET contour (wave 9).

The user saw global equipment data because CabinetModuleService ignored
chat scope: list returned rows from every chat, writes left NULL sessions,
and the budget sync ran unscoped. These tests pin the new contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService


def _svc(inst: SimpleNamespace) -> CabinetModuleService:
    session = AsyncMock()
    svc = CabinetModuleService.__new__(CabinetModuleService)
    svc._session = session
    svc._access = MagicMock()
    svc._access.require_access = AsyncMock()
    svc._instances = MagicMock()
    svc._instances.ensure_cabinet_instance = AsyncMock(return_value=inst)
    svc._instances.sot_may_edit = AsyncMock(return_value=True)
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    svc._require_module_binding = AsyncMock()
    svc._schedule_rematerialize = AsyncMock(return_value={})
    svc._maybe_run_row_actions = AsyncMock()
    return svc


def _tables_body(chats: str) -> list:
    return [{"slug": "request_lines", "scope": {"chats": chats}}]


@pytest.mark.asyncio
async def test_list_chats_current_filters_by_active_session() -> None:
    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("current"))
    svc._instances.list_data_rows = AsyncMock(return_value=[])

    await svc.list_data_rows(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=MagicMock(),
        employee=None,
        session_id="ags_123",
    )
    svc._instances.list_data_rows.assert_awaited_once()
    kwargs = svc._instances.list_data_rows.await_args.kwargs
    assert kwargs["session_id"] == "ags_123"


@pytest.mark.asyncio
async def test_list_chats_current_without_session_uses_synthetic_main() -> None:
    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("current"))
    svc._instances.list_data_rows = AsyncMock(return_value=[])

    await svc.list_data_rows(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=MagicMock(),
        employee=None,
        session_id=None,
    )
    kwargs = svc._instances.list_data_rows.await_args.kwargs
    assert kwargs["session_id"] == "main"


@pytest.mark.asyncio
async def test_list_chats_all_no_filter() -> None:
    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("all"))
    svc._instances.list_data_rows = AsyncMock(return_value=[])

    await svc.list_data_rows(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=MagicMock(),
        employee=None,
        session_id="ags_123",
    )
    kwargs = svc._instances.list_data_rows.await_args.kwargs
    assert kwargs.get("session_id") is None


@pytest.mark.asyncio
async def test_create_chats_current_stamps_session() -> None:
    from unittest.mock import patch

    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("current"))
    created = {"row_id": "row_1", "table_slug": "request_lines", "body": {"t": 1}}
    svc._instances.create_data_row = AsyncMock(return_value=created)
    svc._instances.get_data_row = AsyncMock(return_value=created)

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
            module_id="mod_equipment",
            table_slug="request_lines",
            body={"t": 1},
            principal=SimpleNamespace(sub="u1"),
            employee=None,
            session_id="ags_123",
        )
    kwargs = svc._instances.create_data_row.await_args.kwargs
    assert kwargs["session_id"] == "ags_123"
    assert kwargs["body"]["session_id"] == "ags_123"
    assert out["row_id"] == "row_1"


@pytest.mark.asyncio
async def test_update_chats_current_rejects_foreign_session_row() -> None:
    from prodavan.domain.errors import AppError

    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("current"))
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_1", "session_id": "ags_other", "body": {}}
    )
    with pytest.raises(AppError) as exc:
        await svc.update_data_row(
            cabinet_id="cab_1",
            module_id="mod_equipment",
            table_slug="request_lines",
            row_id="row_1",
            body={"t": 2},
            principal=MagicMock(),
            employee=None,
            session_id="ags_123",
        )
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_delete_chats_current_rejects_foreign_session_row() -> None:
    from prodavan.domain.errors import AppError

    inst = SimpleNamespace(id="minst_cab")
    svc = _svc(inst)
    svc._instances.resolve_tables_body = AsyncMock(return_value=_tables_body("current"))
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_1", "session_id": "main", "body": {}}
    )
    svc._instances.delete_data_row = AsyncMock(return_value=True)
    with pytest.raises(AppError) as exc:
        await svc.delete_data_row(
            cabinet_id="cab_1",
            module_id="mod_equipment",
            table_slug="request_lines",
            row_id="row_1",
            principal=MagicMock(),
            employee=None,
            session_id="ags_123",
        )
    assert exc.value.status == 404
    svc._instances.delete_data_row.assert_not_awaited()
