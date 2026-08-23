"""Unit tests — MCP policy filter."""

from prodavan.application.agent.policy_service import filter_mcp_servers
from prodavan.domain.agent import AgentToolPolicy


def test_mcp_manifest_only_keeps_packages() -> None:
    servers = {"packages": [{"name": "pkg-a"}, {"name": "pkg-b"}]}
    policy = AgentToolPolicy(mcp="manifest_only")
    out = filter_mcp_servers(servers, policy)
    assert len(out["packages"]) == 2


def test_mcp_allowlist_filters() -> None:
    servers = {"packages": [{"name": "pkg-a"}, {"name": "pkg-b"}]}
    policy = AgentToolPolicy(mcp="allowlist", mcp_allowlist=("pkg-a",))
    out = filter_mcp_servers(servers, policy)
    assert len(out["packages"]) == 1
    assert out["packages"][0]["name"] == "pkg-a"
