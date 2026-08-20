"""S4B availability mapping. listStock is not always in_stock."""

from __future__ import annotations

import re
from typing import Any

_ON_ORDER_LEAD_RE = re.compile(r"под\s*заказ", re.I)
_ZERO_QTY = frozenset({"0", "0.0", "-", "нет", "no", "n/a", "нет в наличии"})


def map_s4b_availability(raw: Any) -> str:
    text = str(raw or "").strip()
    if text in {"in_stock", "on_order", "unknown", "missing"}:
        return text
    key = text.lower().replace("_", "").replace("-", "")
    if key in {"stock", "instock"}:
        return "in_stock"
    if key in {"nostock", "onorder", "order"}:
        return "on_order"
    return "unknown"


def effective_s4b_availability(item: dict[str, Any]) -> str:
    raw = item.get("availability_raw") if "availability_raw" in item else item.get("availability")
    mapped = map_s4b_availability(raw)
    if mapped != "in_stock":
        return mapped
    lead = item.get("delivery_time") or item.get("lead_time")
    if _ON_ORDER_LEAD_RE.search(str(lead or "")):
        return "on_order"
    qty = str(item.get("quantity") or "").strip().lower()
    if qty in _ZERO_QTY:
        return "on_order"
    return "in_stock"


def is_s4b_in_stock(item: dict[str, Any]) -> bool:
    return effective_s4b_availability(item) == "in_stock"
