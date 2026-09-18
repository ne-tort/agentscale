"""Unit tests for AI key probe domain + http probe interpretation (PROBE-P1)."""

from __future__ import annotations

import pytest

from prodavan.domain.ai_keys import (
    ApiKind,
    ProbeKind,
    ProbeResult,
    ProbeStatus,
    is_http_probe_kind,
)


def test_http_probe_kind_classification() -> None:
    assert is_http_probe_kind("openai_api")
    assert is_http_probe_kind("anthropic_api")
    assert is_http_probe_kind("openrouter")
    assert is_http_probe_kind("custom")
    assert is_http_probe_kind("cursor_sdk")
    assert is_http_probe_kind("codex_sdk")
    assert is_http_probe_kind("claude_agent_sdk")
    # cli_subscription cannot be probed via HTTP
    assert not is_http_probe_kind("cli_subscription")
    assert not is_http_probe_kind(None)
    assert not is_http_probe_kind("")


def test_probe_result_to_dict_shape() -> None:
    result = ProbeResult(
        status=ProbeStatus.OK,
        kind=ProbeKind.MODELS,
        latency_ms=120,
        models=["gpt-5.1", "claude-sonnet-4-6"],
        default_model="gpt-5.1",
        http_status=200,
        provider="codex",
        api_kind="openai_api",
    )
    d = result.to_dict()
    assert d["status"] == "ok"
    assert d["kind"] == "models"
    assert d["latency_ms"] == 120
    assert d["models"] == ["gpt-5.1", "claude-sonnet-4-6"]
    assert d["default_model"] == "gpt-5.1"
    assert d["http_status"] == 200
    assert d["error_code"] is None
    assert d["error_message"] is None


def test_probe_result_error_shape() -> None:
    result = ProbeResult(
        status=ProbeStatus.ERROR,
        error_code="AUTH_INVALID",
        error_message="401 Unauthorized",
        provider="claude_code",
        api_kind="anthropic_api",
    )
    d = result.to_dict()
    assert d["status"] == "error"
    assert d["error_code"] == "AUTH_INVALID"
    assert d["models"] == []


def test_probe_status_enum_values() -> None:
    assert ProbeStatus.OK == "ok"
    assert ProbeStatus.ERROR == "error"
    assert ProbeStatus.UNAVAILABLE == "unavailable"


def test_probe_kind_enum_values() -> None:
    assert ProbeKind.MODELS == "models"
    assert ProbeKind.CHAT == "chat"


def test_probe_result_is_frozen() -> None:
    result = ProbeResult(status=ProbeStatus.OK)
    with pytest.raises(Exception):
        result.status = ProbeStatus.ERROR  # type: ignore[misc]
