# M06 — MCP tools catalog (Commerce → Prodavan)

Полный маппинг legacy Commerce MCP на namespace Prodavan. Формат tool: `{namespace}.{method}`.

## Сводная таблица серверов

| Commerce server | Prodavan server | Namespace |
| --- | --- | --- |
| `commerce-search` | `prodavan-catalog` | `catalog.*` |
| `commerce-s4b` | `prodavan-s4b` | `s4b.*` |
| `commerce-offers` | `prodavan-offers` | `offers.*`, `specs.*` |
| `commerce-pipeline` | `prodavan-pipeline` | `pipeline.*` |
| `commerce-equipment` | `prodavan-equipment` | `equipment.*` |
| `commerce-extract` | `prodavan-extract` | `extract.*` |
| _(новый)_ | `prodavan-integrations` | `integrations.*` |

## prodavan-catalog (`catalog.*`)

| Commerce | Prodavan | Args | Notes |
| --- | --- | --- | --- |
| `list_databases` | `catalog.list_databases` | — | Только БД кабинета |
| `describe_database` | `catalog.describe_database` | `database` | |
| `query_database` | `catalog.query_database` | `database`, `sql`, `limit?` | Read-only |
| `distinct_values` | `catalog.distinct_values` | `database`, `column` | |
| `search_by_part_number` | `catalog.search_by_part_number` | `part_number`, `limit?` | Exact P/N |
| `search_all` | `catalog.search_all` | `query`, `limit_per_db?` | |
| `search_catalog_text` | `catalog.search_text` | `query`, `limit?` | |
| `list_catalog_files` | `catalog.list_files` | — | |
| `list_web_shops` | `integrations.list_web_shops` | — | **Перенесено в M05** |
| `s4b-cache` virtual DB | `catalog.query_database` (`database=s4b-cache`) | | Cache scoped per cabinet |

## prodavan-s4b (`s4b.*`)

| Commerce | Prodavan | Args | Notes |
| --- | --- | --- | --- |
| `s4b_status` | `s4b.status` | — | |
| `s4b_ping` | `s4b.ping` | — | |
| `s4b_list_trusted_sellers` | `s4b.list_trusted_sellers` | — | Per cabinet list |
| `s4b_set_max_age_days` | `s4b.set_max_age_days` | `days` | |
| `s4b_search_keywords` | `s4b.search_keywords` | `keywords`, `trusted_only?` | in_stock implicit |
| `s4b_search_articles` | `s4b.search_articles` | `articles[]`, `trusted_only?` | |
| `s4b_parse_xlsx` | `s4b.parse_xlsx` | `path` | Path in cabinet storage |
| `s4b_search_xlsx` | `s4b.search_xlsx` | `path`, `trusted_only?` | |
| `s4b_unpack_zip` | `s4b.unpack_zip` | `path` | |
| `s4b_cache_search` | `s4b.cache_search` | `query`, `type?` | |
| `s4b_cache_purge` | `s4b.cache_purge` | `max_age_days?` | Deny in `kp` profile |
| _(removed)_ | `include_on_order` | — | **Не существует** |

## prodavan-offers (`offers.*`, `specs.*`)

| Commerce | Prodavan | Args | Notes |
| --- | --- | --- | --- |
| `offers_upsert_lineitem` | `offers.upsert_lineitem` | `project`, `item_json` | `project` → cabinet project slug |
| `offers_add_variants` | `offers.add_variants` | `project`, `offers_json` | |
| `offers_list_best` | `offers.list_best` | `project` | |
| `offers_list_lineitems` | `offers.list_lineitems` | `project`, `run_id?` | |
| `offers_list_variants` | `offers.list_variants` | `project`, `n?`, `role?` | |
| `offers_get` | `offers.get` | `project`, `n` | |
| `offers_score` | `offers.score` | `project`, `n`, `scores_json` | |
| `offers_set_best` | `offers.set_best` | `project`, `n` | |
| `offers_delete` | `offers.delete` | `project`, `n` | |
| `offers_import_run` | `offers.import_run` | `project`, `run_id`, `runs_dir?` | |
| `specs_upsert` | `specs.upsert` | `project`, `card_json` | |
| `specs_link` | `specs.link` | `project`, `n`, `spec_key` | |
| `specs_for_variant` | `specs.for_variant` | `project`, `n` | |

