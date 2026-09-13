"""MCP prodavan-equipment — catalog search via Pod HTTP (no SQLite)."""

from __future__ import annotations

import json
from unittest.mock import patch

from prodavan.application.mcp.prodavan_equipment_mcp import server as mcp_server


def test_tools_list_contains_catalog_and_sot_tools() -> None:
    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "equipment_catalog_search" in names
    assert "equipment_catalog_sources" in names
    assert "request_lines_upsert" in names
    assert "found_offers_upsert" in names


def test_catalog_search_calls_pod_api(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    fake = {
        "items": [
            {
                "part_number": "SSD-1",
                "title": "SSD",
                "brand": "B",
                "price": "10",
                "supplier": "S",
                "lead_time": "1",
                "catalog_id": "c1",
                "source_catalog": "loc",
                "match_rank": "exact_pn",
                "in_stock": True,
            }
        ],
        "total": 1,
    }
    with patch.object(mcp_server, "_http", return_value=fake) as http:
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "equipment_catalog_search",
                    "arguments": {"part_number": "SSD-1", "limit": 5},
                },
            }
        )
    assert resp is not None
    assert resp["result"].get("isError") is not True
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["items"][0]["part_number"] == "SSD-1"
    http.assert_called_once()
    args = http.call_args
    assert args.args[0] == "POST"
    assert "catalog-search" in args.args[1]


def test_catalog_sources_calls_pod_api(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")
    with patch.object(
        mcp_server, "_http", return_value={"items": [{"id": "c1", "name": "A", "kind": "local"}]}
    ) as http:
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "equipment_catalog_sources", "arguments": {}},
            }
        )
    assert resp is not None
    assert resp["result"].get("isError") is not True
    http.assert_called_once()
    assert http.call_args.args[0] == "GET"
    assert "catalog-sources" in http.call_args.args[1]


def test_found_offers_upsert_validates_and_http(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    with patch.object(mcp_server, "_http", return_value={"row_id": "o1"}) as http:
        with patch.object(mcp_server, "_list_rows", return_value=[]):
            resp = mcp_server._handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "found_offers_upsert",
                        "arguments": {
                            "title": "Mouse",
                            "line_id": "line-1",
                            "match_kind": "exact",
                        },
                    },
                }
            )
    assert resp is not None
    assert resp["result"].get("isError") is not True
    assert http.called
