"""Catalog seed specs stay aligned with AI key api_kind / provider enums."""

from __future__ import annotations

from prodavan.application.ai_keys.service import API_KINDS, PROVIDERS
from prodavan.application.catalog.service import _AI_HTTP_SEED, CATALOG_AI_HTTP_PROVIDERS


def test_ai_http_seed_payloads_are_valid() -> None:
    assert CATALOG_AI_HTTP_PROVIDERS == "ai.http_providers"
    assert len(_AI_HTTP_SEED) >= 4
    ids = {s["id"] for s in _AI_HTTP_SEED}
    assert ids == {"openai", "anthropic", "openrouter", "cursor"}
    for spec in _AI_HTTP_SEED:
        payload = spec["payload"]
        assert payload["agent_provider"] in PROVIDERS
        assert payload["api_kind"] in API_KINDS
        assert "title" in spec
