"""Single source of truth for api_kind → bridge adapter mapping (L03).

Audit CLAW-P1a: the mapping ``api_kind -> bridge adapter_kind`` previously
existed in two copies (``openclaw_bridge.api_kind_to_bridge_adapter`` and
``openclaw_config_materializer._api_kind_to_bridge_adapter``). They had
already drifted once (private copy accepts ``None``). Both mcp config
materialization and the bridge send path must resolve the same adapter for
the same key, otherwise the pod runtime receives a different adapter than
the workspace config.yaml advertises.

This module is the only place that knows the mapping. Callers import
``api_kind_to_bridge_adapter`` here; ``None``/empty is normalized to the
platform default before mapping so the two call-sites behave identically.
"""

from __future__ import annotations

from prodavan.domain.ai_keys import ApiKind

#: Adapter kinds the OpenClaw bridge Pod sidecar accepts.
BRIDGE_ADAPTER_KINDS: frozenset[str] = frozenset(
    {
        "platform_openclaw",
        "openclaw_sdk",
        "cursor_sdk",
        "codex_sdk",
        "claude_agent_sdk",
    }
)

#: Default adapter when ``api_kind`` is not a proprietary SDK.
PLATFORM_OPENCLAW_ADAPTER = "platform_openclaw"


def api_kind_to_bridge_adapter(api_kind: str | None) -> str:
    """Map a Prodavan ``api_kind`` to the OpenClaw bridge ``adapter_kind``.

    ``None``/empty maps to the platform OpenClaw adapter so that HTTP LLM
    providers (openai_api / anthropic_api / openrouter / custom / ...) share
    one default instead of each call-site deciding separately.
    """
    if not api_kind:
        return PLATFORM_OPENCLAW_ADAPTER
    if api_kind == ApiKind.CURSOR_SDK:
        return "cursor_sdk"
    if api_kind == ApiKind.CODEX_SDK:
        return "codex_sdk"
    if api_kind == ApiKind.CLAUDE_AGENT_SDK:
        return "claude_agent_sdk"
    return PLATFORM_OPENCLAW_ADAPTER
