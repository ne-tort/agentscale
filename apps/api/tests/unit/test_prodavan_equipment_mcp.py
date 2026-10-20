"""MCP prodavan-equipment — catalog search via Pod HTTP (no SQLite)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

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
        == "3.2.0"
    )


def test_found_groups_list_schema_declares_build_id() -> None:
    """Группы слотов сборки не имеют line_id — без build_id в схеме агент
    не может прочитать компоненты копии, сделанной ready_build_attach.

    Обработчик build_id поддерживал и раньше, но в inputSchema его не было,
    а `additionalProperties: False` не давал агенту его передать: на
    `found_groups_list(line_id=…)` приходил пустой список, и копия сборки
    оказывалась невидимой.
    """
    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool = next(t for t in listed["result"]["tools"] if t["name"] == "found_groups_list")
    props = tool["inputSchema"]["properties"]
    assert "build_id" in props
    assert "line_id" in props
    assert tool["inputSchema"].get("additionalProperties") is False
    # описание должно объяснять, что у групп слота нет line_id
    assert "build_id" in tool["description"]


def test_found_groups_list_filters_by_build_id(monkeypatch) -> None:
    """Группы слотов сборки видны по build_id: у них нет line_id."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    rows = [
        {"row_id": "g_line", "body": {"line_id": "line_1", "owner_kind": "line"}},
        {
            "row_id": "g_cpu",
            "body": {"build_id": "b1", "slot_type_id": "etype_cpu", "owner_kind": "build"},
        },
        {
            "row_id": "g_ram",
            "body": {"build_id": "b1", "slot_type_id": "etype_ram", "owner_kind": "build"},
        },
        {"row_id": "g_other", "body": {"build_id": "b2", "owner_kind": "build"}},
    ]

    def call(arguments):
        with patch.object(mcp_server, "_http", return_value={"items": rows}):
            resp = mcp_server._handle(
                {
                    "jsonrpc": "2.0",
                    "id": 21,
                    "method": "tools/call",
                    "params": {"name": "found_groups_list", "arguments": arguments},
                }
            )
        assert resp["result"].get("isError") is not True
        return [r["row_id"] for r in json.loads(resp["result"]["content"][0]["text"])["items"]]

    assert call({"build_id": "b1"}) == ["g_cpu", "g_ram"]
    assert call({"line_id": "line_1"}) == ["g_line"]
    # без фильтра — всё, включая группы сборок
    assert call({}) == ["g_line", "g_cpu", "g_ram", "g_other"]


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
    # WAVE12: слоты сборок агент не пишет — их зеркалит платформа
    group = next(t for t in listed["result"]["tools"] if t["name"] == "found_groups_upsert")
    props = group["inputSchema"]["properties"]
    assert "build_id" not in props and "slot_type_id" not in props


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


def test_found_groups_build_slot_rejected(monkeypatch) -> None:
    """WAVE12: группы слотов сборок пишет только платформа (зеркало готовой
    сборки) — агенту путь сборок отклонён с подсказкой про ready build."""
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
                "arguments": {"part_number": "X", "note": "без line_id"},
            },
        }
    )
    assert resp["result"].get("isError") is True
    assert "line_id" in resp["result"]["content"][0]["text"]
    assert "ready_build" in resp["result"]["content"][0]["text"]

    # обычная позиция — проходит
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
                        "line_id": "line-1",
                        "part_number": "X",
                        "match_kind": "exact",
                    },
                },
            }
        )
    assert ok["result"].get("isError") is not True
    assert posted["body"]["line_id"] == "line-1"


def test_equipment_builds_upsert_and_delete(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    # создание без ready_build_id — ошибка: сборка чата = ссылка на каталог
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
    assert "ready_build_id" in bad["result"]["content"][0]["text"]

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
                        "ready_build_id": "rb-1",
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
    assert calls[0][2]["body"]["ready_build_id"] == "rb-1"
    # slots/slot_qty не принадлежат агенту — их в теле нет
    assert "slots" not in calls[0][2]["body"]
    assert "slot_qty" not in calls[0][2]["body"]
    assert calls[1][0] == "DELETE"
    assert calls[1][1].endswith("/equipment_builds/b1")








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








# ------------------------------------------------- WAVE12: карточки и сборки v2

@pytest.fixture()
def _env(monkeypatch):
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")


def _call(tool: str, arguments: dict):
    return mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )














