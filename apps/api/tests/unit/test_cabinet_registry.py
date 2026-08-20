"""Cabinet module registry smoke tests."""

from prodavan.cabinets.registry import get_module_for_profile, registered_pack_ids


def test_registered_packs_include_electronics_and_generic():
    ids = registered_pack_ids()
    assert "electronics-procurement" in ids
    assert "generic-assistant" in ids


def test_generic_has_no_procurement_commands():
    mod = get_module_for_profile("generic-assistant")
    assert mod.pack_id == "generic-assistant"
    h = mod.health()
    assert h.status == "ok"


def test_electronics_health():
    mod = get_module_for_profile("electronics-procurement")
    assert mod.pack_id == "electronics-procurement"
    assert mod.health().pack_version
