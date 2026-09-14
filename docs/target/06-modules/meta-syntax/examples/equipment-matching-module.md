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

- `catalogs` — shared (`scope.chats=all`): `name`, `source_kind` (`local`\|`remote`), …, `project_ids`
- `request_lines` — per-chat (`scope.chats=current`): `title`, `part_number`, `qty`, `found_count`, `selected_offer_id`, `status` (+ system `session_id`)
- `found_offers` — per-chat: `line_id` (ref → `request_lines`), … (+ `session_id`)
- `equipment_types` — shared: `name`, `sort_order`, `build_scope`, `fields_json`
- `equipment_items` — per-chat: characteristics / items
- `equipment_builds` — per-chat: PC/server builds
- `trusted_sellers` — shared: `name`, `aliases`
- `web_shops` — shared: `name`, `url`, `cookies`
- `s4b_settings` — per-chat: S4B credentials / MCP zip (+ `session_id`)

## Meta primitives

- Hub tiles → collections with `scaffold.title` (AppBar titles, not view slugs)
- Per-chat hub tiles (`request_lines`, `found_offers`, `equipment_items`, `equipment_builds`, `s4b_settings`) set **`scope.active_chat: required`** — hidden in cabinet Data hub until an active chat is selected (`selectedSessionId`); shared tiles stay visible
- Cabinet→project bind defaults to **global** (shared cabinet SoT); UI may switch to local leaf
- Catalogs: collection + `inline_add`; settings with **Тип** (`source_kind`), local `file_upload` / remote DSN + optional login/password + **База** / **Таблица** pickers, `column_map` (autosave), `project_multiselect`, `paused`, reindex interval (remote)
- Found offers: `inline_add` on title; **Запрос** = `line_id` `type_ref_picker` → `request_lines_pick`
- Action `content.index_opensearch` after catalog write / toolbar «Переиндексировать» (Celery wipe+bulk; header probe sync for local)
- Action `content.probe_remote_sql` after **remote** DSN/table write — columns + `COUNT(*)` for UI map
- Actions `content.list_remote_sql_databases` / `content.list_remote_sql_tables` for pickers
- Materialize: `catalog_manifest` JSON only (no merged SQLite); S4B `mcp_package`
- `container_env` / secrets: `S4B_*` only (equipment DSN foreach removed)
- MCP `prodavan-equipment`: catalog tools → Pod OpenSearch APIs; SoT rows → Bridge modules API
