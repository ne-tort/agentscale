"""Unified equipment catalog search (local SQLite + remote PostgreSQL).

Canonical result shape after column_map. Designed for million-row remote tables:
push-down filters, exact/prefix PN before title ILIKE, hard LIMIT, never full scan.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CANONICAL_FIELDS = (
    "part_number",
    "title",
    "brand",
    "price",
    "supplier",
    "lead_time",
    "currency",
)

# Source headers that mean "currency of the price column" (case-insensitive).
_CURRENCY_HEADER_RE = re.compile(
    r"^(валюта|вал\.?|currency|curr\.?|cur\.|ден\.?ед\.?|currency_code|iso_currency)$",
    re.IGNORECASE,
)


def detect_currency_value(raw: Any) -> str:
    """Normalize a raw currency cell to RUB/USD/EUR ('' when unknown)."""
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    table = str.maketrans({"$": "S", "€": "E", "₽": "R", "Р": "R"})
    s2 = s.translate(table)
    if s in {"RUB", "RUR", "РУБ", "Р", "R", "₽", "РУБ."} or s2.startswith("RUB"):
        return "RUB"
    usd = {"USD", "$", "USA", "US", "S", "ДОЛЛ", "ДОЛЛ.", "ДОЛЛАР", "ДОЛЛАР США", "У.Е.", "УЕ"}
    if s in usd or s2.startswith("USD"):
        return "USD"
    if s in {"EUR", "€", "E", "ЕВРО", "EVRO", "ЕВРО."} or s2.startswith("EUR"):
        return "EUR"
    return ""

MATCH_EXACT_PN = 0
MATCH_PN_PREFIX = 1
MATCH_TITLE = 2
MATCH_OTHER = 3

_MATCH_LABELS = {
    MATCH_EXACT_PN: "exact_pn",
    MATCH_PN_PREFIX: "pn_prefix",
    MATCH_TITLE: "title",
    MATCH_OTHER: "other",
}

# Values treated as on-order / not in stock (case-insensitive, trim).
_ON_ORDER_EXACT = frozenset(
    {
        "",
        "-",
        "—",
        "–",
        "−",
        ".",
        "…",
        "...",
        "нет",
        "no",
        "n/a",
        "na",
        "n.a.",
        "none",
        "nil",
        "под заказ",
        "подзаказ",
        "on order",
        "on_order",
        "out of stock",
        "oos",
        "нет в наличии",
        "отсутствует",
    }
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_RE = re.compile(
    r"^(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)


def is_in_stock(lead_time: str | None) -> bool:
    """True when availability/lead_time looks like real stock (not on-order)."""
    if lead_time is None:
        return False
    raw = str(lead_time).strip()
    if not raw:
        return False
    key = raw.casefold()
    if key in _ON_ORDER_EXACT:
        return False
    # Collapse internal whitespace for "под  заказ"
    collapsed = " ".join(key.split())
    if collapsed in _ON_ORDER_EXACT:
        return False
    return True


def parse_column_map(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def apply_column_map(source_row: dict[str, Any], column_map: dict[str, str]) -> dict[str, str]:
    """Map source headers → canonical fields (missing → empty string)."""
    values: dict[str, str] = {}
    for canon in CANONICAL_FIELDS:
        src = column_map.get(canon)
        if not src:
            # Already-canonical local merged rows
            if canon in source_row:
                values[canon] = "" if source_row[canon] is None else str(source_row[canon])
            else:
                values[canon] = ""
            continue
        val = source_row.get(src)
        values[canon] = "" if val is None else str(val)
    # Currency is almost always unmapped in legacy column maps - heal it by
    # scanning source headers for a currency-ish column so prices from USD/EUR
    # catalogs do not get mislabeled as RUB.
    if not values.get("currency"):
        for key in source_row:
            if _CURRENCY_HEADER_RE.match(str(key).strip()):
                detected = detect_currency_value(source_row.get(key))
                if detected:
                    values["currency"] = detected
                    break
    if values.get("currency"):
        values["currency"] = detect_currency_value(values["currency"]) or "RUB"
    return values


def parse_price(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    # Strip currency junk
    cleaned = re.sub(r"[^0-9.\-]", "", s)
    if not cleaned or cleaned in {".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CatalogSource:
    id: str
    name: str
    kind: str  # local | remote
    column_map: dict[str, str] = field(default_factory=dict)
    row_count: int | None = None
    # local
    sqlite_path: Path | None = None
    # remote
    dsn: str | None = None
    table: str | None = None  # schema.table or table
    dsn_env: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogHit:
    catalog_id: str
    source_catalog: str
    values: dict[str, str]
    match_rank: int
    in_stock: bool
    price_num: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.values,
            "catalog_id": self.catalog_id,
            "source_catalog": self.source_catalog,
            "match_rank": _MATCH_LABELS.get(self.match_rank, "other"),
            "match_rank_order": self.match_rank,
            "in_stock": self.in_stock,
            "price_num": self.price_num,
        }


RemoteQueryFn = Callable[..., list[dict[str, Any]]]


def load_remote_registry_from_env(
    *,
    registry_env: str = "EQUIPMENT_REMOTE_CATALOGS",
    environ: dict[str, str] | None = None,
) -> list[CatalogSource]:
    env = environ if environ is not None else dict(os.environ)
    raw = env.get(registry_env) or "[]"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out: list[CatalogSource] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        dsn_env = str(item.get("dsn_env") or "").strip() or None
        dsn = env.get(dsn_env) if dsn_env else None
        rc = item.get("row_count")
        row_count = int(rc) if isinstance(rc, (int, float)) else None
        out.append(
            CatalogSource(
                id=cid,
                name=str(item.get("name") or cid),
                kind="remote",
                column_map=parse_column_map(item.get("column_map")),
                row_count=row_count,
                dsn=dsn if isinstance(dsn, str) and dsn.strip() else None,
                table=str(item.get("table") or "").strip() or None,
                dsn_env=dsn_env,
            )
        )
    return out


def local_merged_source(
    workspace_root: Path,
    *,
    catalog_id: str = "local-merged",
    name: str = "Local catalogs (merged)",
) -> CatalogSource | None:
    path = workspace_root / "catalogs" / "catalog.sqlite"
    if not path.is_file():
        return None
    # Local merge already canonical — identity map
    identity = {k: k for k in CANONICAL_FIELDS}
    return CatalogSource(
        id=catalog_id,
        name=name,
        kind="local",
        column_map=identity,
        sqlite_path=path,
    )


def list_catalog_sources(
    workspace_root: Path,
    *,
    environ: dict[str, str] | None = None,
) -> list[CatalogSource]:
    sources: list[CatalogSource] = []
    local = local_merged_source(workspace_root)
    if local is not None:
        sources.append(local)
    sources.extend(load_remote_registry_from_env(environ=environ))
    return sources


def _quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def _split_table(table: str) -> tuple[str, str]:
    m = _TABLE_RE.match(table.strip())
    if not m:
        raise ValueError(f"unsafe table name: {table!r}")
    schema = m.group("schema") or "public"
    return schema, m.group("table")


def _stock_sql_pg(col_sql: str) -> tuple[str, list[Any]]:
    """PostgreSQL predicate: in-stock only (NOT on-order patterns)."""
    # Empty / whitespace
    parts = [
        f"({col_sql} IS NOT NULL)",
        f"(btrim({col_sql}::text) <> '')",
    ]
    params: list[Any] = []
    # Exact denylist (lower(trim))
    denylist = sorted(_ON_ORDER_EXACT - {""})
    if denylist:
        placeholders = ", ".join(["%s"] * len(denylist))
        parts.append(f"(lower(btrim({col_sql}::text)) NOT IN ({placeholders}))")
        params.extend(denylist)
    return " AND ".join(parts), params


def _stock_sql_sqlite(col_sql: str) -> tuple[str, list[Any]]:
    parts = [
        f"({col_sql} IS NOT NULL)",
        f"(trim({col_sql}) <> '')",
    ]
    params: list[Any] = []
    denylist = sorted(_ON_ORDER_EXACT - {""})
    if denylist:
        placeholders = ", ".join(["?"] * len(denylist))
        parts.append(f"(lower(trim({col_sql})) NOT IN ({placeholders}))")
        params.extend(denylist)
    return " AND ".join(parts), params


def _match_mode(part_number: str | None, query: str | None) -> tuple[str, str]:
    """Return (mode, needle) where mode in exact_pn|pn_prefix|title|scan."""
    pn = (part_number or "").strip()
    q = (query or "").strip()
    if pn:
        return "exact_pn", pn
    if q:
        # Short token without spaces → try as PN prefix first when calling search
        return "title", q
    return "scan", ""


def _hit_from_mapped(
    *,
    catalog_id: str,
    source_name: str,
    mapped: dict[str, str],
    match_rank: int,
) -> CatalogHit:
    return CatalogHit(
        catalog_id=catalog_id,
        source_catalog=source_name,
        values=mapped,
        match_rank=match_rank,
        in_stock=is_in_stock(mapped.get("lead_time")),
        price_num=parse_price(mapped.get("price")),
    )


def query_local_sqlite(
    source: CatalogSource,
    *,
    part_number: str | None = None,
    query: str | None = None,
    brand: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    in_stock_only: bool = True,
    fetch_limit: int = 40,
) -> list[CatalogHit]:
    if source.sqlite_path is None or not source.sqlite_path.is_file():
        return []
    fetch_limit = max(1, min(int(fetch_limit), 500))
    uri = f"file:{source.sqlite_path.as_posix()}?mode=ro"
    cmap = source.column_map or {k: k for k in CANONICAL_FIELDS}
    conn = sqlite3.connect(uri, uri=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(rows)").fetchall()]
        if not cols:
            return []
        colset = set(cols)

        def src_col(canon: str) -> str | None:
            name = cmap.get(canon) or (canon if canon in colset else None)
            if name and name in colset:
                return name
            return None

        where: list[str] = []
        params: list[Any] = []
        match_rank = MATCH_OTHER

        pn = (part_number or "").strip()
        tq = (query or "").strip()
        pn_col = src_col("part_number")
        title_col = src_col("title")
        brand_col = src_col("brand")
        price_col = src_col("price")
        lead_col = src_col("lead_time")

        if pn and pn_col:
            where.append(f'lower("{pn_col}") = lower(?)')
            params.append(pn)
            match_rank = MATCH_EXACT_PN
        elif tq and pn_col and " " not in tq and len(tq) >= 3:
            # Prefer PN prefix when query looks like a code
            where.append(f'lower("{pn_col}") LIKE lower(?) || \'%\'')
            params.append(tq)
            match_rank = MATCH_PN_PREFIX
        elif tq and title_col:
            where.append(f'"{title_col}" LIKE ?')
            params.append(f"%{tq}%")
            match_rank = MATCH_TITLE
        elif tq:
            # Fallback: any column (still limited)
            clauses = " OR ".join(f'"{c}" LIKE ?' for c in cols)
            where.append(f"({clauses})")
            params.extend([f"%{tq}%"] * len(cols))
            match_rank = MATCH_OTHER

        if brand and brand_col:
            where.append(f'lower("{brand_col}") = lower(?)')
            params.append(brand.strip())

        # Price filters applied in Python after parse (SQLite text prices messy)
        if in_stock_only and lead_col:
            stock_sql, stock_params = _stock_sql_sqlite(f'"{lead_col}"')
            where.append(stock_sql)
            params.extend(stock_params)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = ""
        if price_col:
            order_sql = f' ORDER BY CAST(replace(replace("{price_col}", ",", "."), " ", "") AS REAL) ASC'
        select_cols = ", ".join(f'"{c}"' for c in cols)
        # Over-fetch a bit for post price filter
        sql = f"SELECT {select_cols} FROM rows{where_sql}{order_sql} LIMIT ?"
        params.append(fetch_limit * 3 if (price_min is not None or price_max is not None) else fetch_limit)
        rows = conn.execute(sql, params).fetchall()
        hits: list[CatalogHit] = []
        for row in rows:
            raw = {cols[i]: row[i] for i in range(len(cols))}
            mapped = apply_column_map(raw, cmap)
            # Local merged already has source_catalog
            if "source_catalog" in raw and raw["source_catalog"]:
                mapped_src = str(raw["source_catalog"])
            else:
                mapped_src = source.name
            hit = _hit_from_mapped(
                catalog_id=source.id,
                source_name=mapped_src,
                mapped=mapped,
                match_rank=match_rank,
            )
            if price_min is not None and (hit.price_num is None or hit.price_num < price_min):
                continue
            if price_max is not None and (hit.price_num is None or hit.price_num > price_max):
                continue
            if in_stock_only and not hit.in_stock:
                continue
            hits.append(hit)
            if len(hits) >= fetch_limit:
                break
        return hits
    finally:
        conn.close()


def _pg_connect_and_fetch(
    dsn: str,
    sql: str,
    params: Sequence[Any],
) -> list[dict[str, Any]]:
    """Run a parameterized SELECT; prefer asyncpg, else psycopg."""
    try:
        import asyncio

        import asyncpg

        async def _run() -> list[dict[str, Any]]:
            # asyncpg uses $1 — convert from %s
            ap_sql = sql
            ap_params = list(params)
            if "%s" in ap_sql:
                i = 0

                def repl(_: Any) -> str:
                    nonlocal i
                    i += 1
                    return f"${i}"

                ap_sql = re.sub(r"%s", repl, ap_sql)
            conn = await asyncpg.connect(dsn, timeout=30)
            try:
                records = await conn.fetch(ap_sql, *ap_params)
                return [dict(r) for r in records]
            finally:
                await conn.close()

        return asyncio.run(_run())
    except ImportError:
        pass

    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, list(params))
                cols = [d.name for d in cur.description] if cur.description else []
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    except ImportError as exc:
        raise RuntimeError(
            "Remote catalog search requires asyncpg or psycopg in the agent runtime"
        ) from exc


def query_remote_postgres(
    source: CatalogSource,
    *,
    part_number: str | None = None,
    query: str | None = None,
    brand: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    in_stock_only: bool = True,
    fetch_limit: int = 40,
    query_fn: RemoteQueryFn | None = None,
) -> list[CatalogHit]:
    if not source.dsn or not source.table:
        return []
    fetch_limit = max(1, min(int(fetch_limit), 500))
    cmap = source.column_map
    if not cmap.get("title") or not cmap.get("price"):
        # Incomplete map — refuse rather than scan raw million-row table
        return []

    schema, table = _split_table(source.table)
    table_sql = f"{_quote_ident(schema)}.{_quote_ident(table)}"

    def qcol(canon: str) -> str | None:
        src = cmap.get(canon)
        if not src or not _IDENT_RE.match(src):
            return None
        return _quote_ident(src)

    select_map: list[tuple[str, str]] = []
    for canon in CANONICAL_FIELDS:
        src = cmap.get(canon)
        if src and _IDENT_RE.match(src):
            select_map.append((canon, _quote_ident(src)))
    if not select_map:
        return []
    select_sql = ", ".join(f"{src} AS {_quote_ident(canon)}" for canon, src in select_map)

    where: list[str] = []
    params: list[Any] = []
    match_rank = MATCH_OTHER

    pn = (part_number or "").strip()
    tq = (query or "").strip()
    pn_c = qcol("part_number")
    title_c = qcol("title")
    brand_c = qcol("brand")
    price_c = qcol("price")
    lead_c = qcol("lead_time")

    if pn and pn_c:
        where.append(f"lower({pn_c}::text) = lower(%s)")
        params.append(pn)
        match_rank = MATCH_EXACT_PN
    elif tq and pn_c and " " not in tq and len(tq) >= 3:
        where.append(f"lower({pn_c}::text) LIKE lower(%s) || '%'")
        params.append(tq)
        match_rank = MATCH_PN_PREFIX
    elif tq and title_c:
        where.append(f"{title_c}::text ILIKE %s")
        params.append(f"%{tq}%")
        match_rank = MATCH_TITLE
    else:
        # No selective predicate — still require hard LIMIT but skip open scan of huge tables
        if not where and (source.row_count or 0) > 100_000 and not brand and price_min is None:
            return []

    if brand and brand_c:
        where.append(f"lower({brand_c}::text) = lower(%s)")
        params.append(brand.strip())

    if price_c and price_min is not None:
        where.append(
            f"NULLIF(regexp_replace(replace({price_c}::text, ',', '.'), '[^0-9.\\-]', '', 'g'), '')::float >= %s"
        )
        params.append(price_min)
    if price_c and price_max is not None:
        where.append(
            f"NULLIF(regexp_replace(replace({price_c}::text, ',', '.'), '[^0-9.\\-]', '', 'g'), '')::float <= %s"
        )
        params.append(price_max)

    if in_stock_only and lead_c:
        stock_sql, stock_params = _stock_sql_pg(f"{lead_c}::text")
        where.append(stock_sql)
        params.extend(stock_params)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = ""
    if price_c:
        order_sql = (
            " ORDER BY NULLIF(regexp_replace(replace("
            f"{price_c}::text, ',', '.'), '[^0-9.\\-]', '', 'g'), '')::float ASC NULLS LAST"
        )
    sql = f"SELECT {select_sql} FROM {table_sql}{where_sql}{order_sql} LIMIT %s"
    params.append(fetch_limit)

    runner = query_fn or _pg_connect_and_fetch
    try:
        rows = runner(source.dsn, sql, params)
    except Exception:
        return []

    hits: list[CatalogHit] = []
    for row in rows:
        # Already aliased to canonical names
        mapped = {k: ("" if row.get(k) is None else str(row.get(k))) for k in CANONICAL_FIELDS}
        hit = _hit_from_mapped(
            catalog_id=source.id,
            source_name=source.name,
            mapped=mapped,
            match_rank=match_rank,
        )
        if in_stock_only and not hit.in_stock:
            continue
        hits.append(hit)
    return hits


def search_catalogs(
    sources: Sequence[CatalogSource],
    *,
    part_number: str | None = None,
    query: str | None = None,
    brand: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    in_stock_only: bool = True,
    catalog_ids: Sequence[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    remote_query_fn: RemoteQueryFn | None = None,
) -> dict[str, Any]:
    """Fan-out search, merge by match_rank then price, paginate."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    # Per-source fetch enough to fill page after merge
    fetch_limit = min(500, offset + limit)

    wanted = set(catalog_ids) if catalog_ids else None
    all_hits: list[CatalogHit] = []
    errors: list[dict[str, str]] = []

    for src in sources:
        if wanted is not None and src.id not in wanted:
            continue
        try:
            if src.kind == "local":
                hits = query_local_sqlite(
                    src,
                    part_number=part_number,
                    query=query,
                    brand=brand,
                    price_min=price_min,
                    price_max=price_max,
                    in_stock_only=in_stock_only,
                    fetch_limit=fetch_limit,
                )
            else:
                hits = query_remote_postgres(
                    src,
                    part_number=part_number,
                    query=query,
                    brand=brand,
                    price_min=price_min,
                    price_max=price_max,
                    in_stock_only=in_stock_only,
                    fetch_limit=fetch_limit,
                    query_fn=remote_query_fn,
                )
            all_hits.extend(hits)
        except Exception as exc:  # noqa: BLE001
            errors.append({"catalog_id": src.id, "error": str(exc)})

    def sort_key(h: CatalogHit) -> tuple[int, float, str]:
        price_key = h.price_num if h.price_num is not None else float("inf")
        return (h.match_rank, price_key, h.values.get("title") or "")

    all_hits.sort(key=sort_key)
    page = all_hits[offset : offset + limit]
    return {
        "items": [h.as_dict() for h in page],
        "total_returned": len(page),
        "offset": offset,
        "limit": limit,
        "scanned_hits": len(all_hits),
        "errors": errors,
        "sort": ["match_rank", "price_asc"],
        "in_stock_only": in_stock_only,
    }


def ensure_local_catalog_indexes(db_path: Path) -> None:
    """Create helpful indexes on merged catalog.sqlite (idempotent)."""
    if not db_path.is_file():
        return
    conn = sqlite3.connect(db_path.as_posix())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(rows)").fetchall()}
        if "part_number" in cols:
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_rows_part_number_lower '
                'ON rows (lower("part_number"))'
            )
        if "title" in cols:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_rows_title ON rows ("title")')
        if "brand" in cols:
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_rows_brand_lower ON rows (lower("brand"))'
            )
        conn.commit()
    finally:
        conn.close()
