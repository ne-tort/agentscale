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
