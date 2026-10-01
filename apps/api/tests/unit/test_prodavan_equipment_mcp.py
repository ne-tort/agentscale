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
    # WAVE7: агент пишет группы, не офферы
    assert "found_groups_upsert" in names
    assert "found_groups_list" in names
    assert not any(n.startswith("found_offers_") for n in names)
    assert (
        mcp_server._handle({"jsonrpc": "2.0", "id": 9, "method": "initialize"})["result"][
            "serverInfo"
        ]["version"]
        == "2.0.0"
    )


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


def test_found_groups_upsert_posts_row(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    posted: dict = {}

    def fake_http(method, path, payload=None, *, session_id=None):
        if method == "POST":
            posted.update(payload or {})
            return {"row_id": "g1", "body": (payload or {}).get("body")}
        return {}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "found_groups_upsert",
                    "arguments": {
                        "line_id": "line-1",
                        "part_number": "LC1D09",
                        "aliases_pn": "LC1 D09",
                        "match_kind": "exact",
                        "note": "точное совпадение по ПН",
                    },
                },
            }
        )
    assert resp is not None
    assert resp["result"].get("isError") is not True
    assert posted["body"]["line_id"] == "line-1"
    assert posted["body"]["part_number"] == "LC1D09"
    assert posted["body"]["match_kind"] == "exact"


def test_found_groups_create_requires_line_id_and_keys(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "found_groups_upsert",
                "arguments": {"part_number": "LC1D09"},
            },
        }
    )
    assert resp is not None
    assert resp["result"].get("isError") is True
    assert "line_id" in resp["result"]["content"][0]["text"]

    # line_id без ключей группы — тоже ошибка
    resp2 = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "found_groups_upsert",
                "arguments": {"line_id": "line-1", "note": "без ключей"},
            },
        }
    )
    assert resp2 is not None
    assert resp2["result"].get("isError") is True
    assert "part_number" in resp2["result"]["content"][0]["text"]


def test_found_groups_upsert_rejects_bad_match_kind(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "found_groups_upsert",
                "arguments": {
                    "line_id": "line-1",
                    "part_number": "LC1D09",
                    "match_kind": "maybe",
                },
            },
        }
    )
    assert resp is not None
    assert resp["result"].get("isError") is True
    assert "match_kind" in resp["result"]["content"][0]["text"]


def test_found_groups_tool_docs_teach_selection() -> None:
    """Инструкции ИИ: группы — выбор кандидатов; офферы пишет платформа."""
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "found_groups_upsert")
    desc = tool["description"].lower()
    assert "line_id" in desc
    assert "request_lines.row_id" in tool["description"]
    assert "doubt" in desc
    assert "alias" in desc
    assert "src_hash" in desc
    assert "found_offers" in desc
    search_tool = next(t for t in mcp_server.TOOLS if t["name"] == "equipment_catalog_search")
    assert "src_hash" in search_tool["description"]
