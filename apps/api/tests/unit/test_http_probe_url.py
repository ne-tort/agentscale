"""Unit tests for http_probe URL join (no /v1 doubling)."""

from __future__ import annotations

from prodavan.application.ai_keys.probe.http_probe import _join_url


def test_join_url_strips_duplicate_v1() -> None:
    # ollama / cheapai: base ends with /v1, path /v1/models → /v1/models (not /v1/v1/models)
    assert _join_url("https://cheapai.lol/v1", "/v1/models") == "https://cheapai.lol/v1/models"
    assert _join_url("http://127.0.0.1:11434/v1", "/v1/models") == "http://127.0.0.1:11434/v1/models"
    # chat completions path
    assert _join_url("https://cheapai.lol/v1", "/v1/chat/completions") == "https://cheapai.lol/v1/chat/completions"


def test_join_url_no_segment_to_dedup() -> None:
    # OpenAI / Anthropic: base has no trailing segment matching path prefix.
    assert _join_url("https://api.openai.com", "/v1/models") == "https://api.openai.com/v1/models"
    assert _join_url("https://api.anthropic.com", "/v1/messages") == "https://api.anthropic.com/v1/messages"
    # OpenRouter base already includes /api/v1, path /models (no /v1 prefix)
    assert _join_url("https://openrouter.ai/api/v1", "/models") == "https://openrouter.ai/api/v1/models"
    assert _join_url("https://openrouter.ai/api/v1", "/chat/completions") == "https://openrouter.ai/api/v1/chat/completions"


def test_join_url_trailing_slash_stripped() -> None:
    assert _join_url("https://cheapai.lol/v1/", "/v1/models") == "https://cheapai.lol/v1/models"
    assert _join_url("https://api.openai.com/", "/v1/models") == "https://api.openai.com/v1/models"


def test_join_url_path_without_leading_slash() -> None:
    assert _join_url("https://api.openai.com", "v1/models") == "https://api.openai.com/v1/models"
