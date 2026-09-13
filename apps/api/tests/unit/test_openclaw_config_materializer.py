"""Unit tests — OpenClaw config materializer (AS.1)."""

from __future__ import annotations

import yaml

from prodavan.application.projects.openclaw_config_materializer import (
    api_kind_to_provider_dialect,
    build_openclaw_config,
    mcp_packages_to_openclaw_servers,
    openclaw_config_relative_path,
    provider_to_default_api_kind,
    render_openclaw_config_yaml,
    tool_policy_to_permissions,
)
from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.domain.agent import AgentToolPolicy, default_tool_policy


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
    assert "fs.read" in perms["allow"]
    assert "shell.exec" in perms.get("deny", [])


def test_workspace_dev_preset_permissions() -> None:
    perms = tool_policy_to_permissions(default_tool_policy("workspace_dev"))
    assert "fs.edit" in perms["allow"]
    assert "shell.exec" in perms.get("ask", [])


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


def test_provider_to_default_api_kind() -> None:
    assert provider_to_default_api_kind("cursor") == "cursor_sdk"
    assert provider_to_default_api_kind("codex") == "codex_sdk"
    assert provider_to_default_api_kind("claude_code") == "claude_agent_sdk"
    assert provider_to_default_api_kind(None) is None


def test_api_kind_to_provider_dialect() -> None:
    assert api_kind_to_provider_dialect("openrouter") == "openai_compat"
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
