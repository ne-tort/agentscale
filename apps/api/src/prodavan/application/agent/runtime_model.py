"""Runtime model sanitization — strip legacy HTTP defaults before bridge/SDK calls."""

from __future__ import annotations

from prodavan.domain.ai_keys import ApiKind

_LEGACY_HTTP_DEFAULT_MODELS = frozenset(
    {
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
    }
)

_PSEUDO_SDK_MODELS = frozenset({"default"})

_SDK_FALLBACK_MODELS: dict[str, str | None] = {
    ApiKind.CURSOR_SDK: None,
    ApiKind.CODEX_SDK: "gpt-5.3-codex",
    ApiKind.CLAUDE_AGENT_SDK: "claude-sonnet-4-6",
}


def sanitize_runtime_model(model: str | None) -> str | None:
    """Drop empty values and legacy OpenAI HTTP defaults invalid for SDK adapters."""
    if model is None:
        return None
    cleaned = str(model).strip()
    if not cleaned or cleaned in _LEGACY_HTTP_DEFAULT_MODELS or cleaned in _PSEUDO_SDK_MODELS:
        return None
    return cleaned


def sdk_fallback_model(api_kind: str) -> str | None:
    """Per-adapter SDK default when no explicit model was chosen."""
    return _SDK_FALLBACK_MODELS.get(api_kind)
