"""Materialize `.prodavan/config.yaml` for OpenClaw bridge (Batch AS.1 / L14)."""

from __future__ import annotations

from typing import Any

import yaml

from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.domain.agent import AgentToolPolicy, default_tool_policy
from prodavan.domain.ai_keys import ApiKind

_OPENCLAW_CONFIG_PATH = ".prodavan/config.yaml"

_PROVIDER_DEFAULT_API_KIND: dict[str, str] = {
    "cursor": ApiKind.CURSOR_SDK,
    "codex": ApiKind.CODEX_SDK,
    "claude_code": ApiKind.CLAUDE_AGENT_SDK,
}

# OpenClaw builtin tool ids (L07 v1 set subset used by policy presets).
_READ_TOOLS = ("fs.read", "fs.list", "search.grep", "search.glob")
_WRITE_TOOLS = ("fs.write", "fs.edit")
_SHELL_TOOLS = ("shell.exec",)


def _api_kind_to_bridge_adapter(api_kind: str | None) -> str:
    if api_kind == ApiKind.CURSOR_SDK:
        return "cursor_sdk"
    if api_kind == ApiKind.CODEX_SDK:
        return "codex_sdk"
    if api_kind == ApiKind.CLAUDE_AGENT_SDK:
        return "claude_agent_sdk"
    return "platform_openclaw"


def mcp_packages_to_openclaw_servers(packages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Prodavan mcp.json packages[] → OpenClaw config.mcp.servers map."""
    servers: dict[str, dict[str, Any]] = {}
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        name = str(pkg.get("name") or "").strip()
        command = pkg.get("command")
        if not name or not command:
            continue
        args = pkg.get("args") or []
        normalized_args: list[str] = []
        for arg in args if isinstance(args, list) else []:
            text = str(arg)
            if text.startswith("packages/"):
                text = f"${{WORKSPACE}}/{text}"
            normalized_args.append(text)
        entry: dict[str, Any] = {"command": str(command), "args": normalized_args}
        raw_env = pkg.get("env")
        if isinstance(raw_env, dict) and raw_env:
            entry["env"] = {str(k): str(v) for k, v in raw_env.items() if str(k).strip()}
        servers[name] = entry
    return servers


def provider_to_default_api_kind(provider: str | None) -> str | None:
    """Map project agent_provider (cursor/codex/…) → runtime api_kind."""
    if not provider or not str(provider).strip():
        return None
    return _PROVIDER_DEFAULT_API_KIND.get(str(provider).strip())


def api_kind_to_provider_dialect(api_kind: str | None) -> str | None:
    """HTTP provider dialect for platform_openclaw runtime."""
    if not api_kind:
        return None
    if api_kind in {ApiKind.ANTHROPIC_API, ApiKind.CLAUDE_AGENT_SDK}:
        return "anthropic_messages"
    if api_kind in {ApiKind.OPENAI_API, ApiKind.OPENROUTER, ApiKind.CUSTOM, ApiKind.CODEX_SDK}:
        return "openai_compat"
    return None


def tool_policy_to_permissions(policy: AgentToolPolicy) -> dict[str, Any]:
    """Map AgentToolPolicy preset fields → OpenClaw permissions block."""
    allow: list[str] = []
    ask: list[str] = []
    deny: list[str] = list(policy.extra_deny_tools)

    if policy.fs_read:
        allow.extend(_READ_TOOLS)
    if policy.fs_write:
        allow.extend(_WRITE_TOOLS)
        if policy.approval in {"dangerous_only", "all_tools"}:
            ask.extend(_WRITE_TOOLS)
    elif policy.fs_read:
        deny.extend(_WRITE_TOOLS)

    if policy.shell_exec:
        if policy.approval in {"dangerous_only", "all_tools"}:
            ask.extend(_SHELL_TOOLS)
        else:
            allow.extend(_SHELL_TOOLS)
    else:
        deny.extend(_SHELL_TOOLS)

    if policy.sandbox == "strict" and policy.fs_write:
        mode = "plan"
    elif policy.approval == "none" and policy.fs_write and policy.shell_exec:
        mode = "acceptEdits"
    elif policy.approval in {"dangerous_only", "all_tools"}:
        mode = "default"
    else:
        mode = "plan" if not policy.fs_write else "default"

    # Dedupe preserving order
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    allow = _dedupe(allow)
    ask = _dedupe([a for a in ask if a not in allow])
    deny = _dedupe([d for d in deny if d not in allow])

    perms: dict[str, Any] = {"mode": mode}
    if allow:
        perms["allow"] = allow
    if ask:
        perms["ask"] = ask
    if deny:
        perms["deny"] = deny
    return perms


def build_openclaw_config(
    *,
    company_policy: CompanyAgentRuntimePolicy,
    tool_policy: AgentToolPolicy | None = None,
    api_kind: str | None = None,
    provider_key_id: str | None = None,
    mcp_packages: list[dict[str, Any]] | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    """Build OpenClaw YAML config dict from platform policy + materialized MCP.

    Model selection is API-only — never written into config.yaml.
    """
    policy = tool_policy or default_tool_policy(company_policy.tool_preset)
    adapter = _api_kind_to_bridge_adapter(api_kind)
    runtime_adapter = adapter if adapter != "platform_openclaw" else "platform_openclaw"

    cfg: dict[str, Any] = {
        "version": 1,
        "runtime": {
            "adapter": runtime_adapter,
        },
        "permissions": tool_policy_to_permissions(policy),
        "tools": {"built_in": True, "mcp": policy.mcp != "deny"},
    }

    turns = max_turns
    if turns is not None:
        cfg.setdefault("runtime", {})["max_turns"] = turns

    if company_policy.max_tokens_per_run is not None:
        cfg.setdefault("runtime", {}).setdefault("budget", {})["max_tokens"] = company_policy.max_tokens_per_run

    servers = mcp_packages_to_openclaw_servers(mcp_packages or [])
    if servers or policy.mcp != "deny":
        mcp_block: dict[str, Any] = {}
        if servers:
            mcp_block["servers"] = servers
        if policy.mcp == "allowlist" and policy.mcp_allowlist:
            mcp_block["allow_servers"] = list(policy.mcp_allowlist)
        cfg["mcp"] = mcp_block

    if provider_key_id:
        provider_block: dict[str, Any] = {"key_ref": provider_key_id}
        dialect = api_kind_to_provider_dialect(api_kind)
        if dialect and runtime_adapter == "platform_openclaw":
            provider_block["dialect"] = dialect
        cfg["provider"] = provider_block

    return cfg


def render_openclaw_config_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def openclaw_config_relative_path() -> str:
    return _OPENCLAW_CONFIG_PATH
