"""Unit tests for cabinet.* MCP tool registry / bans (L06)."""

from __future__ import annotations

import pytest

from prodavan.application.cabinets.mcp_dispatcher import CabinetMcpDispatcher, list_platform_tools
from prodavan.domain.errors import AppError


def test_list_platform_tools_has_core_set() -> None:
    names = {t["name"] for t in list_platform_tools()}
    assert "cabinet.tables.list" in names
    assert "cabinet.rows.query" in names
    assert "cabinet.info" in names
    # Declared packages tools are implemented
    assert any(t["name"] == "cabinet.mcp_packages.deploy" and t["implemented"] for t in list_platform_tools())
    assert any(t["name"] == "cabinet.bundle.export" and t["implemented"] for t in list_platform_tools())


def test_banned_sql_tool_rejected() -> None:
    with pytest.raises(AppError) as ei:
        CabinetMcpDispatcher._reject_banned("cabinet.sql.execute")
    assert ei.value.code == "MCP_FORBIDDEN_TOOL"


def test_validate_required_args() -> None:
    schema = {
        "type": "object",
        "required": ["table_slug"],
        "properties": {"table_slug": {"type": "string"}, "limit": {"type": "integer"}},
        "additionalProperties": False,
    }
    with pytest.raises(AppError) as ei:
        CabinetMcpDispatcher._validate_required(schema, {})
    assert ei.value.code == "VALIDATION_ERROR"

    with pytest.raises(AppError) as ei2:
        CabinetMcpDispatcher._validate_required(schema, {"table_slug": "x", "extra": 1})
    assert "unknown" in (ei2.value.detail or "")
