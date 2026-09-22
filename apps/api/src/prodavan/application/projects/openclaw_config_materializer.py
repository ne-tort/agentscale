"""Materialize `.prodavan/config.yaml` for OpenClaw bridge (Batch AS.1 / L14)."""

from __future__ import annotations

from typing import Any

import yaml

from prodavan.application.agent.adapter_kinds import api_kind_to_bridge_adapter
from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.domain.agent import AgentToolPolicy, default_tool_policy
from prodavan.domain.ai_keys import ApiKind

_OPENCLAW_CONFIG_PATH = ".prodavan/config.yaml"

_PROVIDER_DEFAULT_API_KIND: dict[str, str] = {
    "cursor": ApiKind.CURSOR_SDK,
    "codex": ApiKind.CODEX_SDK,
    "claude_code": ApiKind.CLAUDE_AGENT_SDK,
}

# OpenClaw builtin tool ids — canonical MCP names (unified surface: built-ins
# are exposed exclusively as `mcp.openclaw.<bare>` via the stdio MCP server).
# Wildcards so policy presets map to the canonical loop-pool tool names.
_READ_TOOLS = ("mcp.openclaw.fs.read", "mcp.openclaw.fs.list",
               "mcp.openclaw.search.grep", "mcp.openclaw.search.glob")
_WRITE_TOOLS = ("mcp.openclaw.fs.write", "mcp.openclaw.fs.edit")
_SHELL_TOOLS = ("mcp.openclaw.shell.exec",)


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
    """Map AgentToolPolicy preset fields → OpenClaw permissions block.

    Unified MCP surface: built-in tools reach the model as canonical
    `mcp.openclaw.*` names via the stdio MCP server. The bridge
    permission engine auto-allows all `mcp.*` tools (built-in + external
    first-party) in default/acceptEdits modes — no per-name allow-list
    needed. Writing an allow-list would filter out external MCP tools
    (mcp.prodavan-*.*) not enumerated by the policy preset. Only mode +
    deny (plan-mode readonly) are written.
    """
    deny: list[str] = list(policy.extra_deny_tools)

    if policy.sandbox == "strict" and policy.fs_write:
        mode = "plan"
    elif policy.approval == "none" and policy.fs_write and policy.shell_exec:
        mode = "acceptEdits"
    elif policy.approval in {"dangerous_only", "all_tools"}:
        mode = "default"
    else:
        mode = "plan" if not policy.fs_write else "default"

    # Plan mode denies mutating built-ins (permission engine enforces).
    if mode == "plan":
        deny.extend(_WRITE_TOOLS)
        deny.extend(_SHELL_TOOLS)
    elif not policy.fs_write:
        deny.extend(_WRITE_TOOLS)
    if not policy.shell_exec and mode != "plan":
        deny.extend(_SHELL_TOOLS)

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

    deny = _dedupe(deny)

    perms: dict[str, Any] = {"mode": mode}
    if deny:
        perms["deny"] = deny
    return perms


def filter_mcp_packages_by_policy(
    packages: list[dict[str, Any]],
    policy: AgentToolPolicy,
) -> list[dict[str, Any]]:
    """Apply MCP allowlist to package list before any rendering (CLAW-P1b).

    The same filtered list feeds ``mcp.json`` and the OpenClaw ``servers``
    map so the two config formats stay consistent. A deny preset returns an
    empty list; ``manifest_only`` passes everything through; ``allowlist``
    keeps only packages whose ``name`` is in the allowlist.
    """
    if policy.mcp == "deny":
        return []
    if policy.mcp == "manifest_only":
        return list(packages)
    if policy.mcp == "allowlist":
        allowed = set(policy.mcp_allowlist)
        return [
            p
            for p in packages
            if isinstance(p, dict) and str(p.get("name") or "") in allowed
        ]
    return list(packages)


def build_openclaw_config(
    *,
    company_policy: CompanyAgentRuntimePolicy,
    tool_policy: AgentToolPolicy | None = None,
    api_kind: str | None = None,
    provider_key_id: str | None = None,
    provider_endpoint: dict[str, Any] | None = None,
    mcp_packages: list[dict[str, Any]] | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    """Build OpenClaw YAML config dict from platform policy + materialized MCP.

    Model selection is API-only — never written into config.yaml.

    MCP packages are filtered by ``tool_policy`` *before* rendering so the
    OpenClaw ``servers`` map and the ``mcp.json`` file (filtered upstream)
    agree on which servers exist. ``allow_servers`` is kept as a defensive
    allowlist for runtimes that also enforce it server-side.
    """
    policy = tool_policy or default_tool_policy(company_policy.tool_preset)
    adapter = api_kind_to_bridge_adapter(api_kind)
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

    filtered_packages = filter_mcp_packages_by_policy(mcp_packages or [], policy)
    servers = mcp_packages_to_openclaw_servers(filtered_packages)
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
        # Write the resolved HTTP endpoint (from the ai.http_providers catalog)
        # into config.yaml so the bridge reaches the actual provider endpoint
        # (cheapai.lol / ollama / ...) instead of the env default (api.openai.com).
        # Only meaningful for the platform_openclaw (HTTP) adapter.
        if provider_endpoint and runtime_adapter == "platform_openclaw":
            base_url = provider_endpoint.get("base_url")
            if base_url:
                provider_block["base_url"] = str(base_url).rstrip("/")
                auth_scheme = provider_endpoint.get("auth_scheme")
                if auth_scheme:
                    provider_block["auth_scheme"] = str(auth_scheme)
                chat_path = provider_endpoint.get("chat_completions_path")
                if chat_path:
                    provider_block["chat_completions_path"] = str(chat_path)
                models_path = provider_endpoint.get("models_path")
                if models_path:
                    provider_block["models_path"] = str(models_path)
        cfg["provider"] = provider_block

    return cfg


def render_openclaw_config_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def openclaw_config_relative_path() -> str:
    return _OPENCLAW_CONFIG_PATH
