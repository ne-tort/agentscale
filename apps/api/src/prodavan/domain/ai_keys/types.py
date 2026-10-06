"""AI Provider Keys — domain types (L03)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AiProvider(StrEnum):
    CURSOR = "cursor"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    XAI = "xai"


class ApiKind(StrEnum):
    CURSOR_SDK = "cursor_sdk"
    CODEX_SDK = "codex_sdk"
    OPENAI_API = "openai_api"
    CLAUDE_AGENT_SDK = "claude_agent_sdk"
    ANTHROPIC_API = "anthropic_api"
    OPENROUTER = "openrouter"
    CLI_SUBSCRIPTION = "cli_subscription"
    CUSTOM = "custom"
    # xAI (Grok) через OAuth device-code: секрет = JSON-блоб токенов,
    # access_token обновляется сервером (refresh_token ротацией)
    XAI_OAUTH = "xai_oauth"


class KeyStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


# Runtime-capable kinds — NEVER include cli_subscription
RUNTIME_API_KINDS: frozenset[str] = frozenset(
    {
        ApiKind.CURSOR_SDK,
        ApiKind.CODEX_SDK,
        ApiKind.OPENAI_API,
        ApiKind.CLAUDE_AGENT_SDK,
        ApiKind.ANTHROPIC_API,
        ApiKind.OPENROUTER,
        ApiKind.CUSTOM,
        ApiKind.XAI_OAUTH,
    }
)


def is_runtime_api_kind(api_kind: str) -> bool:
    return api_kind in RUNTIME_API_KINDS and api_kind != ApiKind.CLI_SUBSCRIPTION


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    key_id: str
    provider: str
    api_kind: str
    secret: str
    model_defaults: dict | None = None
