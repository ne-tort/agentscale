"""Tabular / spreadsheet bytes → JSON records for chat attachment delivery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from prodavan.application.content.tabular_index import (
    _NS,
    _col_letter_to_index,
    _csv_matrix,
    _decode_csv_bytes,
    _normalize_header,
    _xlsx_cell_text,
    _xlsx_shared_strings,
)

TABULAR_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".xls", ".xml"})


@dataclass(frozen=True, slots=True)
class TabularJsonResult:
    records: list[dict[str, str]]
    columns: list[str]
    row_count: int
    source_format: str


def is_tabular_filename(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in TABULAR_EXTENSIONS


def _nonempty_cells(row: list[str]) -> int:
    return sum(1 for cell in row if str(cell or "").strip())


def _matrix_to_records(headers: list[str], body: list[list[str]]) -> TabularJsonResult:
    """Matrix (already split) → JSON records with a *real* header row.

    Sheets exported from templates often carry artifact rows above the real
    header (a lone ``@dropdown`` marker, a merged title cell, …). The first
    row holding at least two non-empty cells is treated as the header when
    the rows before it are sparser — otherwise the first row stays the header
    (classic single-column files keep that behavior).

    Fully-empty rows are dropped, and columns that are empty both in the
    header and in every data row are removed: the inline representation must
    not ship col_6..col_25 padding.
    """
    # Drop fully-empty rows first.
    body = [row for row in body if _nonempty_cells(row) > 0]

    header_start = -1
    if headers and _nonempty_cells(headers) < 2:
        for i, row in enumerate(body):
            if _nonempty_cells(row) >= 2:
                # First rich row below the sparse header is the real header.
                header_start = i
                break
    if header_start >= 0:
        headers = body[header_start]
        body = body[header_start + 1 :]

    # Columns with no data at all are template padding: drop them when the
    # header is empty, or when there are enough rows to be sure the column
    # is genuinely unused (>= 2). Named columns of 0-1-row tables survive.
    width = max([len(headers)] + [len(r) for r in body]) if body else len(headers)
    keep: list[int] = []
    for i in range(width):
        header_cell = str(headers[i]).strip() if i < len(headers) else ""
        has_data = any(i < len(r) and str(r[i]).strip() for r in body)
        if has_data or (header_cell and len(body) < 2):
            keep.append(i)
    if not keep:
        keep = [0]
    headers = [headers[i] if i < len(headers) else "" for i in keep]
    body = [[row[i] if i < len(row) else "" for i in keep] for row in body]

    used: set[str] = set()
    cols = [_normalize_header(h, index=i, used=used) for i, h in enumerate(headers)]
    if not cols:
        cols = ["col_1"]
    records: list[dict[str, str]] = []
    for row in body:
        padded = list(row[: len(cols)]) + [""] * max(0, len(cols) - len(row))
        records.append({cols[i]: padded[i] for i in range(len(cols))})
    return TabularJsonResult(
        records=records,
        columns=cols,
        row_count=len(records),
        source_format="matrix",
    )


def _csv_to_records(data: bytes, *, source_format: str = "csv") -> TabularJsonResult:
    text = _decode_csv_bytes(data)
    matrix = _csv_matrix(text)
    if not matrix:
        return TabularJsonResult(records=[], columns=["col_1"], row_count=0, source_format=source_format)
    result = _matrix_to_records(matrix[0], matrix[1:])
    return TabularJsonResult(
        records=result.records,
        columns=result.columns,
        row_count=result.row_count,
        source_format=source_format,
    )


def _xlsx_to_records(data: bytes) -> TabularJsonResult:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        grid: dict[int, dict[int, str]] = {}
        max_col = 0
        max_row = 0
        for row_el in root.findall("m:sheetData/m:row", _NS):
            r_idx = int(row_el.attrib.get("r") or "0") - 1
            if r_idx < 0:
                continue
            max_row = max(max_row, r_idx)
            for cell in row_el.findall("m:c", _NS):
                ref = cell.attrib.get("r") or ""
                c_idx = _col_letter_to_index(ref)
                max_col = max(max_col, c_idx)
                grid.setdefault(r_idx, {})[c_idx] = _xlsx_cell_text(cell, shared)
        width = max_col + 1 if max_col >= 0 else 1
        matrix: list[list[str]] = []
        for r in range(max_row + 1):
            row = [grid.get(r, {}).get(c, "") for c in range(width)]
            if any(cell.strip() for cell in row):
                matrix.append(row)
    if not matrix:
        return TabularJsonResult(records=[], columns=["col_1"], row_count=0, source_format="xlsx")
    result = _matrix_to_records(matrix[0], matrix[1:])
    return TabularJsonResult(
        records=result.records,
        columns=result.columns,
        row_count=result.row_count,
        source_format="xlsx",
    )


def _xml_element_to_json(el: ET.Element) -> object:
    children = list(el)
    text = (el.text or "").strip()
    attrib = {k: v for k, v in el.attrib.items()}
    if not children and not attrib:
        return text
    node: dict[str, object] = {}
    if attrib:
        node["@attrs"] = attrib
    if text:
        node["#text"] = text
    grouped: dict[str, list[object]] = {}
    for child in children:
        grouped.setdefault(child.tag.split("}")[-1], []).append(_xml_element_to_json(child))
    for key, values in grouped.items():
        node[key] = values[0] if len(values) == 1 else values
    return node


def _spreadsheet_ml_rows(root: ET.Element) -> list[list[str]] | None:
    """Excel 2003 XML SpreadsheetML → matrix, or None if not that dialect."""
    tag = root.tag.split("}")[-1].lower()
    if tag not in {"workbook", "worksheet"}:
        # Search for Table/Row under any SpreadsheetML namespace.
        rows_el = root.findall(".//{*}Row")
        if not rows_el:
            return None
    else:
        rows_el = root.findall(".//{*}Row")
        if not rows_el:
            return None
    matrix: list[list[str]] = []
    for row in rows_el:
        cells = []
        for cell in row.findall("{*}Cell"):
            data = cell.find("{*}Data")
            cells.append("" if data is None or data.text is None else str(data.text))
        if any(c.strip() for c in cells):
            matrix.append(cells)
    return matrix or None


def _xml_to_tabular_or_tree(data: bytes) -> TabularJsonResult:
    text = data.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)
    matrix = _spreadsheet_ml_rows(root)
    if matrix is not None:
        result = _matrix_to_records(matrix[0], matrix[1:] if len(matrix) > 1 else [])
        return TabularJsonResult(
            records=result.records,
            columns=result.columns,
            row_count=result.row_count,
            source_format="xml",
        )
    tree = _xml_element_to_json(root)
    # Single-object "table": wrap as one record for uniform delivery.
    if isinstance(tree, dict):
        flat = {str(k): json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for k, v in tree.items()}
        cols = list(flat.keys()) or ["col_1"]
        return TabularJsonResult(records=[flat], columns=cols, row_count=1, source_format="xml")
    return TabularJsonResult(
        records=[{"value": json.dumps(tree, ensure_ascii=False)}],
        columns=["value"],
        row_count=1,
        source_format="xml",
    )


def tabular_bytes_to_json(
    data: bytes,
    *,
    filename: str,
) -> TabularJsonResult:
    """Convert csv/tsv/xlsx/xls/xml bytes into JSON records.

    Legacy ``.xls`` (BIFF) is not supported without extra deps — raise ValueError.
    """
    name = (filename or "").lower()
    ext = Path(name).suffix.lower()
    if ext in {".csv", ".tsv"} or (ext == "" and b"," in data[:200]):
        return _csv_to_records(data, source_format="tsv" if ext == ".tsv" else "csv")
    if ext == ".xlsx" or data[:2] == b"PK":
        return _xlsx_to_records(data)
    if ext == ".xls":
        if data[:2] == b"PK":
            return _xlsx_to_records(data)
        raise ValueError("legacy .xls is not supported; convert to .xlsx or .csv")
    if ext == ".xml":
        return _xml_to_tabular_or_tree(data)
    # Fallback sniff
    head = data.lstrip()[:64]
    if head.startswith(b"<") or head.startswith(b"\xef\xbb\xbf<"):
        return _xml_to_tabular_or_tree(data)
    return _csv_to_records(data)


def records_to_json_bytes(records: list[dict[str, str]], *, indent: int | None = 2) -> bytes:
    return json.dumps(records, ensure_ascii=False, indent=indent).encode("utf-8")


def _md_cell(value: str) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = text.replace("|", "\\|")
    return re.sub(r"\s+", " ", text)


def records_to_markdown_table(
    records: list[dict[str, str]],
    columns: list[str],
    *,
    max_rows: int | None = None,
) -> str:
    """Records → compact GFM markdown table (the natural chat representation)."""
    shown = records[:max_rows] if max_rows else records
    lines = [
        "| " + " | ".join(_md_cell(c) for c in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for rec in shown:
        lines.append("| " + " | ".join(_md_cell(rec.get(c, "")) for c in columns) + " |")
    return "\n".join(lines)
