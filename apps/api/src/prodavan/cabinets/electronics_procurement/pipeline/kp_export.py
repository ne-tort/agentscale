"""Build KP xlsx from run artifacts only (INV-SKP-001)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook

TEMPLATE_VERSION = "kp-prodavan-v1"
HEADERS = (
    "line_id",
    "raw_text",
    "part_number",
    "qty",
    "role",
    "seller",
    "title",
    "price",
    "currency",
    "source",
    "needs_review",
)


def build_kp_rows(
    lineitems: list[dict],
    offers: list[dict],
    selections: list[dict],
    *,
    include_alternatives: bool,
) -> list[tuple]:
    offer_by_id = {o["offer_id"]: o for o in offers}
    selection_by_line = {s["line_id"]: s for s in selections}
    rows: list[tuple] = []
    for item in lineitems:
        line_id = item["line_id"]
        selection = selection_by_line.get(line_id)
        base = (
            line_id,
            item.get("raw_text") or "",
            item.get("part_number") or "",
            item.get("qty") or 1,
        )
        if selection is None:
            rows.append((*base, "", "", "", None, "", "", bool(item.get("needs_review"))))
            continue
        primary = offer_by_id.get(selection["primary_offer_id"])
        rows.append(_offer_row(base, "primary", primary, item))
        if include_alternatives:
            for alt_id in selection.get("alternative_offer_ids") or []:
                alt = offer_by_id.get(alt_id)
                if alt:
                    rows.append(_offer_row(base, "alternative", alt, item))
    return rows


def _offer_row(base: tuple, role: str, offer: dict | None, item: dict) -> tuple:
    if offer is None:
        return (*base, role, "", "", None, "", "", True)
    return (
        *base,
        role,
        offer.get("seller") or "",
        offer.get("title") or "",
        offer.get("price"),
        offer.get("currency") or "",
        offer.get("source") or "",
        bool(item.get("needs_review")),
    )


def write_kp_workbook(path: Path, rows: list[tuple]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "КП"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def timestamp_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
