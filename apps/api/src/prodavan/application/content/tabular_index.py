"""Tabular file → SQLite artifact (universal content.index_tabular helper)."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

_SAFE_COL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True, slots=True)
class TabularIndexResult:
    sqlite_bytes: bytes
    row_count: int
    columns: list[str]


def _normalize_header(raw: str, *, index: int, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", (raw or "").strip()).strip("_").lower()
    if not base or not _SAFE_COL.match(base):
        base = f"col_{index + 1}"
    name = base
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _rows_to_sqlite(headers: list[str], rows: list[list[str]]) -> TabularIndexResult:
    used: set[str] = set()
    cols = [_normalize_header(h, index=i, used=used) for i, h in enumerate(headers)]
    if not cols:
        cols = ["col_1"]
    conn = sqlite3.connect(":memory:")
    try:
        col_sql = ", ".join(f'"{c}" TEXT' for c in cols)
        conn.execute(f"CREATE TABLE rows ({col_sql})")
        placeholders = ", ".join("?" for _ in cols)
        for row in rows:
            padded = list(row[: len(cols)]) + [""] * max(0, len(cols) - len(row))
            conn.execute(f"INSERT INTO rows VALUES ({placeholders})", padded)
        conn.commit()
        raw = conn.serialize()
    finally:
        conn.close()
    return TabularIndexResult(sqlite_bytes=raw, row_count=len(rows), columns=cols)


def index_csv_bytes(data: bytes) -> TabularIndexResult:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows_iter = list(reader)
    if not rows_iter:
        return _rows_to_sqlite(["col_1"], [])
    headers = [str(c) for c in rows_iter[0]]
    body = [[str(c) for c in r] for r in rows_iter[1:]]
    return _rows_to_sqlite(headers, body)


def _col_letter_to_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return max(0, n - 1)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        texts = [t.text or "" for t in si.findall(".//m:t", _NS)]
        out.append("".join(texts))
    return out


def index_xlsx_bytes(data: bytes, *, sheet_index: int = 0) -> TabularIndexResult:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_name = f"xl/worksheets/sheet{sheet_index + 1}.xml"
        try:
            root = ET.fromstring(zf.read(sheet_name))
        except KeyError as exc:
            raise ValueError(f"xlsx sheet not found: {sheet_name}") from exc
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
                v_el = cell.find("m:v", _NS)
                raw = v_el.text if v_el is not None and v_el.text is not None else ""
                if cell.attrib.get("t") == "s":
                    try:
                        raw = shared[int(raw)]
                    except (ValueError, IndexError):
                        pass
                grid.setdefault(r_idx, {})[c_idx] = raw
        width = max_col + 1 if max_col >= 0 else 1
        matrix: list[list[str]] = []
        for r in range(max_row + 1):
            row = [grid.get(r, {}).get(c, "") for c in range(width)]
            if any(cell.strip() for cell in row):
                matrix.append(row)
        if not matrix:
            return _rows_to_sqlite(["col_1"], [])
        return _rows_to_sqlite(matrix[0], matrix[1:])


def index_tabular_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> TabularIndexResult:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".csv") or "csv" in ctype or (not name.endswith((".xlsx", ".xls")) and b"," in data[:200]):
        return index_csv_bytes(data)
    if name.endswith(".xlsx") or "spreadsheetml" in ctype or "excel" in ctype:
        return index_xlsx_bytes(data)
    if name.endswith(".xls"):
        raise ValueError("legacy .xls is not supported; convert to .xlsx or .csv")
    # default: try csv then xlsx
    try:
        return index_csv_bytes(data)
    except Exception:
        return index_xlsx_bytes(data)
