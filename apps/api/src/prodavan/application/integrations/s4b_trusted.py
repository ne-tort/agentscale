"""S4B trusted distributors (sieve, not a hard ban)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prodavan.application.integrations.s4b_parse import normalize_distributor
from prodavan.config.settings import settings


def trusted_sellers_path() -> Path:
    return (
        settings.packs_root
        / "electronics-procurement"
        / "integrations"
        / "s4b-trusted-sellers.json"
    )


def load_trusted_sellers() -> list[str]:
    path = trusted_sellers_path()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sellers = data.get("sellers") if isinstance(data, dict) else data
    if not isinstance(sellers, list):
        return []
    return [str(name).strip() for name in sellers if str(name).strip()]


def is_trusted_distributor(name: str, sellers: list[str] | None = None) -> bool:
    raw = normalize_distributor(name or "").strip()
    if not raw:
        return False
    low = raw.lower()
    pool = sellers if sellers is not None else load_trusted_sellers()
    for trusted in pool:
        tl = trusted.strip().lower()
        if not tl:
            continue
        if low == tl or tl in low or low in tl:
            return True
    return False


def split_trusted(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sellers = load_trusted_sellers()
    trusted: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for item in items:
        if is_trusted_distributor(str(item.get("distributor") or ""), sellers):
            trusted.append(item)
        else:
            other.append(item)
    return trusted, other
