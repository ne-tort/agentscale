"""Unit tests — live model default resolution."""

from prodavan.application.ai_models.live_service import resolve_ui_default


def test_resolve_ui_default_prefers_catalog_when_in_effective() -> None:
    assert resolve_ui_default(["gpt-5", "default"], "gpt-5") == "gpt-5"


def test_resolve_ui_default_uses_sdk_default_id() -> None:
    assert resolve_ui_default(["composer-2.5", "default"], None) == "default"


def test_resolve_ui_default_falls_back_to_first() -> None:
    assert resolve_ui_default(["composer-2.5", "claude-opus"], None) == "composer-2.5"


def test_resolve_ui_default_empty() -> None:
    assert resolve_ui_default([], None) is None
