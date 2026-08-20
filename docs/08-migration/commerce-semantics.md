# Commerce semantics — field-level mapping

Таблицы соответствия полей, сущностей, MCP tools и файловых артеfactов между **Commerce MVP** и **Prodavan**. Семантика домена закупок **сохраняется**; меняются storage, scope и namespacing.

---

## 1. Domain entities

| Commerce concept | Prodavan entity | PostgreSQL table | Notes |
|------------------|-----------------|-------------------|-------|
| Project folder `projects/<name>/` | `Project` | `projects.projects` | `slug` = normalized folder name |
| Run folder `runs/<id>/` | `SpecRun` | `specs.spec_runs` | UUID run id |
| Operator (Telegram user) | `User` + membership | `tenants.users` | Auth via JWT, not Telegram id |
| — | `Tenant` | `tenants.tenants` | New hierarchy level |
| — | `Cabinet` | `tenants.cabinets` | Profile + capabilities |
| Agent session (SDK pool) | `AgentSession` | `agent.sessions` | One pod per session |
| Chat message | `ChatMessage` | `agent.messages` | Replaces Telegram message log |

---

## 2. LineItem (`lineitems.json` / SQLite)

Source schema: `Commerce/schemas/lineitem.schema.json` → `specs.line_items`

| Commerce field | Type | Prodavan column | Transform |
|----------------|------|-----------------|-----------|
| `id` | string | `id` | Direct copy |
| `source.file` | string | `source_json->file` | JSONB subfield |
| `source.sheet` | string\|null | `source_json->sheet` | |
| `source.row` | int\|null | `source_json->row` | |
| `raw_text` | string | `raw_text` | Direct |
| `category` | enum | `category` | Same enum values |
| `manufacturer` | string\|null | `manufacturer` | Direct |
| `model` | string\|null | `model` | Direct |
| `part_number` | string\|null | `part_number` | **No modification** |
| `qty` | number | `qty` | Direct |
| `constraints` | object | `constraints_json` | JSONB |
| `bundle_hint` | bool | `bundle_hint` | Direct |
| `confidence` | 0..1 | `confidence` | Direct |
| `needs_review` | bool | `needs_review` | Direct |
| `clarify_notes` | string\|null | `clarify_notes` | Direct |
| `search_gap` | string\|null | `search_gap` | Direct |
| — | — | `tenant_id` | Injected on import |
| — | — | `cabinet_id` | Injected on import |
| — | — | `run_id` | FK to spec_runs |

SQLite-only columns (`n`, `seq`, `created_at`) map to Prodavan `seq`, timestamps.

---

## 3. Offer (`offers.json`)

Source schema: `Commerce/schemas/offer.schema.json` → `specs.offers`

| Commerce field | Type | Prodavan column | Transform |
|----------------|------|-----------------|-----------|
| `id` | string | `id` | Direct |
| `lineitem_id` | string | `lineitem_id` | FK |
| `supplier` | string | `supplier` | Direct |
| `seller_tier` | enum | `seller_tier` | trusted/acceptable/avoid/unknown |
| `sku` | string\|null | `sku` | Direct |
| `part_number` | string\|null | `part_number` | Direct |
| `title` | string | `title` | Direct |
| `price` | number\|null | `price` | Direct |
| `currency` | string\|null | `currency` | Default RUB |
| `vat` | enum | `vat` | included/excluded/unknown |
| `availability` | enum | `availability` | **`on_order` → DROP on import** |
| `lead_time` | string\|null | `lead_time` | Direct |
| `match_type` | enum | `match_type` | exact/equivalent/alternative/unknown |
| `relevance` | 0..1 | `relevance` | Direct |
| `relevance_notes` | string\|null | `relevance_notes` | Direct |
| `source_type` | enum | `source_type` | catalog/api/web/web_clarify |
| `source_ref` | string | `source_ref` | Direct |
| `as_of` | ISO datetime | `as_of` | Parse timestamptz |
| `notes` | string\|null | `notes` | Direct |

**Policy:** `availability=on_order` offers **не импортируются** и **не создаются** в Prodavan pipeline.

---

## 4. Selection (`selection.json`)

Source schema: `Commerce/schemas/selection.schema.json` → derived from `specs.variants.is_best` + role

| Commerce field | Prodavan mapping |
|----------------|------------------|
| `run_id` | `specs.spec_runs.id` |
| `items[].lineitem_id` | `specs.line_items.id` |
| `items[].primary_offer_id` | `specs.variants` where `role=primary` / `is_best=true` |
| `items[].alternative_offer_ids` | `specs.variants` where `role=alternative` |
| `items[].reason` | `specs.variants.notes` or ranking metadata JSONB |

---

## 5. Variant (`commerce.sqlite`)

Source: `Commerce/tools/commerce_db.py` SCHEMA → `specs.variants`

