"""Unit tests for AI models catalog domain helpers (MODELS-L1)."""

from __future__ import annotations

import sys

from prodavan.application.ai_models.service import _normalize_aliases, _normalize_api_kinds


def test_normalize_aliases_dedup_case_insensitive() -> None:
    out = _normalize_aliases(["ca-opus-4.6", "CA-OPUS-4.6", "claude-opus-4-6", ""])
    assert out == ["ca-opus-4.6", "claude-opus-4-6"]


def test_normalize_aliases_strips_and_drops_empty() -> None:
    out = _normalize_aliases(["  ca-opus-4.6  ", "", "   "])
    assert out == ["ca-opus-4.6"]


def test_normalize_aliases_none() -> None:
    assert _normalize_aliases(None) == []


def test_normalize_api_kinds_dedup_and_strip() -> None:
    out = _normalize_api_kinds(["cursor_sdk", "cursor_sdk", " codex_sdk ", ""])
    assert out == ["cursor_sdk", "codex_sdk"]


def test_normalize_api_kinds_none() -> None:
    assert _normalize_api_kinds(None) == []


def test_service_module_importable() -> None:
    from prodavan.application.ai_models.service import AiModelsService

    assert AiModelsService is not None
    # ensure the alias/normalize helpers are exported at module level
    assert "AiModelsService" in dir(sys.modules["prodavan.application.ai_models.service"])
