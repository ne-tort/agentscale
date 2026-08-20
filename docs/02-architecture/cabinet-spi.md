# Cabinet SPI (v1)

Universal contract between **Platform Host** and a **Cabinet Module**.  
OpenAPI: [`../../packages/schemas/cabinet-spi/v1.yaml`](../../packages/schemas/cabinet-spi/v1.yaml).

## Base URL

- Colocated: in-process `CabinetModule` (no network).
- Remote: `https://{cabinet-service}/spi/v1` (DSN on cabinet row).

Every request carries platform context headers:

| Header | Meaning |
|--------|---------|
| `X-Tenant-Id` | Tenant UUID |
| `X-Cabinet-Id` | Cabinet UUID |
| `X-Project-Id` | Project UUID (when scoped) |
| `X-User-Id` | Acting user UUID |
| `Authorization` | Platform JWT (module validates via JWKS or shared secret in MVP) |

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness + pack version |
| `GET` | `/manifest` | Capabilities, UI module refs, tools, command/query schemas |
| `POST` | `/commands/{name}` | Mutating domain ops (`run_phase`, `export_kp`, …) |
| `GET` | `/queries/{name}` | Read models (`list_runs`, `list_offers`, …) |
| `POST` | `/hooks/platform-event` | Async platform events |
| `POST` | `/migrate` | Apply pack DB migrations for this cabinet instance |

## Platform events

Published by core; cabinets subscribe via hook:

| Event | When |
|-------|------|
| `project.created` | Project row + FS workspace ready |
| `project.opened` | User opened project (JWT refreshed) |
| `file.uploaded` | Inbox/catalog upload stored on workspace |
| `agent.session.started` | Agent chat session created |
| `agent.tool_requested` | Agent wants a tool; cabinet may fulfill |
| `cabinet.archived` | Soft-delete; module should pause workers |

Payload envelope:

```json
{
  "event_id": "uuid",
  "type": "file.uploaded",
  "occurred_at": "ISO-8601",
  "tenant_id": "uuid",
  "cabinet_id": "uuid",
  "project_id": "uuid|null",
  "actor_user_id": "uuid|null",
  "data": {}
}
```

## Cabinet database

- Provisioned at cabinet create (after pack seed).
- Connection recorded as `cabinets.capabilities.runtime.db` (or future `db_dsn` column).
- MVP default: SQLite file `{storage}/cabinets/{tenant}/{cabinet}/cabinet.sqlite`.
- Migrations live in pack `migrations/` and run only through SPI `/migrate`.

## Flutter module host

See [../04-frontend/module-host.md](../04-frontend/module-host.md). Shell reads `manifest.ui` and mounts routes; it never hard-codes KP/S4B screens.