| SQLite column | Prodavan column | Transform |
|---------------|-----------------|-----------|
| `id` | `id` | Direct |
| `lineitem_id` | `lineitem_id` | FK |
| `supplier` | `supplier` | Direct |
| `seller` | `seller` | Direct |
| `seller_tier` | `seller_tier` | Direct |
| `sku` | `sku` | Direct |
| `part_number` | `part_number` | Direct |
| `title` | `title` | Direct |
| `price` | `price` | Direct |
| `currency` | `currency` | Direct |
| `vat` | `vat` | Direct |
| `availability` | `availability` | Map; drop on_order rows |
| `lead_time` | `lead_time` | Direct |
| `match_type` | `match_type` | Direct |
| `relevance` | `relevance` | 0–10 scale preserved |
| `ai_confidence` | `ai_confidence` | Direct |
| `price_score` | `price_score` | Direct |
| `source_type` | `source_type` | Direct |
| `source_ref` | `source_ref` | Direct |
| `notes` | `notes` | Direct |
| `is_best` | `is_best` | Direct (bool) |
| `as_of` | `as_of` | timestamptz |
| `created_at` | `created_at` | timestamptz |
| — | `tenant_id`, `cabinet_id`, `project_id` | Injected |

---

## 6. Specs / Equipment (`commerce.sqlite`)

| SQLite `specs` | Prodavan `specs.specs` |
|----------------|------------------------|
| `id` | `id` |
| `part_number` | `part_number` |
| `category` | `category` |
| `manufacturer` | `manufacturer` |
| `model` | `model` |
| `title` | `title` |
| `attrs_json` | `attrs_json` |
| `notes` | `notes` |
| `sources_json` | `sources_json` |
| `compatibility_json` | `compatibility_json` |
| `updated_at` | `updated_at` |

| SQLite `spec_links` | Prodavan `specs.spec_links` |
|---------------------|----------------------------|
| `spec_id` | `spec_id` |
| `part_number` | `part_number` |
| `variant_n` | map to `variant_id` (text) |
| `lineitem_id` | `lineitem_id` |

---

## 7. Run artifacts (filesystem)

| Commerce file | Prodavan object store path | PG reference |
|---------------|---------------------------|--------------|
| `runs/<id>/rows.json` | `.../runs/<id>/rows.json` | `run_artifacts` |
| `runs/<id>/lineitems.json` | `.../lineitems.json` | + `specs.line_items` |
| `runs/<id>/offers.json` | `.../offers.json` | + `specs.offers` |
| `runs/<id>/selection.json` | `.../selection.json` | + variants ranking |
| `runs/<id>/sources.log` | `.../sources.log` | audit reference |
| `runs/<id>/status.json` | `.../status.json` | `specs.spec_runs.status` |
| `input/*` | `.../input/*` | `input_files_json` |

Run status phases (identical semantics):

| Phase | Commerce | Prodavan |
|-------|----------|----------|
| ingest | ✓ | ✓ |
| classify | ✓ | ✓ |
| search | ✓ | ✓ |
| rank | ✓ | ✓ |
| sqlite | ✓ | `import_variants` |
| review | ✓ | ✓ |
| final | ✓ | ✓ (operator gate) |

---

## 8. MCP tools mapping

| Commerce MCP tool | Prodavan tool | Module |
|-------------------|---------------|--------|
| `commerce-extract.extract_table` | `extract.table` | M02 |
| `commerce-pipeline.new_run` | `pipeline.new_run` | M02 |
| `commerce-pipeline.parse_spec` | `pipeline.parse_spec` | M02 |
| `commerce-pipeline.classify_rows` | `pipeline.classify` | M02 |
| `commerce-pipeline.save_lineitems` | `pipeline.save_lineitems` | M02 |
| `commerce-pipeline.search_offers` | `pipeline.search` | M02 |
| `commerce-pipeline.rank_offers` | `pipeline.rank` | M02 |
| `commerce-pipeline.validate_run` | `pipeline.validate` | M02 |
| `commerce-search.list_databases` | `catalog.list_databases` | M04 |
| `commerce-search.describe_database` | `catalog.describe_database` | M04 |
| `commerce-search.query_database` | `catalog.query_database` | M04 |
| `commerce-search.search_by_part_number` | `catalog.search_pn` | M04 |
| `commerce-search.search_catalog_text` | `catalog.search_text` | M04 |
| `commerce-search.distinct_values` | `catalog.distinct_values` | M04 |
| `commerce-search.list_web_shops` | `integrations.list_web_shops` | M05 |
| `commerce-s4b.s4b_status` | `s4b.status` | M05 |
| `commerce-s4b.s4b_search_articles` | `s4b.search_articles` | M05 |
| `commerce-s4b.s4b_search_keywords` | `s4b.search_keywords` | M05 |
| `commerce-s4b.s4b_list_trusted` | `s4b.list_trusted_sellers` | M05 |
| `commerce-s4b.s4b_cache_*` | `s4b.cache_*` | M05 |
| `commerce-offers.offers_list` | `offers.list` | M02 |
| `commerce-offers.offers_upsert` | `offers.upsert` | M02 |
| `commerce-offers.offers_set_best` | `offers.set_best` | M02 |
| `commerce-offers.specs_*` | `specs.*` / `equipment.*` | M02 |
| `commerce-equipment.equipment_*` | `equipment.*` | M02 |
| `include_on_order` param | **REMOVED** | — |

