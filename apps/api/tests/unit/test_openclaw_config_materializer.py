"""Unit tests — OpenClaw config materializer (AS.1)."""

from __future__ import annotations

import yaml

from prodavan.application.projects.openclaw_config_materializer import (
    api_kind_to_provider_dialect,
    build_openclaw_config,
    filter_mcp_packages_by_policy,
    mcp_packages_to_openclaw_servers,
    openclaw_config_relative_path,
    provider_to_default_api_kind,
    render_openclaw_config_yaml,
    tool_policy_to_permissions,
)
from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.domain.agent import AgentToolPolicy, default_tool_policy


def test_filter_mcp_packages_by_policy_allowlist() -> None:
    packages = [
        {"name": "echo", "command": "node", "args": []},
        {"name": "github", "command": "node", "args": []},
        {"name": "secret", "command": "node", "args": []},
    ]
    policy = AgentToolPolicy(mcp="allowlist", mcp_allowlist=("echo", "github"))
    filtered = filter_mcp_packages_by_policy(packages, policy)
    assert {p["name"] for p in filtered} == {"echo", "github"}


def test_filter_mcp_packages_by_policy_deny_returns_empty() -> None:
    policy = AgentToolPolicy(mcp="deny")
    filtered = filter_mcp_packages_by_policy(
        [{"name": "echo", "command": "node", "args": []}],
        policy,
    )
    assert filtered == []


def test_filter_mcp_packages_by_policy_manifest_only_passes_through() -> None:
    policy = AgentToolPolicy(mcp="manifest_only")
    packages = [{"name": "echo", "command": "node", "args": []}]
    assert filter_mcp_packages_by_policy(packages, policy) == packages


def test_build_openclaw_config_servers_match_allowlist() -> None:
    # CLAW-P1b: mcp.json packages and OpenClaw servers map must both honor the
    # company allowlist. A package not in the allowlist must not appear in
    # either config surface.
    policy = AgentToolPolicy(mcp="allowlist", mcp_allowlist=("echo",))
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_full"),
        tool_policy=policy,
        mcp_packages=[
            {"name": "echo", "command": "node", "args": []},
            {"name": "secret", "command": "node", "args": []},
        ],
    )
    assert set(cfg["mcp"]["servers"]) == {"echo"}
    assert "secret" not in cfg["mcp"]["servers"]


def test_build_openclaw_config_deny_preset_omits_servers() -> None:
    policy = AgentToolPolicy(mcp="deny")
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_full"),
        tool_policy=policy,
        mcp_packages=[{"name": "echo", "command": "node", "args": []}],
    )
    assert "servers" not in cfg.get("mcp", {})



def test_mcp_packages_to_openclaw_servers() -> None:
    servers = mcp_packages_to_openclaw_servers(
        [
            {
                "name": "echo",
                "command": "node",
                "args": ["packages/echo/server.mjs"],
                "env": {"PRODAVAN_PROJECT_ID": "${PRODAVAN_PROJECT_ID}"},
            }
        ]
    )
    assert servers["echo"]["command"] == "node"
    assert servers["echo"]["args"] == ["${WORKSPACE}/packages/echo/server.mjs"]
    assert servers["echo"]["env"] == {"PRODAVAN_PROJECT_ID": "${PRODAVAN_PROJECT_ID}"}


def test_chat_readonly_preset_permissions() -> None:
    perms = tool_policy_to_permissions(default_tool_policy("chat_readonly"))
    assert perms["mode"] == "plan"
    # Unified MCP surface: no allow-list (auto-allow for mcp.*). Plan mode
    # denies mutating built-ins via canonical mcp.openclaw.* names.
    assert "allow" not in perms
    assert "mcp.openclaw.shell.exec" in perms.get("deny", [])
    assert "mcp.openclaw.fs.write" in perms.get("deny", [])


def test_workspace_dev_preset_permissions() -> None:
    perms = tool_policy_to_permissions(default_tool_policy("workspace_dev"))
    # default mode: all mcp.* tools auto-allowed (built-in + external).
    # No allow/ask/deny lists written — permission engine handles it.
    assert perms["mode"] == "default"
    assert "allow" not in perms
    assert "ask" not in perms


