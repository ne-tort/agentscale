# M02 — MCP tools: спеки и КП

MCP servers (capability-filtered):

| Server | electronics | generic |
| --- | --- | --- |
| commerce-search | ✓ | ✗ |
| commerce-s4b | ✓ | **✗** |
| commerce-offers | ✓ | ✗ |
| commerce-equipment | ✓ | ✗ |

## commerce-search

**search_by_part_number**

```json
{
  "workspace_key": "cab:...:proj_...",
  "part_number": "910-001793",
  "databases": ["distrib-main", "s4b-cache"]
}
```

Note: `s4b-cache` virtual DB only if s4b capability.

**query_database**

```json
{
  "database": "distrib-main",
  "sql": "SELECT * FROM products WHERE pn = ? LIMIT 10",
  "params": ["910-001793"]
}
```

---

## commerce-s4b (electronics ONLY)

**s4b_search**

```json
{
  "part_number": "910-001793",
  "in_stock_only": true
}
```

Never returns on_order items. Tool **not registered** outside electronics-procurement.

**s4b_trusted_sellers** — list trusted for rank.

Credential state from M04 affects availability (rate_limited → error with retry_after).

---

## commerce-offers

**offers_upsert_batch** — variants phase

```json
{
  "workspace_key": "cab:...",
  "run_id": "01JABC...",
  "offers": [ { "line_id": "line_001", "role": "primary", "...": "..." } ]
}
```

**offers_list_variants**

```json
{ "line_id": "line_001" }
```

---

## commerce-equipment (electronics ONLY)

**equipment_upsert**

```json
{
  "part_number": "CMK32GX5M2B5600C36",
  "category": "ram",
  "specs": { "ddr": "DDR5", "capacity_gb": 32 }
}
```

**equipment_find_analogs**

```json
{ "part_number": "...", "constraints": { "ddr": "DDR5" } }
```

---

## Pipeline orchestration (agent)

Recommended tool sequence (not enforced by MCP, enforced by phase guards):

```text
1. create_run / advance ingest
2. advance classify
3. search (catalog → s4b* → web)
4. advance rank
5. offers_upsert_batch
6. human review
7. API finalize + export/kp
```

## Negative test IDs

| Test ID | Tool call |
| --- | --- |
| NEG-SKP-MCP-001 | commerce-s4b on generic |
| NEG-SKP-MCP-002 | upsert price not in offers.json |
| NEG-SKP-MCP-003 | equipment on generic |
