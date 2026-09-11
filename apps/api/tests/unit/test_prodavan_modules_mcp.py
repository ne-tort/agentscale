"""Smoke test for prodavan-modules MCP stdio protocol."""

from __future__ import annotations

from unittest.mock import patch

from prodavan.application.mcp.prodavan_modules_mcp import server as mcp_server


def test_tools_list_and_modules_list_http(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "modules_list" in names
    assert "module_meta_put" in names

    with patch.object(mcp_server, "_http", return_value={"items": [{"module_id": "mod-a"}]}) as http:
        called = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "modules_list", "arguments": {}},
            }
        )
        http.assert_called_once_with("GET", "/projects/proj-1/modules")
    assert called is not None
    assert called["result"]["content"][0]["type"] == "text"
    assert "mod-a" in called["result"]["content"][0]["text"]
