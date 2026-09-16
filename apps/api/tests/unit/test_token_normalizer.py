"""Unit tests — TokenNormalizer / usage normalization (audit CLAW-P0b)."""

from __future__ import annotations

import pytest

from prodavan.application.agent.token_normalizer import (
    TokenNormalizer,
    extract_usage,
    normalize_usage_event,
)
from prodavan.domain.agent import AgentEvent, AgentEventType


def _usage(data: dict) -> AgentEvent:
    return AgentEvent.now(AgentEventType.USAGE, data)


def test_stateful_dedupes_by_message_id() -> None:
    norm = TokenNormalizer()
    first = norm.normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "message_id": "msg_1",
            "provider": "anthropic",
            "model": "claude-3",
        }
    )
    dup = norm.normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "message_id": "msg_1",
            "provider": "anthropic",
            "model": "claude-3",
        }
    )
    assert first is not None
    assert first.input_tokens == 100
    # Duplicate message_id must be skipped to avoid double-counting.
    assert dup is None


def test_cache_tokens_are_captured() -> None:
    norm = TokenNormalizer()
    out = norm.normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 90,
            "cache_read_input_tokens": 200,
            "message_id": "msg_cache",
        }
    )
    assert out is not None
    assert out.cache_creation_tokens == 90
    assert out.cache_read_tokens == 200
    assert out.message_id == "msg_cache"


def test_fully_zero_trailing_usage_is_skipped() -> None:
    norm = TokenNormalizer()
    real = norm.normalize_usage(
        {"input_tokens": 100, "output_tokens": 50, "message_id": "msg_real"}
    )
    trailing_zero = norm.normalize_usage(
        {"input_tokens": 0, "output_tokens": 0, "message_id": "msg_zero"}
    )
    assert real is not None
    # A fully-zero usage event must not overwrite the last real row.
    assert trailing_zero is None


def test_nonzero_usage_with_message_id_is_persisted() -> None:
    norm = TokenNormalizer()
    out = norm.normalize_usage(
        {"input_tokens": 0, "output_tokens": 7, "message_id": "msg_only_out"}
    )
    assert out is not None
    assert out.output_tokens == 7


def test_cost_estimated_cost_alias() -> None:
    norm = TokenNormalizer()
    out = norm.normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost_usd": "0.0123",
            "message_id": "msg_cost",
        }
    )
    assert out is not None
    assert out.cost_usd == pytest.approx(0.0123)


def test_reset_clears_dedupe_state() -> None:
    norm = TokenNormalizer()
    first = norm.normalize_usage(
        {"input_tokens": 10, "output_tokens": 5, "message_id": "msg_x"}
    )
    norm.reset()
    again = norm.normalize_usage(
        {"input_tokens": 10, "output_tokens": 5, "message_id": "msg_x"}
    )
    assert first is not None
    # After reset, the same message_id is accepted again (new turn).
    assert again is not None


def test_stateless_normalize_usage_event_projects_cache_fields() -> None:
    ev = _usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 90,
            "cache_read_input_tokens": 200,
        }
    )
    out = normalize_usage_event(ev)
    assert out is not None
    assert out.data["cache_creation_tokens"] == 90
    assert out.data["cache_read_tokens"] == 200
    assert out.data["input_tokens"] == 10
    # Aliases are projected away.
    assert "cache_creation_input_tokens" not in out.data
    assert "cache_read_input_tokens" not in out.data


def test_stateless_normalize_usage_event_drops_fully_zero() -> None:
    ev = _usage({"input_tokens": 0, "output_tokens": 0})
    assert normalize_usage_event(ev) is None


def test_stateless_passes_through_non_usage_events() -> None:
    ev = AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "hi"})
    assert normalize_usage_event(ev) is ev


def test_extract_usage_to_payload_canonicalizes_aliases() -> None:
    # Fully-zero usage (normalize_usage_event drops it) still needs a
    # canonical payload when persisted to the transcript — extract_usage +
    # to_payload projects vendor aliases to canonical fields so agent_events
    # never carries cache_creation_input_tokens / estimated_cost_usd.
    payload = extract_usage(
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "estimated_cost_usd": 0,
            "usage_source": "bridge",
        }
    ).to_payload()
    assert "cache_creation_input_tokens" not in payload
    assert "cache_read_input_tokens" not in payload
    assert "estimated_cost_usd" not in payload
    assert "usage_source" not in payload
    assert payload["cache_creation_tokens"] == 0
    assert payload["cache_read_tokens"] == 0
    assert payload["token_source"] == "bridge"


def test_extract_usage_to_payload_omits_null_optional_fields() -> None:
    payload = extract_usage({"input_tokens": 1, "output_tokens": 2}).to_payload()
    assert payload == {
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_creation_tokens": None,
        "cache_read_tokens": None,
    }
