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
    # v2.1.0: delete-инструменты; статус позиции агенту не принадлежит
    assert "request_lines_delete" in names
    assert "found_groups_delete" in names
    upsert = next(t for t in listed["result"]["tools"] if t["name"] == "request_lines_upsert")
    assert "status" not in upsert["inputSchema"]["properties"]
    assert (
        mcp_server._handle({"jsonrpc": "2.0", "id": 9, "method": "initialize"})["result"][
            "serverInfo"
        ]["version"]
        == "2.3.0"
    )


def test_tools_list_contains_build_tools() -> None:
    """WAVE10: инструменты сборок и типов комплектующих."""
    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    for name in (
        "equipment_types_list",
        "equipment_builds_list",
        "equipment_builds_get",
        "equipment_builds_upsert",
        "equipment_builds_delete",
    ):
        assert name in names
    # found_groups_upsert теперь умеет слоты сборки
    group = next(t for t in listed["result"]["tools"] if t["name"] == "found_groups_upsert")
    props = group["inputSchema"]["properties"]
    assert "build_id" in props and "slot_type_id" in props


def test_delete_tools_call_pod_delete(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    calls: list[tuple] = []

    def fake_http(method, path, payload=None, *, session_id=None):
        calls.append((method, path))
        return {"deleted": True}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        for i, (tool, row) in enumerate(
            [("request_lines_delete", "l1"), ("found_groups_delete", "g1")], start=10
        ):
            resp = mcp_server._handle(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {"row_id": row}},
                }
            )
            assert resp is not None
            assert resp["result"].get("isError") is not True
    assert calls == [
        ("DELETE", "/projects/proj-1/modules/mod_equipment/data/request_lines/l1"),
        ("DELETE", "/projects/proj-1/modules/mod_equipment/data/found_groups/g1"),
    ]


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


def test_found_groups_create_requires_owner_and_keys(monkeypatch) -> None:
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

    # владелец есть, но без ключей группы — тоже ошибка
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


def test_found_groups_build_slot_requires_slot_type(monkeypatch) -> None:
    """Слот сборки: build_id без slot_type_id — ошибка; с ним — POST."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "found_groups_upsert",
                "arguments": {"build_id": "b1", "part_number": "X"},
            },
        }
    )
    assert resp["result"].get("isError") is True
    assert "slot_type_id" in resp["result"]["content"][0]["text"]

    posted: dict = {}

    def fake_http(method, path, payload=None, *, session_id=None):
        posted.update(payload or {})
        return {"row_id": "g9"}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        ok = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "found_groups_upsert",
                    "arguments": {
                        "build_id": "b1",
                        "slot_type_id": "etype_cpu",
                        "part_number": "X",
                        "match_kind": "exact",
                    },
                },
            }
        )
    assert ok["result"].get("isError") is not True
    assert posted["body"]["build_id"] == "b1"
    assert posted["body"]["slot_type_id"] == "etype_cpu"


def test_equipment_builds_upsert_and_delete(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    # создание без line_id — ошибка
    bad = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "equipment_builds_upsert",
                "arguments": {"name": "Intel сборка"},
            },
        }
    )
    assert bad["result"].get("isError") is True
    assert "line_id" in bad["result"]["content"][0]["text"]

    calls: list[tuple] = []

    def fake_http(method, path, payload=None, *, session_id=None):
        calls.append((method, path, payload))
        return {"row_id": "b1"}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        ok = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "equipment_builds_upsert",
                    "arguments": {
                        "name": "Intel сборка",
                        "line_id": "line-1",
                        "build_kind": "pc",
                    },
                },
            }
        )
        assert ok["result"].get("isError") is not True
        mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {"name": "equipment_builds_delete", "arguments": {"row_id": "b1"}},
            }
        )
    assert calls[0][0] == "POST"
    assert calls[0][2]["body"]["line_id"] == "line-1"
    assert calls[1][0] == "DELETE"
    assert calls[1][1].endswith("/equipment_builds/b1")


def test_equipment_builds_upsert_rejects_bad_kind(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")
    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {
                "name": "equipment_builds_upsert",
                "arguments": {"name": "X", "line_id": "l1", "build_kind": "workstation"},
            },
        }
    )
    assert resp["result"].get("isError") is True
    assert "build_kind" in resp["result"]["content"][0]["text"]


def test_equipment_types_list_filters_by_build_kind(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    rows = [
        {"row_id": "etype_cpu", "body": {"name": "CPU", "build_scope": "all"}},
        {"row_id": "etype_bmc", "body": {"name": "BMC", "build_scope": "server"}},
    ]
    with patch.object(mcp_server, "_list_rows", return_value=rows):
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": "tools/call",
                "params": {"name": "equipment_types_list", "arguments": {"build_kind": "pc"}},
            }
        )
    items = json.loads(resp["result"]["content"][0]["text"])["items"]
    ids = {r["row_id"] for r in items}
    assert "etype_cpu" in ids
    assert "etype_bmc" not in ids


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


def test_tools_list_applies_ui_overrides(monkeypatch) -> None:
    """mcp_tool_overrides (UI): description заменяет, extra_instructions дописывает."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    rows = [
        {"row_id": "r1", "body": {
            "tool": "equipment_catalog_search",
            "description": "Мой поиск по каталогу.",
            "extra_instructions": "Всегда указывай src_hash.",
            "enabled": True,
        }},
        {"row_id": "r2", "body": {
            "tool": "request_lines_list",
            "description": "Не используй.",
            "enabled": False,  # выключен — игнор
        }},
    ]

    with patch.object(mcp_server, "_list_rows", return_value=rows):
        resp = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = resp["result"]["tools"]
    search = next(t for t in tools if t["name"] == "equipment_catalog_search")
    assert search["description"].startswith("Мой поиск по каталогу.")
    assert "Всегда указывай src_hash." in search["description"]
    # выключенный оверрайд не применяется
    rl = next(t for t in tools if t["name"] == "request_lines_list")
    assert "Не используй" not in rl["description"]


def test_tools_list_survives_override_fetch_failure(monkeypatch) -> None:
    """Сбой чтения оверрайдов не ломает tools/list (дефолтные описания)."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    def _boom(*a, **k):  # noqa: ANN001, ANN002
        raise RuntimeError("bridge down")

    with patch.object(mcp_server, "_list_rows", side_effect=_boom):
        resp = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "equipment_catalog_search" in names


def test_found_groups_patch_build_id_requires_slot_type(monkeypatch) -> None:
    """PATCH с build_id без slot_type_id отклоняется (фантомный слот)."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "found_groups_upsert",
                "arguments": {"row_id": "g1", "build_id": "b1"},
            },
        }
    )
    assert resp["result"].get("isError") is True
    assert "slot_type_id" in resp["result"]["content"][0]["text"]

    # с slot_type_id — проходит
    with patch.object(mcp_server, "_http", return_value={"row_id": "g1"}) as http:
        ok = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "found_groups_upsert",
                    "arguments": {"row_id": "g1", "build_id": "b1", "slot_type_id": "etype_cpu"},
                },
            }
        )
    assert ok["result"].get("isError") is not True
    assert http.call_args.args[0] == "PATCH"
