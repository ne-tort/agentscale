# Example: Equipment matching module

Hub on **Данные** (`nav.placement: data`): catalogs (file → SQLite artifact → merged project DB), request lines, found offers with single-select, equipment characteristics + component types.

Product seed: `mod_equipment` — see `product_module_seeds.py`.

## Hybrid storage

| Data | SoT |
|------|-----|
| Module rows (UI + agent) | Postgres instance JSONB |
| Raw parsed price index | Content blob SQLite (`artifact_ref`) — rematerialize input |
| Project search DB | Materialize `merge_mapped_sqlite` → `/workspace/catalogs/catalog.sqlite` |

## Tables (sketch)

- `catalogs` — `name`, `source_file`, `artifact_ref`, `status`, `paused`, `row_count`, `columns_json`, `column_map`, `error`, `project_ids`
- `request_lines` — `title`, `part_number`, `qty`, `found_count`, `selected_offer_id`, `status`
- `found_offers` — `line_id` (ref), `title`, `part_number`, `price`, `catalog_id`, `score`, `match_kind`, `is_selected`, `source_title`
- `equipment_types` — `name`, `sort_order`, `fields_json` (`[{key,label}]`); seed 12 PC/server types
- `equipment_items` — `name`, `type_id`/`type_name`, `part_number`, `qty`, `attrs` (string map by field key)

## Meta primitives

- Hub tiles → collections with `scaffold.title` (AppBar titles, not view slugs)
- Catalogs: collection + `inline_add` + `added_at` from `row.created_at`; settings with `file_upload`, `column_map`, `project_multiselect`, `paused`
- Found offers: `inline_add` on title; `line_id` optional `ref` selector in form
- Line row_tap → offers collection with `context_bind` + `selection` → `data.select_row`
- Equipment items: `type_ref_picker` → `equipment_types_pick` (`selection.set_on_context`); `schema_attrs` after type chosen
- Types: list + `fields_schema_editor` on type settings; seeded latin keys for MCP
- Action `content.index_tabular` after catalog file write
- Materialize `merge_mapped_sqlite` for ready + non-paused catalogs (skip incomplete maps)
- Declarative `mcp_tools` for agent surface

## Agent / MCP contract

1. **Search catalogs (RO):** query `/workspace/catalogs/catalog.sqlite` (canonical `rows`: title, price, part_number, supplier, lead_time, source_catalog) via `equipment_catalog_query`. Prefer exact `part_number`, else title LIKE. Do **not** open platform Postgres from the Pod.
2. **List ready catalogs:** `equipment_catalog_list` reads Postgres module rows (`status=ready`, `paused=false`).
3. **Write matches:** upsert into `found_offers` (`equipment_offers_upsert` / rows API) with `line_id`, bump `request_lines.found_count`. Alternatives stay as non-selected siblings; primary selection is UI/`data.select_row` (`is_selected` + parent `selected_offer_id`).
4. **Component types / characteristics:** `equipment_types_list`, `equipment_items_list`, `equipment_items_upsert` — `attrs` values are plain strings keyed by `fields_json[].key`.
5. **Never** treat Pod FS or dehydrate blobs as SoT for offers/selection — only Postgres module rows survive pause/reload as editable state.

Full definitions live in the product seed; this file is the human summary.
