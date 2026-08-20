# M04 — API: каталоги

## User catalogs

Base: `/v1/cabinets/{cid}/catalogs`

### GET /v1/cabinets/{cid}/catalogs

```json
{
  "items": [
    {
      "id": "cat_abc123",
      "slug": "distrib-main",
      "display_name": "Дистрибьютор Main",
      "format": "sqlite",
      "status": "ready",
      "trusted_seller": true,
      "stats": { "rows": 125000 }
    }
  ]
}
```

---

### POST /v1/cabinets/{cid}/catalogs/upload

Multipart: `file`, `slug`, `display_name`, `trusted_seller`

**Response 202:**

```json
{
  "catalog_id": "cat_abc123",
  "status": "indexing",
  "job_id": "job_index_xyz"
}
```

---

### GET /v1/cabinets/{cid}/catalogs/{catalog_id}

---

### DELETE /v1/cabinets/{cid}/catalogs/{catalog_id}

Archive user catalog (soft). **Not** for system DB.

---

### POST /v1/cabinets/{cid}/catalogs/{catalog_id}/reindex

---

## System databases

### GET /v1/cabinets/{cid}/system-databases

**Response 200 (electronics-procurement):**

```json
{
  "items": [
    {
      "id": "s4b-cache",
      "display_name": "S4B API Cache",
      "type": "s4b_api_cache",
      "deletable": false,
      "virtual": true,
      "requires_profile": "electronics-procurement",
      "stats": {
        "cached_part_numbers": 45000,
        "last_refresh_at": "2026-08-20T06:00:00Z"
      }
    }
  ]
}
```

**Response 200 (generic cabinet):**

```json
{ "items": [] }
```

---

### DELETE /v1/system-databases/{id}

**Always 403** `SYSTEM_DATABASE_NON_DELETABLE` for s4b-cache.

---

## S4B credentials (tenant-level)

Base: `/v1/tenant/s4b-credentials`

Electronics cabinet required to **display** UI link; API is tenant-scoped.

### GET /v1/tenant/s4b-credentials/status

```json
{
  "state": "credentials_valid",
  "last_validated_at": "2026-08-20T05:00:00Z",
  "s4b_username_hint": "buyer***@acme.ru",
  "rate_limit_reset_at": null
}
```

States: `missing_credentials` | `credentials_valid` | `credentials_invalid` | `rate_limited`

---

### PUT /v1/tenant/s4b-credentials

**Request:**

```json
{
  "username": "buyer@acme.ru",
  "password": "secret"
}
```

**Response 200:**

```json
{
  "state": "credentials_valid",
  "validated_at": "2026-08-20T08:00:00Z"
}
```

Validation: test call to S4B API. On failure → `credentials_invalid`.

Requires scope `catalogs:credentials:write`, role admin.

---

### DELETE /v1/tenant/s4b-credentials

Clears vault → `missing_credentials`.

---

### POST /v1/tenant/s4b-credentials/validate

Manual re-check without password change.

---

## Query API (internal / MCP bridge)

### POST /v1/cabinets/{cid}/catalogs/query

```json
{
  "database": "distrib-main",
  "sql": "SELECT pn, price, title FROM products WHERE pn = ? LIMIT 20",
  "params": ["910-001793"]
}
```

System DB `s4b-cache` — same endpoint if capability s4b.

Errors:

| Code | HTTP |
| --- | --- |
| `SYSTEM_DATABASE_NON_DELETABLE` | 403 |
| `S4B_CREDENTIALS_MISSING` | 503 |
| `S4B_RATE_LIMITED` | 429 + Retry-After |
| `CATALOG_NOT_READY` | 409 |
| `CAPABILITY_S4B_FORBIDDEN` | 403 |