## prodavan-pipeline (`pipeline.*`)

| Commerce | Prodavan | Args | Notes |
| --- | --- | --- | --- |
| `new_run` | `pipeline.new_run` | `input_path` | → `projects/{slug}/runs/` |
| `parse_spec` | `pipeline.parse_spec` | `run_id` | |
| `classify_rows` | `pipeline.classify_rows` | `run_id` | |
| `save_lineitems` | `pipeline.save_lineitems` | `run_id`, `items_json` | |
| `search_offers` | `pipeline.search_offers` | `run_id`, `allow_api?`, `allow_web?` | Respects M05 policy |
| `rank_offers` | `pipeline.rank_offers` | `run_id` | |
| `validate_run` | `pipeline.validate_run` | `run_id` | Deny in default `kp` |
| `log_source` | `pipeline.log_source` | `run_id`, `entry_json` | |

## prodavan-equipment (`equipment.*`)

| Commerce | Prodavan | Args |
| --- | --- | --- |
| `equipment_list` | `equipment.list` | `project`, `query?`, `limit?` |
| `equipment_get` | `equipment.get` | `project`, `key` |
| `equipment_upsert` | `equipment.upsert` | `project`, `card_json` |
| `equipment_delete` | `equipment.delete` | `project`, `key` |

## prodavan-extract (`extract.*`)

| Commerce | Prodavan | Args |
| --- | --- | --- |
| `extract_table` | `extract.extract_table` | `path`, `sheet?` |

## prodavan-integrations (`integrations.*`)

См. [M05 mcp-tools.md](../M05-integrations/mcp-tools.md).

## Profile tool filter (детально)

### Profile `kp` (Спека → КП)

**allowed_servers:** all official except none  
**tool_denylist:**

```yaml
- s4b.cache_purge
- pipeline.validate_run
- offers.delete
- equipment.delete
```

**max_tools:** 45

### Profile `catalog-only`

**allowed_servers:** `prodavan-catalog`  
**tool_allowlist:**

```yaml
- catalog.list_databases
- catalog.describe_database
- catalog.query_database
- catalog.search_by_part_number
- catalog.search_text
- catalog.search_all
```

### Profile `readonly-audit`

**tool_allowlist:**

```yaml
- offers.list_best
- offers.list_lineitems
- offers.list_variants
- offers.get
- integrations.get_policy
- s4b.status
- catalog.list_databases
```

### Profile `admin-debug`

**allowed_servers:** all  
**tool_denylist:** `[]`  
**requires role:** `tenant.admin`

## Env injection (cabinet context)

| Variable | Source |
| --- | --- |
| `CABINET_ID` | JWT |
| `TENANT_ID` | JWT |
| `PROJECT_ROOT` | `tenants/{tid}/cabinets/{cid}/` |
| `RUNS_DIR` | `{PROJECT_ROOT}/runs` |
| `COMMERCE_PROJECT` | project slug (legacy alias) |
| `S4B_LOGIN` | M05 decrypted creds |
| `S4B_PASSWORD` | M05 decrypted creds |

## Breaking changes vs Commerce

| change | migration |
| --- | --- |
| Global `catalogs/` | Per-cabinet path |
| `list_web_shops` namespace | `integrations.list_web_shops` |
| `include_on_order` | Remove from agent prompts |
| Snake server ids | kebab in registry, dot in tools |
| Telegram-only MCP | Web session via M07 |

## Discovery contract

MCP SDK `tools/list` → normalize to `fq_name = {namespace}.{snake}`.  
Timeout: 30s. Retry: 2. Failure → installation `state=error`.

## Metrics (M09)

- `prodavan_mcp_discovery_duration_seconds{server_id}`
- `prodavan_mcp_tool_calls_total{server_id,tool,status}`
- `prodavan_mcp_tools_exposed{profile_id}` gauge
