"""Indexer regression: src_hash in docs + suppliers auto-fill with dedup."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import prodavan.application.modules.equipment_catalog_opensearch as osx


def test_source_hash_stable_and_normalized() -> None:
    a = osx.source_hash("OCS", "SSD Samsung 990")
    b = osx.source_hash(" ocs ", "  ssd samsung 990  ")
    c = osx.source_hash("OCS", "Other")
    assert a == b  # case/whitespace-insensitive identity
    assert a != c
    assert len(a) == 16


def test_doc_contains_src_hash() -> None:
    doc = osx._doc_from_mapped(
        mapped={"title": "SSD", "price": "100", "supplier": "OCS"},
        catalog_id="cat_1",
        catalog_name="OCS base",
        company_id="co_1",
        project_id=None,
        cabinet_id=None,
    )
    assert doc["src_hash"] == osx.source_hash("OCS", "SSD")
    assert doc["supplier"] == "OCS"
    assert doc["price_num"] == 100.0


@pytest.mark.asyncio
async def test_sync_sellers_from_catalog_dedups(monkeypatch) -> None:
    sellers: dict[str, dict[str, Any]] = {
        # existing: OCS with alias "OCS-Soft"
        "sell_x": {"name": "OCS", "aliases": "OCS-Soft", "is_enabled": True},
    }
    created: list[tuple[str, dict]] = []

    async def _list(*, instance_id: str, table_slug: str, **_: Any) -> list[dict]:
        if table_slug == "trusted_sellers":
            return [
                {"row_id": rid, "body": body, "session_id": None} for rid, body in sellers.items()
            ]
        return []

    async def _upsert(*, instance_id: str, table_slug: str, row_id: str, body: dict, **_: Any) -> dict:
        created.append((row_id, body))
        sellers[row_id] = body
        return {"row_id": row_id, "body": body}

    session = MagicMock()
    session.commit = AsyncMock()
    svc = MagicMock()
    svc.list_data_rows = AsyncMock(side_effect=_list)
    svc.upsert_data_row = AsyncMock(side_effect=_upsert)
    monkeypatch.setattr(osx, "ModuleInstanceService", lambda _: svc)

    n = await osx._sync_sellers_from_catalog(
        session, instance_id="inst_1", suppliers={"OCS", "OCS-Soft", "ДНС", ""}
    )
    # OCS known by name; OCS-Soft known by alias; "" skipped; ДНС new
    assert n == 1
    assert created[0][1]["name"] == "ДНС"
    assert created[0][0].startswith("sell_")

    # idempotent: second run adds nothing
    n2 = await osx._sync_sellers_from_catalog(
        session, instance_id="inst_1", suppliers={"OCS", "OCS-Soft", "ДНС"}
    )
    assert n2 == 0