def test_ready_build_attach_invokes_action(_env) -> None:
    calls: list[tuple] = []

    def fake_http(method, path, payload=None, *, session_id=None):
        calls.append((method, path, payload))
        return {"ok": True, "build_id": "b1"}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        resp = _call(
            "ready_build_attach",
            {"ready_build_id": "rb1", "line_id": "line1", "note": "копия"},
        )
    assert resp["result"].get("isError") is not True
    method, path, payload = calls[0]
    assert method == "POST"
    assert path.endswith("/actions/ready_build_attach/invoke")
    assert payload == {"row_id": "rb1", "params": {"line_id": "line1", "note": "копия"}}

    bad = _call("ready_build_attach", {"ready_build_id": "rb1"})
    assert bad["result"].get("isError") is True
    assert "line_id" in bad["result"]["content"][0]["text"]


def _env_ccc(monkeypatch):
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")
# ------------------------------------------------- WAVE12: карточки и сборки v3

def test_tools_list_contains_v2_tools() -> None:
    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    for name in (
        "part_cards_list",
        "part_card_upsert",
        "part_card_delete",
        "ready_builds_list",
        "ready_build_get",
        "ready_build_upsert",
        "ready_build_slot_upsert",
        "ready_build_attach",
    ):
        assert name in names
    # старая модель (группы совместимости, классы) убрана
    assert "ready_builds_catalog" not in names
    assert "build_group_upsert" not in names
    assert "build_group_item_upsert" not in names
    # прежние инструменты не потеряны
    assert "equipment_builds_delete" in names
    assert "found_groups_upsert" in names
    # WAVE14: тендеры
    for name in (
        "tenders_list",
        "tender_get",
        "tender_upsert",
        "tender_delete",
        "tender_positions_upsert",
        "tender_attach",
    ):
        assert name in names
    # «Интернет магазины» / «Интернет товары» (web_shops + web_products, TTL 72ч)
    for name in (
        "web_shops_list",
        "web_products_list",
        "web_product_add",
        "web_product_delete",
    ):
        assert name in names
    assert len(names) == 33
    assert (
        mcp_server._handle({"jsonrpc": "2.0", "id": 2, "method": "initialize"})["result"][
            "serverInfo"
        ]["version"]
        == "3.2.0"
    )


def test_ready_builds_list_compact_and_filters(_env) -> None:
    """Компактный список: только рабочие поля, фильтры по платформе и enabled."""
    rows = [
        {"row_id": "rb1", "body": {"name": "Офисный ПК", "build_kind": "pc",
                                   "spec_summary": "Процессор: LGA1700",
                                   "price_total": 30000, "is_enabled": True,
                                   "note": "в compact не попадает"}},
        {"row_id": "rb2", "body": {"name": "Сервер old", "build_kind": "server",
                                   "is_enabled": False}},
        {"row_id": "rb3", "body": {"name": "Сервер 2U", "build_kind": "server",
                                   "is_enabled": True}},
    ]
    with patch.object(mcp_server, "_list_rows", return_value=rows):
        resp = _call("ready_builds_list", {})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert [i["row_id"] for i in payload["items"]] == ["rb1", "rb3"]
    item = payload["items"][0]
    assert item["spec_summary"] == "Процессор: LGA1700"
    assert "note" not in item
    assert "body" not in item

    with patch.object(mcp_server, "_list_rows", return_value=rows):
        resp = _call("ready_builds_list", {"build_kind": "server"})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert [i["row_id"] for i in payload["items"]] == ["rb3"]

    with patch.object(mcp_server, "_list_rows", return_value=rows):
        resp = _call("ready_builds_list", {"build_kind": "server", "only_enabled": False})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert sorted(i["row_id"] for i in payload["items"]) == ["rb2", "rb3"]


def test_ready_build_get_returns_slots(_env) -> None:
    builds = [{"row_id": "rb1", "body": {"name": "A", "is_enabled": True}}]
    slots = [
        {"row_id": "s1", "body": {"build_id": "rb1", "type_id": "etype_cpu",
                                  "qty": 1, "constraints": {"socket": "LGA1700"},
                                  "resolved_price": 11000}},
        {"row_id": "s2", "body": {"build_id": "other", "type_id": "etype_ram", "qty": 2}},
    ]

    def fake_list(mid, table, *, session_id=None):
        return builds if table == "ready_builds" else slots

    with patch.object(mcp_server, "_list_rows", side_effect=fake_list):
        resp = _call("ready_build_get", {"row_id": "rb1"})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["build"]["row_id"] == "rb1"
    assert [s["row_id"] for s in payload["slots"]] == ["s1"]
    assert payload["slots"][0]["constraints"] == {"socket": "LGA1700"}

    with patch.object(mcp_server, "_list_rows", side_effect=fake_list):
        bad = _call("ready_build_get", {"row_id": "ghost"})
    assert bad["result"].get("isError") is True
    assert "not found" in bad["result"]["content"][0]["text"]


