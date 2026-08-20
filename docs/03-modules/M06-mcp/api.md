# M06 — HTTP API

## Platform catalog (read-only)

### GET `/api/v1/mcp/servers`

Глобальный список доступных server definitions.

**Query:** `?category=integrations&trust_level=official`

**Response 200:**

```json
{
  "items": [
    {
      "server_id": "prodavan-s4b",
      "display_name": "S4B Aggregator",
      "version": "1.2.0",
      "category": "integrations",
      "trust_level": "official",
      "tool_count_estimate": 12
    }
  ]
}
```

### GET `/api/v1/mcp/servers/{server_id}`

Детали + список tools из **platform manifest** (не live discovery).

## Cabinet installations

Префикс: `/api/v1/cabinets/{cabinet_id}/mcp`

### GET `/installations`

```json
{
  "items": [
    {
      "id": "uuid",
      "server_id": "prodavan-s4b",
      "state": "enabled",
      "tool_count": 12,
      "last_discovery_at": "2026-08-20T10:00:00Z"
    }
  ]
}
```

### POST `/installations`

**Body:**

```json
{
  "server_id": "prodavan-s4b",
  "config": {}
}
```

**Response 201** — `state: installed`

### POST `/installations/{id}/enable`

Запуск discovery. **Response 200** или **502 MCP_DISCOVERY_FAILED**

### POST `/installations/{id}/disable`

### DELETE `/installations/{id}`

Uninstall (only if disabled).

### GET `/installations/{id}/tools`

Live или cached tool list.

### POST `/installations/{id}/rediscover`

Force refresh catalog.

## Profiles

### GET `/api/v1/mcp/profiles`

Built-in + custom profiles tenant.

### GET `/api/v1/mcp/profiles/{profile_id}`

### POST `/api/v1/tenants/{tenant_id}/mcp/profiles` (tenant.admin)

Custom profile.

**Body:**

```json
{
  "profile_id": "custom-procurement",
  "display_name": "Закупки B2B",
  "allowed_servers": ["prodavan-catalog", "prodavan-s4b"],
  "tool_denylist": ["s4b.cache_purge"],
  "max_tools": 25
}
```

### GET `/api/v1/cabinets/{cabinet_id}/mcp/effective-tools`

Preview для UI: `?profile_id=kp`

```json
{
  "profile_id": "kp",
  "tools": [
    { "name": "catalog.list_databases", "server_id": "prodavan-catalog" }
  ],
  "count": 38
}
```

## Agent runtime (internal)

### POST `/internal/v1/mcp/session-bind`

**Body:**

```json
{
  "session_id": "uuid",
  "cabinet_id": "uuid",
  "profile_id": "kp",
  "model": "claude-sonnet-4"
}
```

**Response:**

```json
{
  "mcp_servers": [
    {
      "name": "prodavan-s4b",
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "prodavan_mcp.s4b"],
      "env": { "CABINET_ID": "..." }
    }
  ],
  "tools_filter": ["s4b.search_articles", "..."],
  "snapshot_hash": "sha256:..."
}
```

### POST `/internal/v1/mcp/tool-call`

Proxy вызова tool с metrics + audit (M09).

## WebSocket (optional admin)

`/ws/v1/cabinets/{id}/mcp/events` — discovery progress, state changes.
