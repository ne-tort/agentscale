"""Unit tests for the shared provider endpoint URL helpers (scheme + dedup)."""

from __future__ import annotations

from prodavan.domain.ai_keys.http_url import (
    endpoint_path_for,
    ensure_scheme,
    join_endpoint_url,
)


def test_ensure_scheme_bare_host_gets_https() -> None:
    assert ensure_scheme("cheapai.lol/v1") == "https://cheapai.lol/v1"
    assert ensure_scheme("cheapai.lol") == "https://cheapai.lol"


def test_ensure_scheme_keeps_existing_scheme() -> None:
    assert ensure_scheme("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    assert ensure_scheme("https://api.openai.com") == "https://api.openai.com"


def test_ensure_scheme_empty_and_whitespace() -> None:
    assert ensure_scheme("") == ""
    assert ensure_scheme("   ") == ""


def test_join_url_scheme_less_base() -> None:
    # The choapi case from the catalog: payload base_url without a scheme.
    assert join_endpoint_url("cheapai.lol/v1", "/v1/models") == "https://cheapai.lol/v1/models"


def test_endpoint_path_for_dedups_shared_segment() -> None:
    # base "https://x/v1" + path "/v1/models" → the pair concatenates to a
    # single /v1/models (bridge joins base_url + models_path verbatim).
    assert endpoint_path_for("https://cheapai.lol/v1", "/v1/models") == "/models"
    assert endpoint_path_for("http://127.0.0.1:11434/v1", "/v1/models") == "/models"


def test_endpoint_path_for_no_dedup() -> None:
    assert endpoint_path_for("https://api.openai.com", "/v1/models") == "/v1/models"
    assert endpoint_path_for("https://openrouter.ai/api/v1", "/models") == "/models"


def test_endpoint_path_for_scheme_less_base() -> None:
    assert endpoint_path_for("cheapai.lol/v1", "/v1/models") == "/models"


def test_endpoint_path_for_path_equals_segment() -> None:
    # base ends with /v1 and the path is exactly /v1 → base as-is.
    assert endpoint_path_for("https://cheapai.lol/v1", "/v1") == "/"