_TYPE_ROWS = [
    {
        "row_id": "etype_cpu",
        "body": {
            "name": "Процессор",
            "fields_json": [
                {"key": "socket", "label": {"ru": "Сокет"}, "kind": "enum",
                 "required": True, "values": ["AM4", "LGA1700"]},
                {"key": "cores", "label": {"ru": "Ядра"}, "kind": "groups",
                 "required": True, "groups": [{"value": "6", "label": "6 ядер"}]},
            ],
        },
    }
]


def test_part_card_upsert_validates_against_type_template(_env) -> None:
    """Карточка: ключи + обязательные поля типа + значения из групп."""
    resp = _call("part_card_upsert", {"type_id": "etype_cpu"})
    assert resp["result"].get("isError") is True
    assert "part_number" in resp["result"]["content"][0]["text"]

    resp = _call("part_card_upsert", {"part_number": "CPU-A"})
    assert resp["result"].get("isError") is True
    assert "type_id" in resp["result"]["content"][0]["text"]

    with patch.object(mcp_server, "_list_rows", return_value=_TYPE_ROWS):
        resp = _call(
            "part_card_upsert",
            {"type_id": "etype_cpu", "part_number": "CPU-A", "attrs": {"cores": "6"}},
        )
        assert resp["result"].get("isError") is True
        assert "socket" in resp["result"]["content"][0]["text"]

        resp = _call(
            "part_card_upsert",
            {"type_id": "etype_cpu", "part_number": "CPU-A",
             "attrs": {"socket": "LGA9999", "cores": "6"}},
        )
        assert resp["result"].get("isError") is True
        assert "groups" in resp["result"]["content"][0]["text"]

        resp = _call(
            "part_card_upsert",
            {"type_id": "etype_cpu", "part_number": "CPU-A",
             "attrs": {"socket": "AM4", "cores": "6", "zzz": "1"}},
        )
        assert resp["result"].get("isError") is True
        assert "not declared" in resp["result"]["content"][0]["text"]

        posted: dict = {}

        def fake_http(method, path, payload=None, *, session_id=None):
            posted.update(payload or {})
            return {"row_id": "pc1"}

        with patch.object(mcp_server, "_http", side_effect=fake_http):
            ok = _call(
                "part_card_upsert",
                {"type_id": "etype_cpu", "part_number": "CPU-A",
                 "aliases_pn": "CPUA", "attrs": {"socket": "AM4", "cores": "6"}},
            )
        assert ok["result"].get("isError") is not True
        assert posted["body"]["attrs"] == {"socket": "AM4", "cores": "6"}


