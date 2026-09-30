"""FX rates for equipment offers: ЦБ РФ daily XML with cached fallback.

Canonical use: found_offers rows carry the ORIGINAL price (``price_orig``) in
``currency``; the RUB price (``price`` — used by «Бюджетирование») is derived
at write time via [convert_offer_price]. Rates are cached in-process for 12h;
when ЦБ РФ is unreachable, fallback constants are used (logged once) so the
budget math never mixes currencies.
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
_CACHE_TTL_SEC = 12 * 3600

_SUPPORTED = ("RUB", "USD", "EUR")
_FALLBACK_RATES = {"USD": 80.0, "EUR": 95.0}

_cache_lock = asyncio.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "rates": dict(_FALLBACK_RATES)}


def normalize_currency(raw: Any) -> str:
    """RUB/USD/EUR (case/symbol tolerant)."""
    s = str(raw or "").strip().upper()
    if s in {"RUB", "RUR", "РУБ", "₽", "Р"}:
        return "RUB"
    if s in {"USD", "$", "USA", "Д", "ДOLLAR"}:
        return "USD"
    if s in {"EUR", "€", "Е", "ЕВРО", "EVRO"}:
        return "EUR"
    return "RUB"


def _parse_cbr_xml(text: str) -> dict[str, float]:
    rates: dict[str, float] = {}
    root = ET.fromstring(text)
    for val in root.findall("Valute"):
        code = (val.findtext("CharCode") or "").strip().upper()
        if code not in _SUPPORTED:
            continue
        raw = (val.findtext("Value") or "").replace(",", ".").replace("\xa0", "")
        try:
            nominal = float((val.findtext("Nominal") or "1").replace(",", "."))
            value = float(raw)
        except ValueError:
            continue
        if nominal > 0:
            rates[code] = value / nominal
    return rates


async def get_rates() -> dict[str, float]:
    """Current RUB-per-unit rates for USD/EUR (cached, fallback-safe)."""
    now = time.monotonic()
    if _cache["rates"] and now - float(_cache["ts"]) < _CACHE_TTL_SEC:
        return dict(_cache["rates"])  # type: ignore[arg-type]
    async with _cache_lock:
        now = time.monotonic()
        if _cache["rates"] and now - float(_cache["ts"]) < _CACHE_TTL_SEC:
            return dict(_cache["rates"])  # type: ignore[arg-type]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(CBR_URL, headers={"User-Agent": "prodavan/1.0"})
                resp.raise_for_status()
                rates = _parse_cbr_xml(resp.text)
            if rates.get("USD") and rates.get("EUR"):
                _cache["ts"] = now
                _cache["rates"] = rates
                return dict(rates)
            logger.warning("fx: incomplete ЦБ РФ payload, using fallback rates")
        except Exception:
            logger.warning("fx: ЦБ РФ fetch failed, using fallback rates", exc_info=True)
        return dict(_FALLBACK_RATES)


async def convert_offer_price(
    *, price: float, currency: str
) -> tuple[float, str]:
    """(rub_price, currency) — original price in `currency` converted to RUB."""
    cur = normalize_currency(currency)
    if cur == "RUB":
        return float(price), "RUB"
    rates = await get_rates()
    return round(float(price) * rates.get(cur, _FALLBACK_RATES.get(cur, 1.0)), 2), cur


async def apply_fx_to_offer_body(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize an offer body: price stays RUB-canonical, price_orig keeps
    the original currency value.

    Input contract (agent/UI): ``price`` = price in ``currency`` (default RUB).
    Output contract (storage): ``price`` = RUB, ``price_orig`` = original,
    ``currency`` = original currency (RUB when not set).
    """
    cur = normalize_currency(body.get("currency"))
    raw = body.get("price")
    if raw is None or isinstance(raw, bool):
        return body
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return body
    if cur == "RUB":
        body = dict(body)
        body["currency"] = "RUB"
        body["price"] = price
        body.setdefault("price_orig", price)
        return body
    rub, cur_out = await convert_offer_price(price=price, currency=cur)
    body = dict(body)
    body["currency"] = cur_out
    body["price_orig"] = price
    body["price"] = rub
    return body
