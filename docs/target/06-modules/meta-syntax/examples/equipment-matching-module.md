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
- `found_offers` — `line_id` (ref **Запрос** → `request_lines` via `request_lines_pick`), `title`, `part_number`, `brand`, `price`, `catalog_id` (provenance only, not on form), `score`, `match_kind`, `is_selected`, `source_title` (denorm from line title)
- `equipment_types` — `name`, `sort_order`, `build_scope` (`all`|`pc`|`server`), `fields_json` (`[{key,label}]`); seed 13 PC/server types (incl. case fans; RAID/HBA/backplane/BMC server-only)
- `equipment_items` — `name`, `offer_id`/`offer_title`, `type_id`/`type_name`, `part_number`, `qty`, `attrs` (string map by field key)
- `equipment_builds` — `name`, `build_kind` (`pc`|`server`), `slots` (`{etype_id: item_row_id}`), denorm `components_count` / `price_total`
- `trusted_sellers` — `name`, `aliases` (comma-separated); CRUD only
- `web_shops` — `name`, `url`, `cookies` (free-form paste via `text_editor` nav page); CRUD only
- `s4b_settings` — `name`, `base_url`, `login`, `password` (`secret_ref` via core `value` + `secret: true`), `mcp_zip` (`file_ref`), `enabled` (`pause_toggle` invert), `project_ids` (empty = all)

## Meta primitives

- Hub tiles → collections with `scaffold.title` (AppBar titles, not view slugs)
- Catalogs: collection + `inline_add` + `added_at` from `row.created_at`; settings with **Тип** (`source_kind`), local `file_upload` / remote DSN + optional login/password + **База** picker (`content.list_remote_sql_databases`) + **Таблица** picker (`content.list_remote_sql_tables`), `column_map` (autosave), `project_multiselect`, `paused`. No default `public.offers`; `?table=` may be read from DSN but is stripped before asyncpg.
- Found offers: `inline_add` on title; **Запрос** = `line_id` `type_ref_picker` → `request_lines_pick` (`also_copy` title→`source_title`); **БД** (`catalog_id`) not on form
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
- First-party MCP `prodavan-equipment` (workspace): unified local+remote catalog search + typed `request_lines` / `found_offers` tools

## Agent / MCP contract

1. **Search catalogs (RO):** `equipment_catalog_search` (first-party `prodavan-equipment`) — unified local merged SQLite **and** live remote PG via `column_map`. Canonical fields always. Prefer exact `part_number`; default `in_stock_only=true` (empty/dash/нет/под заказ excluded). Sort: match_rank then price. Use `equipment_catalog_sources` to list sources.
2. **Write matches:** `found_offers_upsert` with `line_id` (Запрос), optional `catalog_id` provenance from search; bumps `request_lines.found_count`. Alternatives stay as non-selected siblings; primary selection is UI/`data.select_row`.
3. **Request lines:** `request_lines_list` / `request_lines_upsert` — strict schema, soft nullables.
4. **Component types / characteristics:** declarative `equipment_types_list`, `equipment_items_*` (or generic `prodavan-modules`) — `attrs` by `fields_json[].key`. Link item → offer via `offer_id`.
5. **Builds:** `equipment_builds_*` — `slots` maps type → item; `price_total` ≈ Σ offer price × qty.
6. **Trusted sellers / web shops:** list/upsert only.
7. **S4B:** enabled settings inject `S4B_*`; MCP zip unpacks like `mod_mcp`. Do **not** connect Pod to platform Redis.
8. **Never** treat Pod FS as SoT for offers/selection — only Postgres module rows.

Full definitions live in the product seed; this file is the human summary.
