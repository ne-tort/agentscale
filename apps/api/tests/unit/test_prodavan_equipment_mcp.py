"""Smoke tests for prodavan-equipment MCP."""

from __future__ import annotations

import json
from pathlib import Path
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


def test_catalog_search_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    import sqlite3

    db = catalogs / "catalog.sqlite"
    conn = sqlite3.connect(db.as_posix())
    conn.execute(
        'CREATE TABLE rows ("title" TEXT, "price" TEXT, "part_number" TEXT, '
        '"brand" TEXT, "supplier" TEXT, "lead_time" TEXT, "source_catalog" TEXT)'
    )
    conn.execute(
        "INSERT INTO rows VALUES (?,?,?,?,?,?,?)",
        ("SSD", "10", "SSD-1", "B", "S", "1", "loc"),
    )
    conn.commit()
    conn.close()

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
    assert "Mouse" in resp["result"]["content"][0]["text"] or "o1" in resp["result"]["content"][0]["text"]
    assert http.call_args_list[0][0][0] == "POST"


def test_request_lines_upsert_requires_title(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")
    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "request_lines_upsert", "arguments": {"part_number": "X"}},
        }
    )
    assert resp is not None
    assert resp["result"].get("isError") is True
    assert "title" in resp["result"]["content"][0]["text"].lower()
