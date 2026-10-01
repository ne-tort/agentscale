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
async def test_offers_refresh_alias_routes_to_pipeline(monkeypatch) -> None:
    """WAVE7: старый kind equipment.offers_refresh — алиас полного пайплайна.

    Семантика (обновление цены в исходной валюте, is_stale) покрыта
    tests/unit/test_equipment_offers_pipeline.py.
    """
    executor = ModuleActionExecutor(MagicMock())
    calls: list[dict] = []

    async def _pipeline(**kwargs):
        calls.append(kwargs)
        return {"kind": "equipment.pipeline"}

    monkeypatch.setattr(executor, "_equipment_pipeline", _pipeline)

    async def _load(*args, **kwargs):  # noqa: ANN003
        return {"id": "offers_refresh", "kind": "equipment.offers_refresh", "params": {}}

    monkeypatch.setattr(executor, "_load_action", _load)
    out = await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id="offers_refresh",
        principal=_principal(),
        employee=None,
    )
    assert out["kind"] == "equipment.pipeline"
    assert calls[0]["materialize"] is True


def _principal() -> Any:
    from prodavan.domain.identity import Principal

    return Principal(sub="u1", roles=frozenset())
