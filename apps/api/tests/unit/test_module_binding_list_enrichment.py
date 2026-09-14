"""Unit — enriched module project/cabinet list helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.module_binding_service import ModuleBindingService


def _result(rows: list) -> MagicMock:
    q = MagicMock()
    q.all.return_value = rows
    q.scalars.return_value.all.return_value = [r[0] if isinstance(r, tuple) else r for r in rows]
    return q


@pytest.mark.asyncio
async def test_list_project_bindings_for_module_filters_company() -> None:
    session = AsyncMock()
    bind = SimpleNamespace(
        project_id="prj_1",
        bind_kind="local",
        child_may_edit=True,
    )
    # Main join result, then usage + messages aggregations.
    session.execute = AsyncMock(
        side_effect=[
            _result([(bind, "Alpha", "co_own", "cab_1", "Acme")]),
            _result([("prj_1", 10, 5)]),
            _result([("prj_1", 3)]),
        ]
    )

    with patch(
        "prodavan.application.metrics.overview_merge.overlay_store_counters",
        new=AsyncMock(
            side_effect=lambda metrics, **_: {
                **metrics,
                "agent_tokens_used": metrics.get("agent_tokens_used", 0),
                "agent_requests": metrics.get("agent_requests", 0),
            }
        ),
    ):
        svc = ModuleBindingService(session)
        items = await svc.list_project_bindings_for_module("mod_1", company_id="co_own")

    assert len(items) == 1
    assert items[0]["project_id"] == "prj_1"
    assert items[0]["company_id"] == "co_own"
    assert items[0]["company_name"] == "Acme"
    assert items[0]["cabinet_id"] == "cab_1"
    assert items[0]["bind_kind"] == "local"
    assert items[0]["agent_tokens_used"] == 15
    assert items[0]["agent_requests"] == 3
    # company filter must be part of the first SQL statement
    first_stmt = session.execute.await_args_list[0].args[0]
    assert "company_id" in str(first_stmt)


@pytest.mark.asyncio
async def test_list_cabinets_catalog_for_module_company_scope() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result(["cab_bound"]),  # list_cabinet_ids
            _result(
                [
                    ("cab_1", "Cab One", "co_x", "Acme", 2),
                    ("cab_bound", "Bound Cab", "co_x", "Acme", 1),
                ]
            ),
        ]
    )
    svc = ModuleBindingService(session)
    items = await svc.list_cabinets_catalog_for_module("mod_1", company_id="co_x")
    assert len(items) == 2
    assert items[0]["cabinet_id"] == "cab_1"
    assert items[0]["bound"] is False
    assert items[0]["modules_count"] == 2
    assert items[1]["bound"] is True
    first_stmt = session.execute.await_args_list[1].args[0]
    assert "owner_company_id" in str(first_stmt)
