"""Merge multiple mapped tabular SQLite artifacts into one canonical table."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

# Source headers that mean "currency of the price column" (case-insensitive).
_CURRENCY_HEADER_RE = re.compile(
    r"^(валюта|вал\.?|currency|curr\.?|cur\.|ден\.?ед\.?|currency_code|iso_currency)$",
    re.IGNORECASE,
)

# Raw currency cell -> RUB/USD/EUR ("" when unknown).
_CURRENCY_VALUES = {
    "RUB": "RUB",
    "RUR": "RUB",
    "РУБ": "RUB",
    "РУБ.": "RUB",
    "Р": "RUB",
    "R": "RUB",
    "₽": "RUB",
    "USD": "USD",
    "$": "USD",
    "USA": "USD",
    "US": "USD",
    "S": "USD",
    "ДОЛЛ": "USD",
    "ДОЛЛ.": "USD",
    "ДОЛЛАР": "USD",
    "ДОЛЛАР США": "USD",
    "У.Е.": "USD",
    "УЕ": "USD",
    "EUR": "EUR",
    "€": "EUR",
    "E": "EUR",
    "ЕВРО": "EUR",
    "EVRO": "EUR",
}


def normalize_currency(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    return _CURRENCY_VALUES.get(s, "")


DEFAULT_SCHEMA = (
    "title",
    "price",
    "part_number",
    "brand",
    "supplier",
    "lead_time",
    "currency",
    "source_catalog",
)


def column_map_is_complete(
    column_map: dict[str, Any] | None,
    *,
    required_keys: list[str],
) -> bool:
    if not isinstance(column_map, dict):
        return False
    for key in required_keys:
        raw = column_map.get(key)
        if not isinstance(raw, str) or not raw.strip():
            return False
    return True


def merge_mapped_sqlite_bytes(
    sources: list[dict[str, Any]],
    *,
    schema: list[str] | None = None,
    required_map_keys: list[str] | None = None,
    artifact_field: str = "artifact_ref",
    map_field: str = "column_map",
    provenance_target: str = "source_catalog",
    provenance_from: str = "name",
    load_artifact: Any | None = None,
) -> tuple[bytes, int, str]:
    """Build one SQLite blob from catalog rows.

    Each ``sources`` item is a row body (may include ``row_id``) with:
    - artifact bytes via ``load_artifact(ref)`` or inline ``_artifact_bytes``
    - ``column_map`` mapping target→source column names
    - provenance label from ``provenance_from``

    Returns ``(sqlite_bytes, row_count, content_sha256)``.
    """
    cols = list(schema or DEFAULT_SCHEMA)
    req = list(required_map_keys or ["title", "price"])
    if provenance_target not in cols:
        cols.append(provenance_target)

    out = sqlite3.connect(":memory:")
    try:
        col_sql = ", ".join(f'"{c}" TEXT' for c in cols)
        out.execute(f"CREATE TABLE rows ({col_sql})")
        placeholders = ", ".join("?" for _ in cols)
        insert_sql = f"INSERT INTO rows VALUES ({placeholders})"
        total = 0
        digest = hashlib.sha256()

        for body in sources:
            cmap = body.get(map_field)
            if isinstance(cmap, str):
                try:
                    cmap = json.loads(cmap)
                except json.JSONDecodeError:
                    cmap = None
            if not column_map_is_complete(cmap if isinstance(cmap, dict) else None, required_keys=req):
                continue
            assert isinstance(cmap, dict)

            raw = body.get("_artifact_bytes")
            if not isinstance(raw, (bytes, bytearray)):
                ref = body.get(artifact_field)
                if load_artifact is None or not isinstance(ref, dict):
                    continue
                raw = load_artifact(ref)
            if not isinstance(raw, (bytes, bytearray)) or not raw:
                continue

            provenance = str(body.get(provenance_from) or body.get("row_id") or "").strip()
            digest.update(raw)
            digest.update(json.dumps(cmap, sort_keys=True, ensure_ascii=False).encode("utf-8"))
            digest.update(provenance.encode("utf-8"))

            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = Path(tmp.name)
            try:
                uri = f"file:{tmp_path.as_posix()}?mode=ro"
                src = sqlite3.connect(uri, uri=True)
                try:
                    src_cols = [r[1] for r in src.execute("PRAGMA table_info(rows)").fetchall()]
                    if not src_cols:
                        continue
                    select_cols = ", ".join(f'"{c}"' for c in src_cols)
                    for row in src.execute(f"SELECT {select_cols} FROM rows"):
                        values = {
                            src_cols[i]: "" if row[i] is None else str(row[i])
                            for i in range(len(src_cols))
                        }
                        mapped: list[str] = []
                        currency_idx = -1
                        currency_val = ""
                        for ti, target in enumerate(cols):
                            if target == provenance_target:
                                mapped.append(provenance)
                                continue
                            source_name = cmap.get(target)
                            if target == "currency":
                                currency_idx = ti
                            if not isinstance(source_name, str) or not source_name.strip():
                                mapped.append("")
                                continue
                            mapped.append(values.get(source_name.strip(), ""))
                        if currency_idx >= 0 and not mapped[currency_idx]:
                            # Legacy column maps have no currency entry - heal
                            # from a currency-ish source header so USD/EUR
                            # catalogs are not mislabeled as RUB.
                            for key in values:
                                if _CURRENCY_HEADER_RE.match(str(key).strip()):
                                    currency_val = normalize_currency(values.get(key))
                                    if currency_val:
                                        break
                        if currency_idx >= 0:
                            mapped[currency_idx] = currency_val or (
                                normalize_currency(mapped[currency_idx]) or "RUB"
                            )
                        out.execute(insert_sql, mapped)
                        total += 1
                finally:
                    src.close()
            finally:
                tmp_path.unlink(missing_ok=True)

        # Helpful indexes for agent catalog search (exact PN / title / brand).
        colset = set(cols)
        if "part_number" in colset:
            out.execute(
                'CREATE INDEX IF NOT EXISTS idx_rows_part_number_lower '
                'ON rows (lower("part_number"))'
            )
        if "title" in colset:
            out.execute('CREATE INDEX IF NOT EXISTS idx_rows_title ON rows ("title")')
        if "brand" in colset:
            out.execute(
                'CREATE INDEX IF NOT EXISTS idx_rows_brand_lower ON rows (lower("brand"))'
            )

        out.commit()
        blob = out.serialize()
    finally:
        out.close()

    return blob, total, digest.hexdigest()
