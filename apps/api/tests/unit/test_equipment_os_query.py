"""Unit tests for OpenSearch query builder used by equipment catalog search."""

from __future__ import annotations

from prodavan.application.tenant_infra.equipment_catalog_search_service import (
    _build_os_query,
)


def test_build_os_query_default_text_fields() -> None:
    q = _build_os_query(
        query="Schneider LC1",
        part_number=None,
        brand=None,
        price_min=None,
        price_max=None,
        in_stock_only=True,
        catalog_ids=None,
    )
    should = q["bool"]["must"][0]["bool"]["should"]
    fields = should[0]["multi_match"]["fields"]
    assert "title^3" in fields
    assert "brand^2" in fields
    assert "supplier" in fields
    assert any(f.get("term", {}).get("in_stock") is True for f in q["bool"]["filter"])


def test_build_os_query_brand_matches_title_or_brand() -> None:
    q = _build_os_query(
        query=None,
        part_number="LC1D09",
        brand="Schneider",
        price_min=None,
        price_max=None,
        in_stock_only=False,
        catalog_ids=None,
    )
    brand_filter = next(
        f for f in q["bool"]["filter"] if "bool" in f and "should" in f["bool"]
    )
    should = brand_filter["bool"]["should"]
    assert any(s.get("term", {}).get("brand") == "Schneider" for s in should)
    assert any(s.get("match_phrase", {}).get("title") == "Schneider" for s in should)
