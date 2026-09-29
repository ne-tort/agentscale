"""Spreadsheet editing helpers (openpyxl, DOCUM).

All functions are pure: bytes in → bytes out. ``fill_template_cells`` is the
template-preservation path — it writes *values only* into the listed cells and
leaves every other cell (including formulas) untouched, so openpyxl serializes
the existing formulas back on save.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from openpyxl import load_workbook, workbook, worksheet
from openpyxl.utils import column_index_from_string

from prodavan.domain.errors import AppError

MAX_READ_ROWS = 5_000
MAX_READ_COLS = 256


@dataclass(slots=True)
class SheetSpec:
    """Declarative sheet description for ``create_workbook``."""

    name: str = "Sheet1"
    rows: list[list[Any]] = field(default_factory=list)
    header: list[Any] | None = None


def _normalize_cell_value(value: Any) -> Any:
    """Make an openpyxl cell value JSON-serializable (read path)."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)


def load_workbook_bytes(data: bytes) -> workbook.Workbook:
    """Open a workbook from bytes; AppError on malformed input."""
    if not data:
        raise AppError(
            code="VALIDATION_ERROR", title="Validation Error", status=422, detail="empty workbook"
        )
    try:
        return load_workbook(io.BytesIO(data), data_only=False, keep_vba=False)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"cannot open xlsx document: {exc}",
        ) from exc


def _resolve_sheet(wb: workbook.Workbook, sheet: str | None) -> worksheet.Worksheet:
    if sheet is None or not sheet.strip():
        return wb.active
    if sheet not in wb.sheetnames:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"sheet {sheet!r} not found",
        )
    return wb[sheet]


def list_sheets(data: bytes) -> list[str]:
    """Sheet names in workbook order."""
    wb = load_workbook_bytes(data)
    return list(wb.sheetnames)


def read_rows(
    data: bytes,
    *,
    sheet: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rows as dicts keyed by the first header row (data_only values).

    ``data_only=False`` is kept so *formulas* return their formula string —
    the caller (agent) usually wants the expression, not a stale cached value.
    """
    wb = load_workbook_bytes(data)
    ws = _resolve_sheet(wb, sheet)
    lim = MAX_READ_ROWS if limit is None else max(1, min(int(limit), MAX_READ_ROWS))
    # Only real columns — iter_rows over MAX_READ_COLS would materialize
    # hundreds of trailing None cells for compact sheets.
    max_col = min(int(ws.max_column or 1), MAX_READ_COLS)
    matrix: list[list[Any]] = []
    for row in ws.iter_rows(min_row=1, max_row=lim + 1, max_col=max_col):
        values = [_normalize_cell_value(c.value) for c in row]
        # trim trailing empty cells so the header/rows stay compact
        while values and (values[-1] is None or values[-1] == ""):
            values.pop()
        if any(v is not None and v != "" for v in values):
            matrix.append(values)
        if len(matrix) >= lim + 1:
            break
    if not matrix:
        return []
    header = [str(h or f"col{i + 1}") for i, h in enumerate(matrix[0])]
    out: list[dict[str, Any]] = []
    for raw in matrix[1 : lim + 1]:
        # right-pad short rows so zip stays aligned
        vals = list(raw) + [None] * (len(header) - len(raw))
        out.append({header[i]: vals[i] for i in range(min(len(header), len(vals)))})
    return out


def set_cells(
    data: bytes,
    *,
    sheet: str | None = None,
    cells: dict[str, Any],
) -> bytes:
    """Write values into cells (``A1``-style addresses) and re-serialize."""
    if not cells:
        raise AppError(
            code="VALIDATION_ERROR", title="Validation Error", status=422, detail="cells required"
        )
    wb = load_workbook_bytes(data)
    ws = _resolve_sheet(wb, sheet)
    for addr, value in cells.items():
        try:
            ws[_normalize_address(addr)] = value
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"cannot set cell {addr}: {exc}",
            ) from exc
    return _save_workbook(wb)


def fill_template_cells(
    data: bytes,
    *,
    sheet: str | None = None,
    cells: dict[str, Any],
    preserve_formulas: bool = True,
) -> bytes:
    """Template fill: write *values only*; never touch formula cells.

    ``preserve_formulas=True`` (default) means cells carrying a formula
    (``openpyxl`` value starting with ``=``) are skipped — the mapping is
    supposed to describe data cells, not rewrite the template's logic.
    Untouched cells keep their formulas: openpyxl re-serializes whatever it
    read, so formulas outside the written addresses survive the round-trip.
    """
    if not cells:
        raise AppError(
            code="VALIDATION_ERROR", title="Validation Error", status=422, detail="cells required"
        )
    wb = load_workbook_bytes(data)
    ws = _resolve_sheet(wb, sheet)
    if preserve_formulas:
        formula_cells = _formula_cell_addresses(ws, cells)
        if formula_cells:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "cells contain formulas and were not overwritten: "
                    f"{', '.join(formula_cells[:20])}"
                ),
            )
    for addr, value in cells.items():
        try:
            ws[_normalize_address(addr)] = value
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"cannot set cell {addr}: {exc}",
            ) from exc
    return _save_workbook(wb)


def _formula_cell_addresses(
    ws: worksheet.Worksheet,
    cells: dict[str, Any],
) -> list[str]:
    """Addresses in ``cells`` that currently hold a formula (``=...``)."""
    hits: list[str] = []
    for addr in cells:
        try:
            cell = ws[_normalize_address(addr)]
        except AppError:
            raise
        except Exception:
            continue
        if isinstance(cell.value, str) and cell.value.startswith("="):
            hits.append(str(addr))
    return hits


def create_workbook(sheets: list[SheetSpec]) -> bytes:
    """Build a workbook from declarative sheet specs (header + rows)."""
    if not sheets:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="at least one sheet spec required",
        )
    wb = workbook.Workbook()
    default = wb.active
    default.title = (sheets[0].name or "Sheet1")[:31] or "Sheet1"
    _fill_sheet(default, sheets[0])
    for spec in sheets[1:]:
        ws = wb.create_sheet(title=(spec.name or f"Sheet{len(wb.sheetnames) + 1}")[:31])
        _fill_sheet(ws, spec)
    return _save_workbook(wb)


def sheet_to_csv(data: bytes, *, sheet: str | None = None) -> str:
    """Active (or named) sheet as CSV text (openpyxl → csv)."""
    wb = load_workbook_bytes(data)
    ws = _resolve_sheet(wb, sheet)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, MAX_READ_ROWS * 10)):
        writer.writerow([_csv_cell(c.value) for c in row])
    return buf.getvalue()


def _csv_cell(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value


def _normalize_address(addr: str) -> str:
    """Accept ``A1`` / ``a1`` / ``Sheet!A1``-free inputs; validate shape."""
    raw = (addr or "").strip().replace("$", "")
    if not raw:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="empty cell address",
        )
    col_part = "".join(ch for ch in raw if ch.isalpha())
    row_part = "".join(ch for ch in raw if ch.isdigit())
    if not col_part or not row_part:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"invalid cell address {addr!r}",
        )
    try:
        column_index_from_string(col_part.upper())
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"invalid cell address {addr!r}",
        ) from exc
    int(row_part)
    return f"{col_part.upper()}{row_part}"


def _fill_sheet(ws: worksheet.Worksheet, spec: SheetSpec) -> None:
    if spec.header:
        ws.append(list(spec.header))
    for row in spec.rows:
        ws.append([_normalize_cell_value(v) if isinstance(v, dict | list) else v for v in row])


def _save_workbook(wb: workbook.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
