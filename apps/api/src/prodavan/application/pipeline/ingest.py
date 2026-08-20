"""Honest ingest: parse csv/txt; xlsx without a parser is not_implemented (no invented rows)."""

from __future__ import annotations

import csv
import io
from pathlib import Path


def ingest_rows(input_path: Path) -> dict:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        return {"parser": "csv", "rows": _parse_csv(input_path.read_text(encoding="utf-8-sig"))}
    if suffix == ".txt":
        return {"parser": "txt", "rows": _parse_txt(input_path.read_text(encoding="utf-8-sig"))}
    return {
        "parser": "not_implemented",
        "parser_status": "not_implemented",
        "note": f"No parser for {suffix or 'unknown'}; rows not invented",
        "rows": [],
    }


def extracted_markdown(filename: str, ingest: dict) -> str:
    if ingest.get("parser_status") == "not_implemented":
        return (
            f"# {filename}\n\n"
            f"parser_status: not_implemented\n\n"
            "Binary spec not extracted. Rows were not invented.\n"
        )
    lines = [f"# {filename}", "", f"parser: {ingest.get('parser')}", ""]
    for row in ingest["rows"]:
        lines.append(f"- {row.get('raw_line', '')}")
    return "\n".join(lines) + "\n"


def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    if reader.fieldnames is None:
        return rows
    for index, cells in enumerate(reader, start=2):
        cleaned = {k: (v or "").strip() for k, v in cells.items() if k}
        raw = " ".join(v for v in cleaned.values() if v)
        if not raw:
            continue
        rows.append({"row_index": index, "cells": cleaned, "raw_line": raw})
    return rows


def _parse_txt(text: str) -> list[dict]:
    rows: list[dict] = []
    for index, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        rows.append({"row_index": index, "cells": {"line": raw}, "raw_line": raw})
    return rows
