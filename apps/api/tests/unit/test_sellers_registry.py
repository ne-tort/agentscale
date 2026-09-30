"""Suppliers registry regression tests (wave: «Поставщики»).

- budget_sync auto-maps suppliers from offers (dedup by name/alias, no session
  stamp — shared table) and initializes budget markup from the supplier's
  ``margin_pct`` (manual budget edits are never overwritten);
- unique name/alias validation on trusted_sellers rows;
- catalog search supplier sets (disabled/priority with aliases).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.domain.errors import AppError


def _executor() -> ModuleActionExecutor:
    return ModuleActionExecutor(MagicMock())


def _invoke(executor: ModuleActionExecutor, monkeypatch, rows_by_table: dict) -> Any:
    async def _rows(*, table_slug: str, **_: Any) -> list[dict]:
        return rows_by_table.get(table_slug, [])

    monkeypatch.setattr(executor, "_list_rows_for_scope", AsyncMock(side_effect=_rows))
    created: list[dict] = []

    async def _create(*, table_slug: str, body: dict, **_: Any) -> dict:
        created.append({"table_slug": table_slug, "body": body})
        return {"row_id": f"row_new_{len(created)}", "body": body}

    monkeypatch.setattr(executor._modules, "create_data_row", AsyncMock(side_effect=_create))
    return created


@pytest.mark.asyncio
async def test_budget_sync_maps_sellers_and_init_markup(monkeypatch) -> None:
    """Offers' suppliers land in trusted_sellers; margin_pct seeds markup."""
    executor = _executor()
    monkeypatch.setattr(
        executor,
        "_load_action",
        AsyncMock(
            return_value={
                "id": "budget_sync_lines",
                "kind": "equipment.budget_sync",
                "params": {},
            }
        ),
    )
    rows_by_table: dict[str, list[dict]] = {
        "request_lines": [
            {"row_id": "line_1", "body": {"title": "SSD", "qty": 1, "part_number": "MZ"}},
            {"row_id": "line_2", "body": {"title": "RAM", "qty": 2}},
        ],
        "found_offers": [
            {
                "row_id": "off_1",
                "body": {
                    "title": "SSD Samsung",
                    "price": 100,
                    "seller": "OCS",
                    "line_id": "line_1",
                    "is_selected": True,
                },
            },
            {
                "row_id": "off_2",
                "body": {
                    "title": "RAM Kingston",
                    "price": 50,
                    "seller": "DNS-Store",
                    "line_id": "line_2",
                    "is_selected": True,
                },
            },
        ],
        "budget_lines": [],
        "catalogs": [],
        # ДНС is a known supplier with alias DNS-Store and margin 25%
        "trusted_sellers": [
            {
                "row_id": "sell_1",
                "body": {"name": "ДНС", "aliases": "DNS, DNS-Store", "margin_pct": 0.25},
            }
        ],
    }
    created = _invoke(executor, monkeypatch, rows_by_table)

    out = await executor.invoke(
        action_id="budget_sync_lines",
        cabinet_id="cab_1",
        module_id="mod_equipment",
        principal=MagicMock(),
        employee=None,
    )
    assert out["created"] == 2
    assert out["sellers_created"] == 1  # OCS only — DNS-Store matched an alias

    seller_creates = [c for c in created if c["table_slug"] == "trusted_sellers"]
    assert [c["body"]["name"] for c in seller_creates] == ["OCS"]
    assert seller_creates[0]["body"]["is_enabled"] is True

    budget_creates = [c for c in created if c["table_slug"] == "budget_lines"]
    by_line = {b["body"]["line_id"]: b["body"] for b in budget_creates}
    # Known supplier with margin_pct=0.25 -> markup seeded from the registry
    assert by_line["line_2"]["markup"] == 0.25
    assert by_line["line_2"]["seller"] == "DNS-Store"
    # Unknown supplier -> default markup
    assert by_line["line_1"]["markup"] == 0.1
    assert by_line["line_1"]["seller"] == "OCS"


