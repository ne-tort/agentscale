# Example: Equipment matching module

Hub on **Данные** (`nav.placement: data`): catalogs (local file → SQLite artifact → merged project DB, **or** remote PostgreSQL live), request lines, found offers with single-select, equipment characteristics + component types, PC/server builds.

Product seed: `mod_equipment` — see `product_module_seeds.py`.

## Hybrid storage

| Data | SoT |
|------|-----|
| Module rows (UI + agent) | Postgres instance JSONB |
| Local price index | Content blob SQLite (`artifact_ref`) — rematerialize input |
| Remote price source | External PostgreSQL (live); DSN in Vault `secret_ref` |
| Project search DB (local only) | Materialize `merge_mapped_sqlite` → `/workspace/catalogs/catalog.sqlite` |
| Remote DSN in Pod | `EQUIPMENT_CATALOG_DSN_<ROW>` + `EQUIPMENT_REMOTE_CATALOGS` JSON (no data copy) |

## Tables (sketch)

- `catalogs` — `name`, `source_kind` (`local`\|`remote`), `source_file`, `remote_dsn` (`secret_ref`), `remote_user` / `remote_password` (when URL has no creds; password is `secret_ref`), `remote_database` (SoT; UI `remote_database_picker` when URL has no `/dbname`), `remote_table` (SoT; UI `remote_table_picker`), `artifact_ref`, `status`, `paused`, `row_count`, `columns_json`, `column_map`, `error`, `project_ids`
- `request_lines` — `title`, `part_number`, `qty`, `found_count`, `selected_offer_id`, `status`
- `found_offers` — `line_id` (ref), `title`, `part_number`, `price`, `catalog_id`, `score`, `match_kind`, `is_selected`, `source_title`
- `equipment_types` — `name`, `sort_order`, `build_scope` (`all`|`pc`|`server`), `fields_json` (`[{key,label}]`); seed 13 PC/server types (incl. case fans; RAID/HBA/backplane/BMC server-only)
- `equipment_items` — `name`, `offer_id`/`offer_title`, `type_id`/`type_name`, `part_number`, `qty`, `attrs` (string map by field key)
- `equipment_builds` — `name`, `build_kind` (`pc`|`server`), `slots` (`{etype_id: item_row_id}`), denorm `components_count` / `price_total`
- `trusted_sellers` — `name`, `aliases` (comma-separated); CRUD only
- `web_shops` — `name`, `url`, `cookies` (free-form paste via `text_editor` nav page); CRUD only
- `s4b_settings` — `name`, `base_url`, `login`, `password` (`secret_ref` via core `value` + `secret: true`), `mcp_zip` (`file_ref`), `enabled` (`pause_toggle` invert), `project_ids` (empty = all)

## Meta primitives

- Hub tiles → collections with `scaffold.title` (AppBar titles, not view slugs)
- Catalogs: collection + `inline_add` + `added_at` from `row.created_at`; settings with **Тип** (`source_kind`), local `file_upload` / remote DSN + optional login/password + **База** picker (`content.list_remote_sql_databases`) + **Таблица** picker (`content.list_remote_sql_tables`), `column_map` (autosave), `project_multiselect`, `paused`. No default `public.offers`; `?table=` may be read from DSN but is stripped before asyncpg.
- Found offers: `inline_add` on title; `line_id` optional `ref` selector in form
- Line row_tap → offers collection with `context_bind` + `selection` → `data.select_row`
- Equipment items: `type_ref_picker` for offer (`found_offers_pick`) then type (`equipment_types_pick`); pick views use `selection.control: switch` + `placement: trailing` + `disable_row_tap`; `schema_attrs` section title «Характеристики»
- Types: list without «Порядок» column (sort still by `sort_order`); `fields_schema_editor` on type settings; seeded latin keys for MCP
- Builds: `build_slots` lists types filtered by `build_scope` vs `build_kind`; pick item via `equipment_items_pick` (ephemeral pickContext `type_id` filter + `map_field: slots`); recompute count/price from linked offers
- Action `content.index_tabular` after **local** catalog file write
- Action `content.probe_remote_sql` after **remote** DSN/table write — connect-only → `draft` + `needs_database` / `needs_table` as needed; full columns+`COUNT(*)` when both set (no SQLite snapshot)
- Actions `content.list_remote_sql_databases` / `content.list_remote_sql_tables` for pickers (Name + switch)
- Materialize `merge_mapped_sqlite` for ready + non-paused **local** catalogs only (remote skipped)
- Materialize `mcp_package` from enabled `s4b_settings.mcp_zip` → `/workspace/packages/{name}` + OpenClaw/mcp.json
- `container_env` / `container_env_secrets`: `S4B_*` from enabled row; remote catalogs via `foreach_rows` → `EQUIPMENT_CATALOG_DSN_*` + `EQUIPMENT_REMOTE_CATALOGS`
- Declarative `mcp_tools` for agent surface; live remote SQL MCP query is a follow-up

## Agent / MCP contract

1. **Search catalogs (RO):** query `/workspace/catalogs/catalog.sqlite` (canonical `rows`: title, price, part_number, supplier, lead_time, source_catalog) via `equipment_catalog_query`. Prefer exact `part_number`, else title LIKE. Do **not** open platform Postgres from the Pod.
2. **List ready catalogs:** `equipment_catalog_list` reads Postgres module rows (`status=ready`, `paused=false`).
3. **Write matches:** upsert into `found_offers` (`equipment_offers_upsert` / rows API) with `line_id`, bump `request_lines.found_count`. Alternatives stay as non-selected siblings; primary selection is UI/`data.select_row` (`is_selected` + parent `selected_offer_id`).
4. **Component types / characteristics:** `equipment_types_list`, `equipment_items_list`, `equipment_items_upsert` — `attrs` values are plain strings keyed by `fields_json[].key`. Link item → offer via `offer_id`.
5. **Builds:** `equipment_builds_list` / `equipment_builds_upsert` — `slots` maps type row id → item row id; `price_total` ≈ Σ `found_offers.price * (item.qty ?? 1)`.
6. **Trusted sellers / web shops:** `trusted_sellers_*`, `web_shops_*` — list/upsert only (no ranking/search logic yet).
7. **S4B:** enabled settings inject `S4B_*` into the Pod; MCP zip unpacks like `mod_mcp` packages. Do **not** connect Pod to platform Redis — future Cache API via [tenant-infra-gateway](../../../12-layer-docs/tenant-infra-gateway.md).
8. **Never** treat Pod FS or dehydrate blobs as SoT for offers/selection — only Postgres module rows survive pause/reload as editable state.

Full definitions live in the product seed; this file is the human summary.
