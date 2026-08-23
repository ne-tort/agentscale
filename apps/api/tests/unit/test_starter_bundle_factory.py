"""Unit tests — starter bundle factory (L04)."""

from prodavan.domain.admin.starter_bundle_factory import build_equipment_procurement_bundle
from prodavan.infrastructure.cabinets.bundle_codec import unpack_bundle


def test_equipment_procurement_bundle_unpacks() -> None:
    raw = build_equipment_procurement_bundle()
    parsed = unpack_bundle(raw)
    assert parsed["manifest"]["format"] == "cabinet.bundle"
    slugs = {t["slug"] for t in parsed["tables"]}
    assert "line_items" in slugs
    assert any(t.get("title") == "Line items" and not t.get("system") for t in parsed["tabs"])
    assert parsed["data_by_slug"]["line_items"]