@pytest.mark.asyncio
async def test_budget_sync_never_overwrites_manual_markup(monkeypatch) -> None:
    """Update path keeps user-edited markup/vat (snapshot fields only)."""
    executor = _executor()
    monkeypatch.setattr(
        executor,
        "_load_action",
        AsyncMock(
            return_value={
                "id": "budget_sync_lines",
                "kind": "equipment.budget_sync",
                "params": {},
            }
        ),
    )
    rows_by_table: dict[str, list[dict]] = {
        "request_lines": [{"row_id": "line_1", "body": {"title": "SSD", "qty": 1}}],
        "found_offers": [
            {
                "row_id": "off_1",
                "body": {"title": "SSD", "price": 10, "seller": "OCS", "line_id": "line_1", "is_selected": True},
            }
        ],
        "budget_lines": [
            {
                "row_id": "b1",
                "body": {
                    "line_id": "line_1",
                    "title": "SSD",
                    "qty": 1,
                    "price_in": 10,
                    "vat": 0.2,
                    "markup": 0.55,  # user override
                    "seller": "OCS",
                },
            }
        ],
        "catalogs": [],
        "trusted_sellers": [
            {"row_id": "s1", "body": {"name": "OCS", "margin_pct": 0.25}},
        ],
    }
    updates: list[dict] = []

    async def _rows(*, table_slug: str, **_: Any) -> list[dict]:
        return rows_by_table.get(table_slug, [])

    async def _create(*, table_slug: str, body: dict, **_: Any) -> dict:
        return {"row_id": "row_new", "body": body}

    async def _update(**kw: Any) -> dict:
        updates.append(kw)
        return {"row_id": kw["row_id"], "body": kw["body"]}

    monkeypatch.setattr(executor, "_list_rows_for_scope", AsyncMock(side_effect=_rows))
    monkeypatch.setattr(executor._modules, "create_data_row", AsyncMock(side_effect=_create))
    monkeypatch.setattr(executor, "_update_row_for_scope", AsyncMock(side_effect=_update))

    out = await executor.invoke(
        action_id="budget_sync_lines",
        cabinet_id="cab_1",
        module_id="mod_equipment",
        principal=MagicMock(),
        employee=None,
    )
    assert out["updated"] == 1
    assert out["sellers_created"] == 0
    body = updates[0]["body"]
    assert body["markup"] == 0.55  # manual value kept
    assert body["vat"] == 0.2


@pytest.mark.asyncio
async def test_unique_columns_reject_duplicate_name_and_alias() -> None:
    """trusted_sellers name/alias uniqueness (case-insensitive, token-wise)."""
    svc = ModuleInstanceService(MagicMock())
    svc.resolve_columns_body = AsyncMock(
        return_value=[
            {"table_slug": "trusted_sellers", "name": "name", "unique": True},
            {"table_slug": "trusted_sellers", "name": "aliases", "unique": True},
        ]
    )
    existing = [
        SimpleNamespace(
            table_slug="trusted_sellers",
            row_id="row_1",
            body={"name": "OCS", "aliases": "OCS-Soft, OCS-M"},
        ),
        SimpleNamespace(table_slug="budget_lines", row_id="row_9", body={}),
    ]
    svc.list_all_data_rows = AsyncMock(return_value=existing)
    svc._session = MagicMock()
    svc._session.flush = AsyncMock()
    svc._session.add = MagicMock()
    inst_row = SimpleNamespace(module_id="mod_equipment")
    svc._session.get = AsyncMock(return_value=inst_row)
    none_result = MagicMock()
    none_result.scalar_one_or_none.return_value = None
    svc._session.execute = AsyncMock(return_value=none_result)

    # duplicate name (case-insensitive)
    with pytest.raises(AppError) as ei:
        await svc.upsert_data_row(
            instance_id="i1",
            table_slug="trusted_sellers",
            row_id="row_2",
            body={"name": "ocs"},
            created_by="emp_1",
        )
    assert "duplicate" in str(ei.value.detail)

    # duplicate via alias token
    with pytest.raises(AppError):
        await svc.upsert_data_row(
            instance_id="i1",
            table_slug="trusted_sellers",
            row_id="row_3",
            body={"name": "Another", "aliases": "OCS-Soft"},
            created_by="emp_1",
        )

    # updating the same row (self) is allowed
    await svc.upsert_data_row(
        instance_id="i1",
        table_slug="trusted_sellers",
        row_id="row_1",
        body={"name": "OCS", "aliases": "OCS-Soft, OCS-M", "email": "x@y.z"},
        created_by="emp_1",
    )

    # seed provenance bypasses the check
    await svc.upsert_data_row(
        instance_id="i1",
        table_slug="trusted_sellers",
        row_id="row_4",
        body={"name": "OCS"},
        created_by="module_seed",
    )


def test_supplier_sets_with_aliases() -> None:
    from prodavan.application.tenant_infra.equipment_catalog_search_service import (
        _supplier_sets,
    )

    rows = [
        {
            "row_id": "s1",
            "body": {"name": "ДНС", "aliases": "DNS, DNS-Store", "is_enabled": False},
        },
        {
            "row_id": "s2",
            "body": {
                "name": "OCS",
                "aliases": ["OCS-Soft"],
                "priority_purchase": True,
            },
        },
        {"row_id": "s3", "body": {"name": "Neutral"}},
    ]
    disabled, priority = _supplier_sets(rows)
    assert disabled == {"днс", "dns", "dns-store"}
    assert priority == {"ocs", "ocs-soft"}
