"""Unit tests — starter bundle catalog (L04)."""

from prodavan.application.admin.starter_bundle_service import StarterBundleCatalogService


def test_list_starter_bundles_includes_equipment() -> None:
    items = StarterBundleCatalogService().list_entries()
    assert any(i["id"] == "equipment-procurement" for i in items)
    match = next(i for i in items if i["id"] == "equipment-procurement")
    assert match["official"] is True
    assert "bundle_available" in match
