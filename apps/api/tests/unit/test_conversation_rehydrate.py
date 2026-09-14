"""Unit tests for conversation rehydrate after pod hydrate_generation bump."""

from __future__ import annotations

from prodavan.application.agent.conversation_rehydrate import (
    HYDRATE_GEN_KEY,
    format_rehydrate_bridge_message,
    needs_conversation_rehydrate,
    stamp_hydrate_generation,
)


def test_needs_rehydrate_when_generation_changes() -> None:
    assert needs_conversation_rehydrate({HYDRATE_GEN_KEY: 1}, current_generation=2)
    assert not needs_conversation_rehydrate({HYDRATE_GEN_KEY: 2}, current_generation=2)
    assert needs_conversation_rehydrate(None, current_generation=1)
    assert not needs_conversation_rehydrate({HYDRATE_GEN_KEY: 0}, current_generation=None)


def test_stamp_hydrate_generation_preserves_other_keys() -> None:
    out = stamp_hydrate_generation({"agentId": "a1"}, 3)
    assert out["agentId"] == "a1"
    assert out[HYDRATE_GEN_KEY] == 3


def test_format_rehydrate_includes_prior_turns() -> None:
    msg = format_rehydrate_bridge_message(
        history=[
            {"role": "user", "text": "Найди LC1D09"},
            {"role": "assistant", "text": "Ищу в каталоге"},
            {"role": "tool", "text": "equipment_catalog_search"},
        ],
        new_message="Добавь в найденные",
    )
    assert msg is not None
    assert "Найди LC1D09" in msg
    assert "Ищу в каталоге" in msg
    assert "equipment_catalog_search" not in msg
    assert "Добавь в найденные" in msg
    assert "pod runtime restarted" in msg.lower() or "Workspace was updated" in msg


def test_format_rehydrate_empty_history() -> None:
    assert format_rehydrate_bridge_message(history=[], new_message="hi") is None