def test_build_openclaw_config_never_writes_model_block() -> None:
    for api_kind in ("cursor_sdk", "openrouter", "codex_sdk"):
        cfg = build_openclaw_config(
            company_policy=CompanyAgentRuntimePolicy(
                tool_preset="workspace_dev",
                model_allowlist=["gpt-4o-mini"],
            ),
            api_kind=api_kind,
        )
        assert "model" not in cfg


def test_build_openclaw_config_golden_shape() -> None:
    company = CompanyAgentRuntimePolicy(
        tool_preset="workspace_dev",
        model_allowlist=["gpt-4o-mini"],
        max_tokens_per_run=12,
    )
    cfg = build_openclaw_config(
        company_policy=company,
        api_kind="cursor_sdk",
        max_turns=12,
        mcp_packages=[{"name": "echo", "command": "node", "args": ["packages/echo/x.mjs"]}],
        provider_key_id="key_abc",
    )
    assert cfg["runtime"]["adapter"] == "cursor_sdk"
    assert "model" not in cfg
    assert cfg["runtime"]["max_turns"] == 12
    assert cfg["provider"]["key_ref"] == "key_abc"
    assert "echo" in cfg["mcp"]["servers"]

    text = render_openclaw_config_yaml(cfg)
    roundtrip = yaml.safe_load(text)
    assert roundtrip["version"] == 1
    assert openclaw_config_relative_path() == ".prodavan/config.yaml"


def test_platform_openclaw_adapter_for_http_providers() -> None:
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_dev"),
        api_kind="openrouter",
    )
    assert cfg["runtime"]["adapter"] == "platform_openclaw"
    assert "model" not in cfg


def test_mcp_allowlist_on_config() -> None:
    policy = AgentToolPolicy(mcp="allowlist", mcp_allowlist=("echo", "github"))
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_full"),
        tool_policy=policy,
        mcp_packages=[{"name": "echo", "command": "node", "args": []}],
    )
    assert cfg["mcp"]["allow_servers"] == ["echo", "github"]


def test_build_openclaw_config_agents_block_canonical_names() -> None:
    # Unified MCP-only loop pool: subagent profiles use canonical
    # `mcp.openclaw.*` names; `default` omits tools → full parent-pool
    # inheritance (external MCP servers included).
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_dev"),
    )
    agents = cfg["agents"]
    assert agents["default"]["description"]
    assert "tools" not in agents["default"]
    assert set(agents["readonly"]["tools"]) == {
        "mcp.openclaw.fs.read",
        "mcp.openclaw.fs.list",
        "mcp.openclaw.search.grep",
        "mcp.openclaw.search.glob",
    }
    assert "mcp.openclaw.fs.write" in agents["implement"]["tools"]
    assert "mcp.openclaw.fs.edit" in agents["implement"]["tools"]


def test_build_openclaw_config_agents_implement_drops_denied_writes() -> None:
    # chat_readonly denies fs.write via the permissions deny list — the
    # implement profile must degrade to the readonly tool set.
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="chat_readonly"),
    )
    agents = cfg["agents"]
    assert "mcp.openclaw.fs.write" not in agents["implement"]["tools"]
    assert "mcp.openclaw.fs.edit" not in agents["implement"]["tools"]
    assert set(agents["implement"]["tools"]) == set(agents["readonly"]["tools"])


def test_provider_to_default_api_kind() -> None:
    assert provider_to_default_api_kind("cursor") == "cursor_sdk"
    assert provider_to_default_api_kind("codex") == "codex_sdk"
    assert provider_to_default_api_kind("claude_code") == "claude_agent_sdk"
    assert provider_to_default_api_kind(None) is None


def test_api_kind_to_provider_dialect() -> None:
    assert api_kind_to_provider_dialect("openrouter") == "openai_compat"
    assert api_kind_to_provider_dialect("xai_oauth") == "openai_compat"
    assert api_kind_to_provider_dialect("anthropic_api") == "anthropic_messages"
    assert api_kind_to_provider_dialect("cursor_sdk") is None


def test_openrouter_config_includes_dialect() -> None:
    cfg = build_openclaw_config(
        company_policy=CompanyAgentRuntimePolicy(tool_preset="workspace_dev"),
        api_kind="openrouter",
        provider_key_id="key_or",
    )
    assert cfg["runtime"]["adapter"] == "platform_openclaw"
    assert cfg["provider"]["dialect"] == "openai_compat"
    assert "model" not in cfg
