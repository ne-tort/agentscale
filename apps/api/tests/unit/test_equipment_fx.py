"""FX + offers_refresh + best-offer budget regression tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import prodavan.application.modules.equipment_fx as fx
from prodavan.application.modules.module_action_executor import ModuleActionExecutor


# ---------------------------------------------------------------- FX service
def test_parse_cbr_xml() -> None:
    xml = """<ValCurs><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>92,5500</Value></Valute>
    <Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>101,2300</Value></Valute></ValCurs>"""
    rates = fx._parse_cbr_xml(xml)
    assert rates == {"USD": 92.55, "EUR": 101.23}


@pytest.mark.asyncio
async def test_get_rates_fallback_on_error(monkeypatch) -> None:
    async def _fail() -> Any:
        raise RuntimeError("network down")

    monkeypatch.setattr(fx.httpx.AsyncClient, "get", _fail)
    fx._cache.update({"ts": 0.0, "rates": {}})
    rates = await fx.get_rates()
    assert rates["USD"] == fx._FALLBACK_RATES["USD"]


@pytest.mark.asyncio
async def test_apply_fx_rub_passthrough() -> None:
    body = {"title": "SSD", "price": 9000, "currency": "RUB"}
    out = await fx.apply_fx_to_offer_body(body)
    assert out["price"] == 9000
    assert out["price_orig"] == 9000
    assert out["currency"] == "RUB"


@pytest.mark.asyncio
async def test_apply_fx_usd_conversion(monkeypatch) -> None:
    async def _rates() -> dict[str, float]:
        return {"USD": 90.0, "EUR": 100.0}

    monkeypatch.setattr(fx, "get_rates", _rates)
    body = {"title": "SSD", "price": 100, "currency": "USD"}
    out = await fx.apply_fx_to_offer_body(body)
    assert out["price_orig"] == 100
    assert out["price"] == 9000.0
    assert out["currency"] == "USD"


def test_normalize_currency() -> None:
    assert fx.normalize_currency("$") == "USD"
    assert fx.normalize_currency("евро") == "EUR"
    assert fx.normalize_currency("₽") == "RUB"
    assert fx.normalize_currency(None) == "RUB"
    assert fx.normalize_currency("usd") == "USD"


# ---------------------------------------------------------------- offers refresh
def _executor() -> ModuleActionExecutor:
    return ModuleActionExecutor(MagicMock())


def _hit(doc: dict[str, Any]) -> Any:
    h = MagicMock()
    h.source = doc
    return h


@pytest.mark.asyncio
async def test_offers_refresh_updates_price_and_marks_stale(monkeypatch) -> None:
    executor = _executor()
    offers = [
        # price changed in OS (raw USD price)
        {
            "row_id": "off_1",
            "body": {
                "title": "SSD",
                "src_hash": "h1",
                "price": 9000.0,
                "price_orig": 100.0,
                "currency": "USD",
                "score": 3,
            },
        },
        # position gone from the catalog
        {"row_id": "off_2", "body": {"title": "RAM", "src_hash": "h2", "price": 500.0, "score": 2}},
        # no hash -> untouched
        {"row_id": "off_3", "body": {"title": "No hash", "price": 7.0}},
    ]
    catalogs = [{"row_id": "cat_1", "body": {"status": "ready"}}]

    async def _rows(*, table_slug: str, **_: Any) -> list[dict]:
        return offers if table_slug == "found_offers" else catalogs

    monkeypatch.setattr(executor, "_list_rows_for_scope", AsyncMock(side_effect=_rows))
    updates: list[dict] = []

    async def _update(**kw: Any) -> dict:
        updates.append(kw)
        return {"row_id": kw["row_id"], "body": kw["body"]}

    monkeypatch.setattr(executor, "_update_row_for_scope", AsyncMock(side_effect=_update))

    async def _rates() -> dict[str, float]:
        return {"USD": 90.0, "EUR": 100.0}

    monkeypatch.setattr(fx, "get_rates", _rates)

    os_result = MagicMock()
    os_result.hits = [_hit({"src_hash": "h1", "price_num": 110.0, "supplier": "OCS"})]
    os_svc = MagicMock()
    os_svc.search = AsyncMock(return_value=os_result)

    import prodavan.application.modules.module_action_executor as me

    monkeypatch.setattr(
        "prodavan.core.infra.opensearch_manager.get_search_index_service", lambda: os_svc
    )

    out = await executor._offers_refresh(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        params={},
        principal=MagicMock(),
        employee=None,
    )
    assert out["checked"] == 3
    assert out["updated"] == 1
    assert out["stale"] == 1

    by_row = {u["row_id"]: u["body"] for u in updates}
    # price changed: orig updated (currency!), RUB reconverted from the NEW orig
    assert by_row["off_1"]["price_orig"] == 110.0
    assert by_row["off_1"]["price"] == 9900.0
    # gone: stale, price untouched
    assert by_row["off_2"]["is_stale"] is True
    assert by_row["off_2"]["price"] == 500.0
    assert "off_3" not in by_row


@pytest.mark.asyncio
async def test_offers_refresh_backfills_score_and_clears_stale(monkeypatch) -> None:
    executor = _executor()
    offers = [
        {
            "row_id": "off_1",
            "body": {
                "title": "SSD",
                "src_hash": "h1",
                "price": 100.0,
                "is_stale": True,
                "match_kind": "exact",
                "score": 0,
            },
        }
    ]
    catalogs = [{"row_id": "cat_1", "body": {"status": "ready"}}]

    async def _rows(*, table_slug: str, **_: Any) -> list[dict]:
        return offers if table_slug == "found_offers" else catalogs

    monkeypatch.setattr(executor, "_list_rows_for_scope", AsyncMock(side_effect=_rows))
    updates: list[dict] = []

    async def _update(**kw: Any) -> dict:
        updates.append(kw)
        return {"row_id": kw["row_id"], "body": kw["body"]}

    monkeypatch.setattr(executor, "_update_row_for_scope", AsyncMock(side_effect=_update))

    os_result = MagicMock()
    os_result.hits = [_hit({"src_hash": "h1", "price_num": 100.0})]
    os_svc = MagicMock()
    os_svc.search = AsyncMock(return_value=os_result)
    monkeypatch.setattr(
        "prodavan.core.infra.opensearch_manager.get_search_index_service", lambda: os_svc
    )

    out = await executor._offers_refresh(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        params={},
        principal=MagicMock(),
        employee=None,
    )
    body = updates[0]["body"]
    assert body["is_stale"] is False  # back in the catalog
    assert body["score"] == 1.0  # exact backfill
    assert body["price"] == 100.0  # unchanged price
    assert out["updated"] == 1


# ---------------------------------------------------------------- best-offer budget
@pytest.mark.asyncio
async def test_budget_sync_uses_best_candidate_when_unselected(monkeypatch) -> None:
    """No selection yet -> budget snapshot from the best candidate."""
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
            {"row_id": "line_1", "body": {"title": "SSD", "qty": 2, "part_number": "MZ"}}
        ],
        "found_offers": [
            {
                "row_id": "off_a",
                "body": {
                    "title": "SSD analog",
                    "price": 500,
                    "seller": "Expensive Ltd",
                    "line_id": "line_1",
                    "match_kind": "analog",
                    "score": 1,
                    "brand": "Kingston",
                },
            },
            {
                "row_id": "off_b",
                "body": {
                    "title": "SSD exact",
                    "price": 300,
                    "seller": "OCS",
                    "line_id": "line_1",
                    "match_kind": "exact",
                    "score": 1,
                    "brand": "Samsung",
                },
            },
        ],
        "budget_lines": [],
        "catalogs": [],
        "trusted_sellers": [],
    }
    created: list[dict] = []

    async def _rows(*, table_slug: str, **_: Any) -> list[dict]:
        return rows_by_table.get(table_slug, [])

    async def _create(*, table_slug: str, body: dict, **_: Any) -> dict:
        created.append({"table_slug": table_slug, "body": body})
        return {"row_id": f"row_new_{len(created)}", "body": body}

    async def _update(**kw: Any) -> dict:
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
    budget = next(c["body"] for c in created if c["table_slug"] == "budget_lines")
    # exact beats analog regardless of price ordering in the source
    assert budget["title"] == "SSD exact"
    assert budget["price_in"] == 300.0
    assert budget["seller"] == "OCS"
    assert budget["brand"] == "Samsung"
    assert out["created"] == 1
