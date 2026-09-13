# Example: Equipment matching module

Hub on **Данные** (`nav.placement: data`): catalogs (local/remote → OpenSearch), request lines, found offers with single-select, equipment characteristics + component types, PC/server builds.

Product seed: `mod_equipment` — see `product_module_seeds.py`.

## Storage

| Data | SoT |
|------|-----|
| Module rows (UI + agent) | Postgres instance JSONB |
| Local price index | MinIO `source_file` → OpenSearch (`equipment` / `c_{row_id}`) via Celery |
| Remote price source | External PostgreSQL → same OpenSearch index (probe for headers; full scan indexed) |
| Agent search | Pod `…/equipment/catalog-search` → Search Index BC (**no** `catalog.sqlite`, **no** `EQUIPMENT_*`) |

## Tables (sketch)

- `catalogs` — `name`, `source_kind` (`local`\|`remote`), `source_file`, `remote_dsn` (`secret_ref`), `remote_user` / `remote_password`, `remote_database`, `remote_table`, `status`, `paused`, `row_count`, `columns_json`, `column_map`, `last_indexed_at`, `reindex_interval_hours`, `index_name`, `error`, `project_ids`
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
- Catalogs: collection + `inline_add`; settings with **Тип** (`source_kind`), local `file_upload` / remote DSN + optional login/password + **База** / **Таблица** pickers, `column_map` (autosave), `project_multiselect`, `paused`, reindex interval (remote)
- Found offers: `inline_add` on title; **Запрос** = `line_id` `type_ref_picker` → `request_lines_pick`
- Action `content.index_opensearch` after catalog write / toolbar «Переиндексировать» (Celery wipe+bulk; header probe sync for local)
- Action `content.probe_remote_sql` after **remote** DSN/table write — columns + `COUNT(*)` for UI map
- Actions `content.list_remote_sql_databases` / `content.list_remote_sql_tables` for pickers
- Materialize: `catalog_manifest` JSON only (no merged SQLite); S4B `mcp_package`
- `container_env` / secrets: `S4B_*` only (equipment DSN foreach removed)
- MCP `prodavan-equipment`: catalog tools → Pod OpenSearch APIs; SoT rows → Bridge modules API