---

## 9. Integrations & catalogs scope

| Commerce (global) | Prodavan (per cabinet) |
|-------------------|------------------------|
| `.env` `S4B_API_LOGIN/PASSWORD` | `integrations.s4b_credentials` encrypted |
| Bot `/s4b доверенные` | `integrations.s4b_trusted_sellers` |
| `catalogs/web-shop-allowlist.json` | `integrations.web_shop_allowlist` |
| `catalogs/db/*.sqlite` | `catalogs/catalog_databases` + S3 path |
| `catalogs/s4b/` cache | `integrations/s4b-cache/` |
| Global trusted sellers | Per-cabinet trusted list |

### Web shop IDs

| Commerce allowlist | Prodavan M05 v1 |
|--------------------|-----------------|
| dns | dns |
| ozon | ozon |
| yandex-market | — (v2) |
| mvideo | — |
| online-trade | — |
| ixbt | — |
| — | aliexpress |
| — | citilink |
| — | regard |
| — | nix |

Migration: map known shops; unmapped → disabled pending adapter.

---

## 10. Prompts & profiles

| Commerce path | Prodavan path |
|---------------|---------------|
| `/AGENTS.md` (repo root) | `prompts/AGENTS.md` per cabinet |
| `profiles/kp/README.md` | `prompts/profiles/kp/README.md` |
| `profiles/kp/*.md` | `prompts/profiles/kp/*.md` |
| `projects/<name>/AGENTS.md` snapshot | Copied on project create (optional) |

Content semantics unchanged; paths scoped to cabinet.

---

## 11. Agent runtime

| Commerce | Prodavan |
|----------|----------|
| `IAgentRuntime` | `AgentProviderPort` |
| `CursorSdkRuntime` | `CursorSdkAdapter` |
| `wrapUserPrompt(raw, locale)` | `wrap_user_prompt(raw, locale)` |
| `CreateAgentOptions.cwd` | `workspace_path` |
| `CreateAgentOptions.mcpServers` | `mcp_servers` + gateway JWT |
| `sandboxOptions.enabled: false` (prod) | `true` enforced |
| `proj:<userId>:<project>` pool key | `tenant_id:cabinet_id:project_id:session_id` |
| Telegram HTML stream | SSE `StreamEvent` types |

---

## 12. KP export

| Commerce | Prodavan |
|----------|----------|
| Bot `/кп` → `templates/kp-template.xlsx` | API `POST /cabinets/{cid}/specs/kp/export` |
| Reads `commerce.sqlite` variants | Reads `specs.variants` |
| `is_best=1` filter | Same |
| `MIN_EXPORT_RELEVANCE=6.0` | Same threshold config |
| Agent writes xlsx | **Forbidden** — API/UI only |

---

## 13. Import script contract (P1)

```bash
python tools/migrate_commerce_project.py \
  --commerce-root /path/to/Commerce \
  --project demo \
  --tenant-id $TID \
  --cabinet-id $CID \
  --prodavan-api https://api.staging.prodavan.local \
  --token $JWT
```

Steps:
1. Upload inbox + run artifacts → object store
2. INSERT spec_run, line_items, offers, variants
3. INSERT specs + spec_links from sqlite
4. Verify counts + checksums

Validation report JSON:

```json
{
  "lineitems": { "commerce": 42, "prodavan": 42 },
  "variants": { "commerce": 180, "prodavan": 175, "dropped_on_order": 5 },
  "primary_match": true
}
```

---

## 14. Semantic invariants (unchanged)

| Rule | Commerce | Prodavan |
|------|----------|----------|
| P/N preservation | ✓ | ✓ |
| No on_order S4B | ✓ | ✓ |
| Trusted seller primary priority | ✓ | ✓ |
| No agent KP write | ✓ | ✓ |
| Final gate operator OK | ✓ | ✓ |
| Offers from artifacts only | ✓ | ✓ + MCP audit |

---

## Связанные документы

- [commerce-boundary.md](commerce-boundary.md)
- [../03-modules/M05-integrations/mcp-tools.md](../03-modules/M05-integrations/mcp-tools.md)
- [../05-backend/erd-v0.md](../05-backend/erd-v0.md)
- [../06-agent-runtime/provider-port.md](../06-agent-runtime/provider-port.md)
