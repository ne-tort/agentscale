"""MCP-аннотации: read-only инструменты отличимы от пишущих.

Клиент (SDK/UI агента) по `readOnlyHint` решает, можно ли звать инструмент
без подтверждения и в автономном цикле поиска. Без аннотаций все инструменты
выглядят одинаково, и модель не может отличить безопасное чтение от записи.
"""

from __future__ import annotations

from prodavan.application.mcp.prodavan_equipment_mcp import server as mcp


def _tools() -> dict[str, dict]:
    return {t["name"]: t for t in mcp._tools_with_overrides()}


def test_every_tool_has_annotations() -> None:
    for name, tool in _tools().items():
        assert "annotations" in tool, name
        ann = tool["annotations"]
        assert "readOnlyHint" in ann, name
        assert isinstance(ann["readOnlyHint"], bool), name


def test_read_only_tools_marked() -> None:
    tools = _tools()
    for name in (
        "web_shops_list",
        "web_products_list",
        "mail_profiles_list",
        "tender_readiness",
        "tender_get",
        "equipment_catalog_search",
    ):
        assert tools[name]["annotations"]["readOnlyHint"] is True, name
        # чтение не может быть destructive
        assert tools[name]["annotations"]["destructiveHint"] is False, name


def test_write_tools_not_read_only() -> None:
    tools = _tools()
    for name in (
        "web_product_add",
        "web_product_delete",
        "tender_report_send",
        "tender_upsert",
        "request_lines_upsert",
    ):
        assert tools[name]["annotations"]["readOnlyHint"] is False, name


def test_deletions_are_destructive() -> None:
    tools = _tools()
    for name in ("web_product_delete", "tender_delete", "found_groups_delete"):
        assert tools[name]["annotations"]["destructiveHint"] is True, name


def test_upserts_are_idempotent() -> None:
    tools = _tools()
    for name in ("web_product_add", "tender_upsert", "request_lines_upsert"):
        assert tools[name]["annotations"]["idempotentHint"] is True, name


def test_unknown_tool_defaults_to_write() -> None:
    """Инструмент вне списков считается пишущим — безопасный дефолт."""
    ann = mcp._with_annotations({"name": "some_new_tool"})["annotations"]
    assert ann["readOnlyHint"] is False


def test_open_world_for_external_io() -> None:
    """Инструменты, ходящие наружу (интернет), помечены openWorld."""
    tools = _tools()
    assert tools["web_product_add"]["annotations"]["openWorldHint"] is True
    # каталог — закрытый мир (данные проекта)
    assert tools["equipment_catalog_search"]["annotations"]["openWorldHint"] is False
