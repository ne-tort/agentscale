"""Index uploaded catalog files into catalog.sqlite. Skip on_order rows."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from prodavan.infrastructure.storage.catalog_storage import CATALOG_SQLITE_SCHEMA

_PN_HEADERS = ("part_number", "pn", "p/n", "артикул")
_TITLE_HEADERS = ("title", "name", "наименование")
_PRICE_HEADERS = ("price", "price_rub", "цена")
_STOCK_HEADERS = ("stock", "qty", "availability", "наличие")
_ON_ORDER = {"on_order", "on-order", "под заказ", "подзаказ"}


class CatalogIndexError(Exception):
    pass


def _header_map(fieldnames: list[str]) -> dict[str, str]:
    lowered = {name.lower().strip(): name for name in fieldnames}

    def pick(aliases: tuple[str, ...]) -> str | None:
        for alias in aliases:
            if alias in lowered:
                return lowered[alias]
        return None

    pn = pick(_PN_HEADERS)
    if pn is None:
        raise CatalogIndexError("CSV must include part_number (or pn) column")
    return {
        "pn": pn,
        "title": pick(_TITLE_HEADERS),
        "price": pick(_PRICE_HEADERS),
        "stock": pick(_STOCK_HEADERS),
        "currency": lowered.get("currency"),
    }


def index_csv_bytes(db_path: Path, data: bytes) -> int:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CatalogIndexError("Empty CSV")
    mapping = _header_map(list(reader.fieldnames))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    inserted = 0
    skipped_on_order = 0
    with sqlite3.connect(db_path) as conn:
        conn.executescript(CATALOG_SQLITE_SCHEMA)
        conn.execute("DELETE FROM products")
        for row in reader:
            pn = (row.get(mapping["pn"]) or "").strip()
            if not pn:
                continue
            stock_raw = ""
            if mapping["stock"]:
                stock_raw = (row.get(mapping["stock"]) or "").strip()
            if stock_raw.lower() in _ON_ORDER:
                skipped_on_order += 1
                continue
            title = (row.get(mapping["title"]) or "").strip() if mapping["title"] else ""
            price = None
            if mapping["price"]:
                raw_price = (row.get(mapping["price"]) or "").replace(",", ".").strip()
                if raw_price:
                    try:
                        price = float(raw_price)
                    except ValueError:
                        continue
            currency = "RUB"
            if mapping["currency"]:
                currency = (row.get(mapping["currency"]) or "RUB").strip() or "RUB"
            in_stock = 1
            conn.execute(
                "INSERT INTO products (part_number, title, price, currency, stock, in_stock) VALUES (?,?,?,?,?,?)",
                (pn, title, price, currency, stock_raw, in_stock),
            )
            inserted += 1
        conn.commit()
    return inserted


def search_exact_pn(db_path: Path, part_number: str) -> list[dict]:
    if not db_path.is_file() or not part_number:
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT part_number, title, price, currency, stock, in_stock FROM products "
            "WHERE part_number = ? AND in_stock = 1 AND price IS NOT NULL",
            (part_number,),
        ).fetchall()
    return [dict(row) for row in rows]
