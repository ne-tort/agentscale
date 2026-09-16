"""Unit tests — adapter_kinds single source of truth (audit CLAW-P1a)."""

from __future__ import annotations

import pytest

from prodavan.application.agent.adapter_kinds import (
    BRIDGE_ADAPTER_KINDS,
    PLATFORM_OPENCLAW_ADAPTER,
    api_kind_to_bridge_adapter,
)


@pytest.mark.parametrize(
    "api_kind, expected",
    [
        ("cursor_sdk", "cursor_sdk"),
        ("codex_sdk", "codex_sdk"),
        ("claude_agent_sdk", "claude_agent_sdk"),
        ("openrouter", PLATFORM_OPENCLAW_ADAPTER),
        ("anthropic_api", PLATFORM_OPENCLAW_ADAPTER),
        ("openai_api", PLATFORM_OPENCLAW_ADAPTER),
        ("custom", PLATFORM_OPENCLAW_ADAPTER),
    ],
)
def test_api_kind_to_bridge_adapter_mapping(api_kind: str, expected: str) -> None:
    assert api_kind_to_bridge_adapter(api_kind) == expected


def test_none_or_empty_maps_to_platform_default() -> None:
    # The old private copy in openclaw_config_materializer accepted None and
    # returned the platform default; the unified mapping must match so that
    # materialization and the bridge send path agree on the adapter.
    assert api_kind_to_bridge_adapter(None) == PLATFORM_OPENCLAW_ADAPTER
    assert api_kind_to_bridge_adapter("") == PLATFORM_OPENCLAW_ADAPTER


def test_bridge_adapter_kinds_cover_all_sdk_dialects() -> None:
    assert BRIDGE_ADAPTER_KINDS == {
        "platform_openclaw",
        "openclaw_sdk",
        "cursor_sdk",
        "codex_sdk",
        "claude_agent_sdk",
    }
