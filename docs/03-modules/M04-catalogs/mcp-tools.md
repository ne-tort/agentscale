# M04 — MCP tools: каталоги

Servers: `commerce-search`, `commerce-s4b` (electronics only).

## commerce-search

### list_databases

**Output (electronics, valid creds):**

```json
{
  "databases": [
    { "id": "distrib-main", "type": "user", "trusted": true, "rows": 125000 },
    { "id": "s4b-cache", "type": "system", "deletable": false, "virtual": true }
  ]
}
```

**Output (generic cabinet):**

```json
{
  "databases": [
    { "id": "distrib-main", "type": "user", "trusted": true, "rows": 125000 }
  ]
}
```

Note: **no s4b-cache** without electronics profile.

---

### describe_database

```json
{ "database": "s4b-cache" }
```

Error `CAPABILITY_S4B_FORBIDDEN` on non-electronics.

---

### query_database

```json
{
  "database": "distrib-main",
  "sql": "SELECT * FROM products WHERE part_number = ? LIMIT 10",
  "params": ["910-001793"]
}
```

---

### search_by_part_number

Cascade internal: user DBs → s4b-cache* → optional live s4b*

*s4b steps skipped if not electronics or cred state ≠ valid.

---

## commerce-s4b (electronics ONLY)

Registered only when:

```text
cabinet.profile_id == electronics-procurement
AND credential_state == credentials_valid
AND NOT rate_limited
```

### s4b_search

```json
{
  "part_number": "910-001793",
  "in_stock_only": true
}
```

**Errors:**

| Condition | MCP error |
| --- | --- |
| missing_credentials | `S4B_CREDENTIALS_MISSING` |
| credentials_invalid | `S4B_CREDENTIALS_INVALID` |
| rate_limited | `S4B_RATE_LIMITED` + retry_after |
| non-electronics cabinet | tool not registered |

---

### s4b_list_trusted_sellers

For M02 rank trusted_seller flag.

---

## prodavan-catalogs (admin)

### upload_catalog

Operator-only.

### get_s4b_credential_status

Returns state enum without secrets.

---

## Negative test IDs

| Test ID | Call |
| --- | --- |
| NEG-CAT-MCP-001 | list_databases shows s4b on generic |
| NEG-CAT-MCP-002 | s4b_search when rate_limited |
| NEG-CAT-MCP-003 | query_database drop table |
