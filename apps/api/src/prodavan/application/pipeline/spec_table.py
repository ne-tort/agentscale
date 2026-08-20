"""Filter and dedupe spec table rows. Does not assign part numbers.

Port of Commerce `tools/extract_table.py` cleaning rules:
drop Excel errors, headers, legal boilerplate, and duplicate SKUs
across КП / Маржа / Спецификация sheets. Richer row (has P/N column
value) wins. P/N guessed here is only a dedup key, never written as
an invented customer part number.
"""

from __future__ import annotations

import re
from typing import Any

EXCEL_ERROR_RE = re.compile(
    r"^#(REF|N/A|VALUE|DIV/0|NAME\?|NULL|NUM|GETTING_DATA)!$",
    re.I,
)
PN_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,}$", re.I)
HEADER_MARKERS: tuple[tuple[str, ...], ...] = (
    ("партномер", "наименование"),
    ("p/n", "описание"),
    ("part_number", "name"),
    ("№", "описание", "кол-во"),
    ("no", "description"),
)
BOILERPLATE_RE = re.compile(
    r"(коммерческое предложение|условия доставки|срок действия кп|"
    r"генеральный директор|покупатель:|поставщик:|"
    r"итого\b|без ндс|ндс\s*\d|спецификация\s*№|приложение\s*№|"
    r"^@dropdown\b|именуемое в дальнейшем)",
    re.I,
)


def is_excel_error_cell(value: object) -> bool:
    return bool(EXCEL_ERROR_RE.match(str(value or "").strip()))


def nonempty_cells(cells: list[Any]) -> list[str]:
    return [str(c).strip() for c in cells if str(c).strip()]


def guess_pn_from_cells(cells: list[Any]) -> str | None:
    """Token that looks like a P/N — used for product detection and dedup only."""
    for cell in cells:
        text = str(cell).strip()
        if PN_RE.match(text) and any(ch.isdigit() for ch in text):
            return text
    return None


def guess_qty_from_cells(cells: list[Any]) -> int | None:
    for cell in cells:
        text = str(cell).strip()
        if re.fullmatch(r"\d{1,4}", text):
            n = int(text)
            if 1 <= n <= 5000:
                return n
    return None


def is_error_row(cells: list[Any]) -> bool:
    nonempty = nonempty_cells(cells)
    if not nonempty:
        return True
    rest = [c for c in nonempty if not re.fullmatch(r"\d+", c)]
    if not rest:
        return True
    return all(is_excel_error_cell(c) for c in rest)


def is_header_row(cells: list[Any]) -> bool:
    joined = " ".join(nonempty_cells(cells)).lower()
    if not joined:
        return False
    return any(all(marker in joined for marker in markers) for markers in HEADER_MARKERS)


def is_boilerplate_row(cells: list[Any]) -> bool:
    joined = " ".join(nonempty_cells(cells)).strip()
    if not joined:
        return True
    if BOILERPLATE_RE.search(joined):
        return True
    low = joined.lower()
    return len(joined) > 280 and ("договор" in low or "устав" in low)


def looks_like_product_row(cells: list[Any]) -> bool:
    if guess_pn_from_cells(cells):
        return True
    texts = [
        c
        for c in nonempty_cells(cells)
        if not re.fullmatch(r"\d+", c) and not is_excel_error_cell(c)
    ]
    long_text = any(len(c) >= 8 for c in texts)
    return guess_qty_from_cells(cells) is not None and long_text


def _normalized_title(row: dict[str, Any]) -> str:
    cells = row.get("cells") or []
    texts = [
        c
        for c in nonempty_cells(cells)
        if not re.fullmatch(r"\d+", c) and not is_excel_error_cell(c) and not PN_RE.match(c)
    ]
    title = max(texts, key=len, default="")
    return re.sub(r"\s+", " ", title).strip().casefold()


def _product_key(row: dict[str, Any]) -> tuple[Any, ...]:
    cells = row.get("cells") or []
    pn = guess_pn_from_cells(cells)
    qty = guess_qty_from_cells(cells)
    if pn:
        return ("pn", pn.casefold())
    return ("title", _normalized_title(row), qty)


def _row_richness(row: dict[str, Any]) -> tuple[int, int]:
    cells = row.get("cells") or []
    filled = sum(1 for c in nonempty_cells(cells) if not is_excel_error_cell(c))
    has_pn = 1 if guess_pn_from_cells(cells) else 0
    return (has_pn, filled)


def headers_by_sheet(rows: list[dict[str, Any]]) -> dict[str | None, list[str]]:
    found: dict[str | None, list[str]] = {}
    for row in rows:
        sheet = row.get("sheet")
        if sheet in found:
            continue
        cells = row.get("cells") or []
        if is_header_row(cells):
            found[sheet] = [str(c).strip() for c in cells]
    return found


def zip_header_cells(headers: list[str], cells: list[Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = header or f"col_{index}"
        value = str(cells[index]).strip() if index < len(cells) else ""
        if key:
            mapped[key] = value
    return mapped


def clean_extracted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        cells = row.get("cells") or []
        if is_error_row(cells) or is_header_row(cells) or is_boilerplate_row(cells):
            continue
        if not looks_like_product_row(cells):
            continue
        kept.append(row)

    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in kept:
        key = _product_key(row)
        if key[0] == "title" and not key[1]:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = row
            order.append(key)
        elif _row_richness(row) > _row_richness(prev):
            best[key] = row
    pn_titles = {_normalized_title(best[k]) for k in order if k[0] == "pn"}
    return [best[k] for k in order if not (k[0] == "title" and k[1] in pn_titles)]


def to_ingest_rows(
    cleaned: list[dict[str, Any]],
    header_map: dict[str | None, list[str]],
) -> list[dict[str, Any]]:
    ingest: list[dict[str, Any]] = []
    for row in cleaned:
        cells_list = [str(c).strip() if c is not None else "" for c in (row.get("cells") or [])]
        headers = header_map.get(row.get("sheet"))
        if headers:
            cells = zip_header_cells(headers, cells_list)
        else:
            cells = {f"col_{i}": v for i, v in enumerate(cells_list) if v}
        raw = str(row.get("raw") or " ".join(v for v in cells.values() if v)).strip()
        ingest.append(
            {
                "row_index": row.get("row"),
                "sheet": row.get("sheet"),
                "cells": cells,
                "raw_line": raw,
            }
        )
    return ingest
