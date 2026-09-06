# Example: Equipment matching module

Hub on **Данные** (`nav.placement: data`): catalogs (file → SQLite artifact), request lines, found offers with single-select.

Product seed: `mod_equipment` — see `product_module_seeds.py`.

## Hybrid storage

| Data | SoT |
|------|-----|
| Module rows (UI + agent) | Postgres instance JSONB |
| Parsed price index | Content blob SQLite → materialize `/workspace/catalogs/{row_id}.sqlite` |

## Tables (sketch)

- `catalogs` — `name`, `source_file` (file_ref), `artifact_ref`, `status`, `row_count`, `columns_json`, `error`
- `request_lines` — `title`, `part_number`, `qty`, `found_count`, `selected_offer_id`, `status`
- `found_offers` — `line_id` (ref), `title`, `part_number`, `price`, `catalog_id`, `score`, `match_kind`, `is_selected`, `source_title`

## Meta primitives

- Hub tiles → collections with `scaffold.title` (AppBar titles, not view slugs)
- Catalogs: collection + `inline_add`; settings detail with `file_upload` (`empty_style: warning`, `subtitle_from: row_count`)
- Found offers: `inline_add` on title; `line_id` optional `ref` selector in form
- Line row_tap → offers collection with `context_bind` + `selection` → `data.select_row`
- Action `content.index_tabular` after catalog file write
- Materialize `copy_blob` of `artifact_ref` when `status=ready`
- Declarative `mcp_tools` for agent surface

## Agent / MCP contract

1. **Search catalogs (RO):** use materialized files under `/workspace/catalogs/{row_id}.sqlite` (table `rows`) via `equipment_catalog_query` / helper `catalog_sqlite_query`. Prefer exact `part_number`, else text query. Do **not** open platform Postgres from the Pod.
2. **List ready catalogs:** `equipment_catalog_list` reads Postgres module rows (`status=ready`); use row ids as SQLite filenames.
3. **Write matches:** upsert into `found_offers` (`equipment_offers_upsert` / rows API) with `line_id`, bump `request_lines.found_count`. Alternatives stay as non-selected siblings; primary selection is UI/`data.select_row` (`is_selected` + parent `selected_offer_id`).
4. **Never** treat Pod FS or dehydrate blobs as SoT for offers/selection — only Postgres module rows survive pause/reload as editable state.

Full definitions live in the product seed; this file is the human summary.
