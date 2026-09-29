"""«Бюджетирование» / КП workbook fill for the equipment module (EQUIP-BUD).

Ported from the Commerce KP exporter, adapted to the platform data model:

- input rows are ``budget_lines`` bodies (``title`` / ``part_number`` / ``qty`` /
  ``price_in`` / ``vat`` / ``markup`` / ``seller`` / ``comment``) — the platform
  writes values directly instead of Excel lookups / dropdowns;
- template formulas (``H`` = ``PRODUCT(E,F)`` … ``O`` = ``PRODUCT(E,N)``, header
  totals on row 4, КП per-item references) are **never rewritten** — only the
  cached ``<v>`` next to each ``<f>`` is refreshed so the file opens with the
  right numbers before any recalculation;
- the КП sheet trims empty item rows (``12+n .. 111``) with a full shift of
  ``row/@r`` / ``cell/@r`` / merges / dimension / drawing anchors, exactly like
  Commerce ``trim_empty_kp_rows``;
- empty budget rows are kept (manual additions after export stay possible).

Pure bytes-in → bytes-out; no lookup sheets, no workbook.xml patch.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from prodavan.application.documents.editing import xlsx_xml_patch as xpatch
from prodavan.domain.errors import AppError

# --- template contract (mirrors Commerce kp/constants.py) ---------------------

DATA_START_ROW = 7  # first budget row («Бюджетирование»)
KP_DATA_START = 12  # first КП item row
KP_ITEM_TEMPLATE_LAST = 111  # 100 item slots (12..111)
BUDGET_TEMPLATE_ROWS = 100  # rows 7..106 carry formulas/numbers in the template

DEFAULT_VAT = 0.22
DEFAULT_MARKUP = 0.1
DEFAULT_CREDIT_DAYS = 30.0

PN_UNKNOWN = "Не определен"
TITLE_NOT_FOUND = "Не найден"
SELLER_NOT_FOUND = "Не найден"

BUDGET_SHEET_PART = "xl/worksheets/sheet1.xml"
KP_SHEET_PART = "xl/worksheets/sheet2.xml"
DRAWING_PART = "xl/drawings/drawing1.xml"

BUDGET_MONEY_COL_WIDTHS = {6: 13.5, 8: 14.5, 12: 14.0, 13: 14.5}  # F H L M
KP_MONEY_COL_WIDTHS = {7: 13.0, 8: 14.5}  # G H

TEMPLATE_NAME = "kp-template.xlsx"

_BUDGET_REF = re.compile(r"Бюджетирование!\$?([A-Z]+)\$?(\d+)")
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


# --- money caches (Commerce kp/budget.py::money_cells) ------------------------


def money_cache(qty: Any, price_in: Any, vat: Any, markup: Any) -> dict[str, float]:
    """Cached results for template formulas F/H/J–O.

    H = qty*F, K = F*(1+markup), M = qty*K, J = K/(1+vat), L = M/(1+vat),
    N = K-F, O = qty*N. H/K/M/N/O round 2; J/L round 6 (Commerce parity).
    """
    try:
        e = float(qty or 0)
        f = float(price_in)
    except (TypeError, ValueError):
        return {}
    g = float(vat or 0)
    i = float(markup or 0)
    h = round(e * f, 2)
    k = round(f * (1 + i), 2)
    m = round(e * k, 2)
    denom = 1 + g
    j = round(k / denom, 6) if denom else 0.0
    l_out = round(m / denom, 6) if denom else 0.0
    n = round(k - f, 2)
    o = round(e * n, 2)
    return {"F": round(f, 2), "H": h, "J": j, "K": k, "L": l_out, "M": m, "N": n, "O": o}


# --- fill model ----------------------------------------------------------------


@dataclass(slots=True)
class BudgetRowFill:
    """One «Бюджетирование» row: direct values + formula caches."""

    row: int
    values: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BudgetFillModel:
    """Fill model for the whole workbook (budget rows + header totals)."""

    n_items: int = 0
    budget: list[BudgetRowFill] = field(default_factory=list)
    header_cache: dict[str, float] = field(default_factory=dict)


def _num_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _str_or(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def build_budget_fill(
    rows: list[dict[str, Any]] | None,
    header: dict[str, Any] | None = None,
) -> BudgetFillModel:
    """Build the fill model from budget row bodies (no workbook mutation)."""
    h = header if isinstance(header, dict) else {}
    vat_rate = _num_or(h.get("vat"), DEFAULT_VAT)
    credit_days = _num_or(h.get("credit_days"), DEFAULT_CREDIT_DAYS)

    model = BudgetFillModel()
    for raw in rows or []:
        if len(model.budget) >= BUDGET_TEMPLATE_ROWS:
            break  # template has 100 formula slots; extra rows are ignored
        body = raw if isinstance(raw, dict) else {}
        qty = _num_or(body.get("qty"), 1)
        price_in = _num_or(body.get("price_in"), 0)
        vat = _num_or(body.get("vat"), DEFAULT_VAT)
        markup = _num_or(body.get("markup"), DEFAULT_MARKUP)
        values: dict[str, Any] = {
            "B": len(model.budget) + 1,
            "C": _str_or(body.get("part_number")) or PN_UNKNOWN,
            "D": _str_or(body.get("title")) or TITLE_NOT_FOUND,
            "E": qty,
            "F": price_in,
            "G": vat,
            "I": markup,
            "P": _str_or(body.get("seller")) or SELLER_NOT_FOUND,
            "Q": str(body.get("comment") or ""),
        }
        model.budget.append(
            BudgetRowFill(
                row=DATA_START_ROW + len(model.budget),
                values=values,
                cache=money_cache(qty, price_in, vat, markup),
            )
        )
    model.n_items = len(model.budget)

    # Header totals (row 4 caches) — Commerce _patch_header_totals parity.
    e4 = round(sum(float(f.values.get("E") or 0) for f in model.budget), 2)
    h4 = round(sum(f.cache.get("H") or 0.0 for f in model.budget), 2)
    m4 = round(sum(f.cache.get("M") or 0.0 for f in model.budget), 2)
    denom = 1 + vat_rate
    l4 = round(m4 / denom, 6) if denom else 0.0
    k4 = round(m4 - l4, 2)
    n4 = round(m4 - h4, 2)
    j4 = round(h4 * 0.083 / 100 * credit_days, 6)
    o4 = round(n4 / m4, 6) if m4 else 0.0
    model.header_cache = {"E": e4, "H": h4, "J": j4, "K": k4, "L": l4, "M": m4, "N": n4, "O": o4}
    return model


# --- sheet patching ---------------------------------------------------------------


def _patch_budget_sheet(xml: bytes, model: BudgetFillModel) -> bytes:
    """Values + caches for «Бюджетирование»: formulas stay untouched."""
    root = ET.fromstring(xml)
    sheet_data = root.find(xpatch._q("sheetData"))
    if sheet_data is None:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="budget sheet has no sheetData",
        )

    xpatch.ensure_col_widths(root, BUDGET_MONEY_COL_WIDTHS)

    for fill in model.budget:
        row = xpatch.find_row(sheet_data, fill.row)
        if row is None:
            continue
        for col, val in fill.values.items():
            if val is None or val == "":
                continue
            cell = xpatch.cell_in_row(row, col)
            if isinstance(val, int | float) and not isinstance(val, bool):
                xpatch.set_number(cell, val)
            else:
                xpatch.set_text(cell, str(val))
        for col, cached in fill.cache.items():
            if col in fill.values:
                continue  # F is a direct value here, not a formula cache
            cell = xpatch.cell_in_row(row, col)
            if cell.find(xpatch._q("f")) is not None:
                xpatch.set_cached_number(cell, cached)

    row4 = xpatch.find_row(sheet_data, 4)
    if row4 is not None:
        for col, val in model.header_cache.items():
            cell = xpatch.cell_in_row(row4, col)
            if cell.find(xpatch._q("f")) is not None:
                xpatch.set_cached_number(cell, val)
    return xpatch.dumps_sml(root)


def _shift_addr(addr: str, *, first_empty: int, last_slot: int, delta: int) -> str | None:
    """Shift A1-style refs. None = drop (row deleted by the trim)."""

    def one(col: str, row_s: str) -> str | None:
        row = int(row_s)
        if first_empty <= row <= last_slot:
            return None
        if row > last_slot:
            row -= delta
        return f"{col}{row}"

    out: list[str] = []
    for part in addr.split(":"):
        m = _CELL_REF.fullmatch(part)
        if not m:
            out.append(part)
            continue
        shifted = one(m.group(1), m.group(2))
        if shifted is None:
            return None
        out.append(shifted)
    return ":".join(out)


def _trim_kp_rows(root: ET.Element, *, n_items: int) -> bool:
    """Delete empty КП item rows and shift everything below up. True if trimmed."""
    first_empty = KP_DATA_START + n_items
    last_slot = KP_ITEM_TEMPLATE_LAST
    if n_items <= 0 or first_empty > last_slot:
        return False
    delta = last_slot - first_empty + 1

    sheet_data = root.find(xpatch._q("sheetData"))
    if sheet_data is None:
        return False
    kept: list[ET.Element] = []
    for row in list(sheet_data):
        r = int(row.get("r") or 0)
        if first_empty <= r <= last_slot:
            continue
        if r > last_slot:
            row.set("r", str(r - delta))
            for cell in row.findall(xpatch._q("c")):
                shifted = _shift_addr(
                    cell.get("r") or "",
                    first_empty=first_empty,
                    last_slot=last_slot,
                    delta=delta,
                )
                if shifted:
                    cell.set("r", shifted)
        kept.append(row)
    sheet_data[:] = kept

    merges = root.find(xpatch._q("mergeCells"))
    if merges is not None:
        remain = []
        for mc in list(merges):
            shifted = _shift_addr(
                mc.get("ref") or "",
                first_empty=first_empty,
                last_slot=last_slot,
                delta=delta,
            )
            if shifted is None:
                continue
            mc.set("ref", shifted)
            remain.append(mc)
        merges[:] = remain
        merges.set("count", str(len(remain)))

    dim = root.find(xpatch._q("dimension"))
    if dim is not None and kept:
        last = kept[-1].get("r") or "1"
        m = _CELL_REF.fullmatch((dim.get("ref") or "").split(":")[-1] or "")
        col = m.group(1) if m else "I"
        dim.set("ref", f"A1:{col}{last}")
    return True


def _refresh_kp_formula_cells(root: ET.Element, model: BudgetFillModel) -> None:
    """КП caches: D refs are text (t=str, no cache); E/G/H + totals get numbers."""
    fills = {fill.row: fill for fill in model.budget}
    header = model.header_cache
    for cell in root.iter(xpatch._q("c")):
        f = cell.find(xpatch._q("f"))
        if f is None:
            continue
        m = _BUDGET_REF.search(f.text or "")
        if m and m.group(1) == "D":
            for cached in cell.findall(xpatch._q("v")):
                cell.remove(cached)
            cell.set("t", "str")
            continue
        if not m:
            continue
        col, row = m.group(1), int(m.group(2))
        val: float | int | None = None
        if row == 4:
            val = header.get(col)
        elif row in fills:
            fill = fills[row]
            if col in fill.cache:
                val = fill.cache[col]
            else:
                raw = fill.values.get(col)
                if isinstance(raw, int | float) and not isinstance(raw, bool):
                    val = raw
        if val is None:
            val = 0
        xpatch.set_cached_number(cell, val)


def _patch_kp_sheet(xml: bytes, model: BudgetFillModel) -> bytes:
    root = ET.fromstring(xml)
    trimmed = _trim_kp_rows(root, n_items=model.n_items)
    xpatch.ensure_col_widths(root, KP_MONEY_COL_WIDTHS)
    _refresh_kp_formula_cells(root, model)
    _ = trimmed
    return xpatch.dumps_sml(root)


def _shift_drawing_rows(xml: bytes, n_items: int) -> bytes:
    """Shift stamp/logo anchors up so they do not «float» after the trim."""
    first_empty = KP_DATA_START + n_items
    last_slot = KP_ITEM_TEMPLATE_LAST
    if n_items <= 0 or first_empty > last_slot:
        return xml
    delta = last_slot - first_empty + 1
    root = ET.fromstring(xml)
    for row_el in root.iter(xpatch.drawing_row_tag("row")):
        try:
            r = int(row_el.text or 0)
        except ValueError:
            continue
        if r >= first_empty:
            row_el.text = str(max(r - delta, 0))
    return xpatch.dumps_drawing(root)


# --- public entry points -----------------------------------------------------------


def fill_budget_workbook(
    template_bytes: bytes,
    rows: list[dict[str, Any]] | None,
    header: dict[str, Any] | None = None,
) -> bytes:
    """Fill the КП template from budget rows; returns the patched workbook bytes."""
    model = build_budget_fill(rows, header)
    with zipfile.ZipFile(io.BytesIO(template_bytes), "r") as z:
        sheet1 = z.read(BUDGET_SHEET_PART)
        sheet2 = z.read(KP_SHEET_PART)
        drawing = z.read(DRAWING_PART) if DRAWING_PART in z.namelist() else None

    updates = {
        BUDGET_SHEET_PART: _patch_budget_sheet(sheet1, model),
        KP_SHEET_PART: _patch_kp_sheet(sheet2, model),
    }
    if drawing is not None:
        updates[DRAWING_PART] = _shift_drawing_rows(drawing, model.n_items)
    return xpatch.rewrite_xlsx_bytes(template_bytes, updates)


def load_budget_template() -> bytes:
    """Read the КП template from ``documents_templates_dir`` (cwd-relative)."""
    from prodavan.config.settings import settings

    base = str(getattr(settings, "documents_templates_dir", "") or "templates").strip() or "templates"
    candidates = [Path(base) / TEMPLATE_NAME]
    if not Path(base).is_absolute():
        candidates.append(Path.cwd() / base / TEMPLATE_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_bytes()
    raise AppError(
        code="VALIDATION_ERROR",
        title="Validation Error",
        status=422,
        detail=f"budget template not found: {TEMPLATE_NAME} (documents_templates_dir={base})",
    )
