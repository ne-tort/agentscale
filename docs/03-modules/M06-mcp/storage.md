# M06 — Storage

## Server bundles

Platform ships MCP packages as versioned artifacts:

```text
/opt/prodavan/mcp/
  prodavan-catalog/1.0.0/
    pyproject.toml
    prodavan_mcp/catalog/
  prodavan-s4b/1.2.0/
  ...
```

Installations reference `server_id@version` from `server_definitions.version`.

## Per-cabinet config

```text
tenants/{tenant_id}/cabinets/{cabinet_id}/mcp/
  installations.json          # optional export/backup
  custom-servers/             # community/custom only
    {installation_id}/
      manifest.json
      server.py
```

**Custom servers:** только `trust_level=custom`, требуют `tenant.admin` approval.

## Runtime state

```text
/var/run/prodavan/mcp/
  {installation_id}.pid
  {installation_id}.stderr.log   # rotated, no secrets
```

Logs retention: 7 days. Redaction middleware для env vars.

## Config templates

`storage/mcp/config-template.yaml`:

```yaml
transport: stdio
spawn:
  command: python
  args: ["-m", "prodavan_mcp.s4b"]
  cwd: /opt/prodavan/mcp/prodavan-s4b/1.2.0
env_from:
  - CABINET_ID
  - S4B_LOGIN
  - S4B_PASSWORD
health_check:
  tool: s4b.ping
  interval_seconds: 60
```

## SSE transport (remote MCP)

Optional HTTP endpoint per installation:

```text
https://mcp.{tenant_subdomain}.prodavan.ru/s4b/{installation_id}/sse
```

TLS mandatory. Auth: signed JWT scoped to session.

## Quotas

| resource | default |
| --- | --- |
| custom server bundle size | 10 MB |
| concurrent MCP processes per cabinet | 7 (one per official server) |
| stderr log per installation | 50 MB |

## Backup

- `mcp.installations`, `mcp.tool_catalog` — PostgreSQL backup
- Process state — ephemeral
- Custom bundles — included in tenant backup
