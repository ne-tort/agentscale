"""Classify rows into LineItems without inventing part numbers."""

from __future__ import annotations

import re

_QTY_KEYS = ("qty", "quantity", "кол-во", "количество")
_PN_KEYS = ("pn", "p/n", "part_number", "partnumber", "артикул")
_PN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,63}$")


def classify_rows(rows: list[dict]) -> dict:
    items: list[dict] = []
    needs_review_count = 0
    for seq, row in enumerate(rows, start=1):
        cells = row.get("cells") or {}
        raw = str(row.get("raw_line") or "").strip()
        qty = _qty_from_cells(cells)
        part_number = _pn_from_cells(cells)
        item = {
            "line_id": f"line_{seq:03d}",
            "raw_text": raw,
            "category": "unknown",
            "part_number": part_number,
            "qty": qty,
            "constraints": [],
            "confidence": 0.4 if part_number else 0.2,
            "needs_review": True,
        }
        needs_review_count += 1
        items.append(item)
    return {"items": items, "needs_review": needs_review_count}


def _qty_from_cells(cells: dict) -> float:
    for key, value in cells.items():
        if key.lower() in _QTY_KEYS:
            try:
                return float(str(value).replace(",", "."))
            except ValueError:
                return 1.0
    return 1.0


def _pn_from_cells(cells: dict) -> str | None:
    for key, value in cells.items():
        if key.lower().replace(" ", "") in {k.replace("/", "") for k in _PN_KEYS} or key.lower() in _PN_KEYS:
            text = str(value).strip()
            if text and _PN_PATTERN.match(text):
                return text
    return None
