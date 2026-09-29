"""Unit tests — natural markdown tables for tabular chat attachments (wave 7)."""

from __future__ import annotations

from prodavan.application.content.tabular_json import (
    records_to_markdown_table,
    tabular_bytes_to_json,
)
from prodavan.application.projects.attachment_delivery import (
    DeliveredAttachment,
    compose_agent_message,
)


def _artifact_csv() -> bytes:
    # Reproduces the user's file: a lone "@dropdown" template marker row above
    # the real header, 25 columns wide with only 5 used.
    rows = [
        "@dropdown" + "," * 24,
        ",№,Партномер,Наименование,Кол-во" + "," * 20,
        ",1,P12688-B21,RAID Controller HP SR416i-a,1" + "," * 20,
        ",2,867592-B21,HPE 4TB SATA SSD,2" + "," * 20,
    ]
    return ("\ufeff" + "\n".join(rows) + "\n").encode("utf-8")


def test_artifact_row_skipped_and_real_header_used() -> None:
    res = tabular_bytes_to_json(_artifact_csv(), filename="spec.csv")
    assert res.columns == ["№", "Партномер", "Наименование", "Кол-во"]
    assert res.row_count == 2
    assert res.records[0]["Партномер"] == "P12688-B21"
    # The artifact key and the 20 padding columns are gone.
    assert "@dropdown" not in res.columns
    assert not any(c.startswith("col_") for c in res.columns)


def test_fully_empty_columns_trimmed() -> None:
    raw = b"a,b,c\n1,,x\n2,,y\n"
    res = tabular_bytes_to_json(raw, filename="t.csv")
    assert res.columns == ["a", "c"]


def test_single_column_csv_keeps_first_row_header() -> None:
    res = tabular_bytes_to_json(b"value\n1\n2\n", filename="one.csv")
    assert res.columns == ["value"]
    assert res.row_count == 2


def test_records_to_markdown_table_gfm() -> None:
    md = records_to_markdown_table(
        [{"№": "1", "Партномер": "P12688-B21", "Кол-во": "1"}],
        ["№", "Партномер", "Кол-во"],
    )
    lines = md.split("\n")
    assert lines[0] == "| № | Партномер | Кол-во |"
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == "| 1 | P12688-B21 | 1 |"


def test_markdown_cell_escapes_pipes_and_newlines() -> None:
    md = records_to_markdown_table([{"txt": "a|b\nc"}], ["txt"])
    assert "a\\|b c" in md


def test_inline_table_compose_markdown_not_json() -> None:
    items = [
        DeliveredAttachment(
            filename="rows.csv",
            storage_ref="object://x",
            kind="inline_table",
            workspace_path=None,
            row_count=1,
            records=[{"a": "1"}],
            note="таблица, 1 строк",
            markdown="| a |\n| --- |\n| 1 |",
        )
    ]
    agent = compose_agent_message(user_text="обработай", items=items)
    assert "```json" not in agent
    assert "| a |" in agent
    assert "обработай" in agent
    ui = items[0].ui_dict()
    assert ui["inline_markdown"] == "| a |\n| --- |\n| 1 |"
    assert ui["kind"] == "inline_table"


def test_artifact_csv_full_pipeline_markdown() -> None:
    res = tabular_bytes_to_json(_artifact_csv(), filename="spec.csv")
    md = records_to_markdown_table(res.records, res.columns)
    assert md.split("\n")[0] == "| № | Партномер | Наименование | Кол-во |"
    assert "| 2 | 867592-B21 | HPE 4TB SATA SSD | 2 |" in md
