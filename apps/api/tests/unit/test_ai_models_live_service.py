"""Unit tests — live model enrichment and filters."""

from prodavan.application.ai_models.live_service import (
    catalog_by_model_name,
    enrich_live_model,
    filter_effective_live_ids,
)


def test_filter_effective_live_ids_case_insensitive_enabled() -> None:
    catalog = [
        {"name": "GPT-5", "enabled": True},
        {"name": "claude-opus", "enabled": False},
    ]
    live = ["gpt-5", "claude-opus", "default"]
    effective = filter_effective_live_ids(live, catalog, [])
    assert effective == ["gpt-5"]


def test_filter_effective_live_ids_all_when_no_enabled() -> None:
    catalog = [{"name": "GPT-5", "enabled": False}]
    live = ["gpt-5", "default"]
    effective = filter_effective_live_ids(live, catalog, [])
    assert effective == ["gpt-5", "default"]


def test_filter_effective_live_ids_matches_by_alias() -> None:
    """A live id matching any model alias (model_ids) of an enabled model is kept."""
    catalog = [
        {
            "name": "Claude Opus 4.8",
            "model_ids": ["claude-opus-4.8", "ca-opus-4.8"],
            "enabled": True,
        },
        {"name": "GPT-5", "model_ids": ["gpt-5"], "enabled": False},
    ]
    live = ["claude-opus-4.8", "ca-opus-4.8", "gpt-5", "default"]
    effective = filter_effective_live_ids(live, catalog, [])
    assert effective == ["claude-opus-4.8", "ca-opus-4.8"]


def test_enrich_live_model_matches_catalog_case_insensitive() -> None:
    lookup = catalog_by_model_name(
        [
            {
                "name": "GPT-5",
                "input_price_usd_per_mtok": 1.5,
                "output_price_usd_per_mtok": 2.0,
                "max_context_tokens": 128000,
                "publisher": "OpenAI",
                "released_at": "2024-05-13",
            }
        ]
    )
    item = enrich_live_model("gpt-5", lookup)
    assert item["catalog_matched"] is True
    assert item["input_price_usd_per_mtok"] == 1.5
    assert item["publisher"] == "OpenAI"


def test_enrich_live_model_unmatched() -> None:
    item = enrich_live_model("unknown-model", {})
    assert item["catalog_matched"] is False
    assert item["publisher"] is None
