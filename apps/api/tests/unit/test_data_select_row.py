"""Unit tests — data.select_row action."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.modules.module_action_executor import ModuleActionExecutor


@pytest.mark.asyncio
async def test_select_row_clears_siblings_and_sets_parent() -> None:
    session = AsyncMock()
    executor = ModuleActionExecutor(session)
    modules = AsyncMock()
    executor._modules = modules

    modules.list_data_rows = AsyncMock(
        side_effect=[
            [
                {
                    "row_id": "o1",
                    "body": {"line_id": "L1", "is_selected": True, "title": "a"},
                },
                {
                    "row_id": "o2",
                    "body": {"line_id": "L1", "is_selected": False, "title": "b"},
                },
                {
                    "row_id": "o3",
                    "body": {"line_id": "L2", "is_selected": True, "title": "c"},
                },
            ],
            [
                {"row_id": "L1", "body": {"title": "line", "selected_offer_id": "o1"}},
            ],
        ]
    )
    modules.update_data_row = AsyncMock(return_value={})

    out = await executor._select_row(
        cabinet_id="cab",
        module_id="mod_equipment",
        params={
            "table_slug": "found_offers",
            "select_field": "is_selected",
            "group_by": "line_id",
            "parent": {
                "table_slug": "request_lines",
                "id_from": "line_id",
                "set_field": "selected_offer_id",
            },
        },
        row_id="o2",
        principal=SimpleNamespace(sub="u"),
        employee=None,
    )
    assert out["row_id"] == "o2"
    assert modules.update_data_row.await_count >= 2
    # parent updated
    parent_calls = [
        c
        for c in modules.update_data_row.await_args_list
        if c.kwargs.get("table_slug") == "request_lines"
    ]
    assert parent_calls
    assert parent_calls[0].kwargs["body"]["selected_offer_id"] == "o2"
