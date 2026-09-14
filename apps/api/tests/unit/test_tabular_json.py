"""Unit tests — tabular → JSON for chat attachments."""

from __future__ import annotations

from prodavan.application.content.tabular_json import records_to_json_bytes, tabular_bytes_to_json
from prodavan.application.projects.attachment_delivery import (
    DeliveredAttachment,
    compose_agent_message,
    compose_display_text,
)
from prodavan.domain.projects import CHAT_INLINE_TABULAR_ROW_LIMIT


def test_csv_to_json_records() -> None:
    raw = "name;qty\nАккумулятор;2\nКабель;10\n".encode("utf-8")
    result = tabular_bytes_to_json(raw, filename="spec.csv")
    assert result.row_count == 2
    assert result.records[0]["name"] == "Аккумулятор"
    assert result.records[1]["qty"] == "10"


def test_xml_tree_to_single_record() -> None:
    raw = b"<root><item id=\"1\">hello</item></root>"
    result = tabular_bytes_to_json(raw, filename="note.xml")
    assert result.row_count == 1
    assert "item" in result.records[0]


def test_inline_compose_includes_json_not_in_display() -> None:
    items = [
        DeliveredAttachment(
            filename="rows.csv",
            storage_ref="object://x",
            kind="inline_json",
            workspace_path=None,
            row_count=1,
            records=[{"a": "1"}],
            note="inlined",
        )
    ]
    agent = compose_agent_message(user_text="обработай", items=items)
    assert "```json" in agent
    assert '"a": "1"' in agent or '"a":"1"' in agent.replace(" ", "")
    assert "обработай" in agent
    display = compose_display_text(user_text="обработай", items=items)
    assert display == "обработай"
    assert "json" not in display.lower()


def test_workspace_path_compose() -> None:
    items = [
        DeliveredAttachment(
            filename="big.csv",
            storage_ref="object://y",
            kind="workspace_file",
            workspace_path="inbox/big.json",
            row_count=CHAT_INLINE_TABULAR_ROW_LIMIT + 1,
            records=None,
            note="converted csv → JSON",
        )
    ]
    agent = compose_agent_message(user_text="", items=items)
    assert "/workspace/inbox/big.json" in agent
    display = compose_display_text(user_text="", items=items)
    assert display == "Вложение: big.csv"


def test_records_to_json_bytes_roundtrip() -> None:
    payload = records_to_json_bytes([{"x": "1"}], indent=None)
    assert payload == b'[{"x": "1"}]'