def test_part_cards_list_filters_by_type_and_constraints(_env) -> None:
    cards = [
        {"row_id": "c1", "body": {"type_id": "etype_storage",
                                  "attrs": {"drive_type": "SSD", "capacity": "512"}}},
        {"row_id": "c2", "body": {"type_id": "etype_storage",
                                  "attrs": {"drive_type": "SSD", "capacity": "256"}}},
        {"row_id": "c3", "body": {"type_id": "etype_cpu",
                                  "attrs": {"socket": "AM4"}}},
    ]
    with patch.object(mcp_server, "_list_rows", return_value=cards):
        resp = _call("part_cards_list", {"type_id": "etype_storage",
                                         "constraints": {"capacity": "512"}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert [i["row_id"] for i in payload["items"]] == ["c1"]
    # регистр значений не важен; обе SSD-карточки подходят
    with patch.object(mcp_server, "_list_rows", return_value=cards):
        resp = _call("part_cards_list", {"constraints": {"drive_type": "ssd"}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert [i["row_id"] for i in payload["items"]] == ["c1", "c2"]


def test_ready_build_upsert_writes_slots_in_one_call(_env) -> None:
    """Сборка + слоты одним вызовом; существующие слоты переиспользуются по
    row_id или типу (идентичность слота стабильна для зеркал чатов)."""
    calls: list[tuple] = []
    existing_slots = [
        {"row_id": "old_cpu", "body": {"build_id": "rb1", "type_id": "etype_cpu"}},
        {"row_id": "old_ram", "body": {"build_id": "rb1", "type_id": "etype_ram"}},
    ]

    def fake_http(method, path, payload=None, *, session_id=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/ready_builds"):
            return {"row_id": "rb1", "body": (payload or {}).get("body")}
        if method == "PATCH" and "/ready_builds/" in path:
            return {"row_id": "rb1", "body": (payload or {}).get("body")}
        return {"row_id": "x"}

    with (
        patch.object(mcp_server, "_http", side_effect=fake_http),
        patch.object(mcp_server, "_list_rows", return_value=existing_slots),
    ):
        resp = _call(
            "ready_build_upsert",
            {
                "row_id": "rb1",
                "name": "Офисный ПК",
                "target_price_max": 50000,
                "slots": [
                    {"type_id": "etype_cpu", "constraints": {"socket": "LGA1700"}, "qty": 1},
                    {"type_id": "etype_storage", "constraints": {"capacity": "512"}},
                ],
            },
        )
    assert resp["result"].get("isError") is not True
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["slots_replaced"] is True
    # старый CPU-слот патчится на месте, RAM удаляется, storage создаётся
    patched = [c for c in calls if c[0] == "PATCH" and "ready_build_slots/old_cpu" in c[1]]
    assert patched and patched[0][2]["body"]["constraints"] == {"socket": "LGA1700"}
    assert (
        "DELETE",
        "/projects/proj-1/modules/mod_equipment/data/ready_build_slots/old_ram",
        None,
    ) in calls
    created = [c for c in calls if c[0] == "POST" and c[1].endswith("/ready_build_slots")]
    assert len(created) == 1
    assert created[0][2]["body"]["build_id"] == "rb1"
    assert created[0][2]["body"]["constraints"] == {"capacity": "512"}


def test_ready_build_upsert_validates_slots(_env) -> None:
    resp = _call("ready_build_upsert", {"name": "X", "slots": [{"qty": 1}]})
    assert resp["result"].get("isError") is True
    assert "type_id" in resp["result"]["content"][0]["text"]

    resp2 = _call(
        "ready_build_upsert",
        {"name": "X", "slots": [{"type_id": "etype_cpu", "qty": 0}]},
    )
    assert resp2["result"].get("isError") is True
    assert "qty" in resp2["result"]["content"][0]["text"]

    resp3 = _call(
        "ready_build_upsert",
        {"name": "X", "slots": [{"type_id": "etype_cpu", "constraints": "ssd"}]},
    )
    assert resp3["result"].get("isError") is True
    assert "constraints" in resp3["result"]["content"][0]["text"]

    resp4 = _call("ready_build_upsert", {"slots": []})
    assert resp4["result"].get("isError") is True
    assert "name" in resp4["result"]["content"][0]["text"]


def test_ready_build_slot_upsert_creates_and_patches_single_slot(monkeypatch) -> None:
    """Патч ОДНОГО слота сборки — без перезаписи всего массива."""
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    posted: list[tuple] = []

    def fake_http(method, path, payload=None, *, session_id=None):
        posted.append((method, path, payload))
        return {"row_id": "slot_new"}

    with patch.object(mcp_server, "_http", side_effect=fake_http):
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "ready_build_slot_upsert",
                    "arguments": {
                        "build_id": "b1",
                        "type_id": "etype_ram",
                        "qty": 2,
                        "constraints": {"ram_type": "DDR4", "module_capacity": "16"},
                    },
                },
            }
        )
        assert resp["result"].get("isError") is not True
        assert posted[-1][0] == "POST"
        assert posted[-1][1].endswith("/ready_build_slots")
        assert posted[-1][2]["body"]["qty"] == 2
        assert posted[-1][2]["body"]["constraints"] == {"ram_type": "DDR4", "module_capacity": "16"}

        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "ready_build_slot_upsert",
                    "arguments": {"row_id": "slot_ram", "card_id": "card_1"},
                },
            }
        )
        assert resp["result"].get("isError") is not True
        assert posted[-1][0] == "PATCH"
        assert posted[-1][1].endswith("/ready_build_slots/slot_ram")
        assert posted[-1][2]["body"] == {"card_id": "card_1"}


def test_ready_build_slot_upsert_validates_qty_and_constraints(monkeypatch) -> None:
    monkeypatch.setenv("PRODAVAN_API_BASE_URL", "http://api.example/api/v1")
    monkeypatch.setenv("PRODAVAN_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PRODAVAN_PROJECT_ID", "proj-1")

    for args, needle in (
        ({"build_id": "b1", "type_id": "etype_ram", "qty": 1.5}, "qty must be a whole number"),
        ({"build_id": "b1"}, "type_id is required"),
        ({"row_id": "slot_x", "qty": 0}, "qty must be a whole number"),
        ({"row_id": "slot_x", "constraints": "ssd"}, "constraints must be an object"),
    ):
        resp = mcp_server._handle(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {"name": "ready_build_slot_upsert", "arguments": args},
            }
        )
        assert resp["result"].get("isError") is True, args
        assert needle in resp["result"]["content"][0]["text"], args
