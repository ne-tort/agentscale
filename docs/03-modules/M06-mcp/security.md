# M06 — Безопасность

## Supply chain

| control | описание |
| --- | --- |
| Official servers only by default | community disabled |
| Signed bundles | SHA256 manifest in server_definitions |
| Custom upload scan | ClamAV + static analysis |
| Pin version | No `latest` in production |

## Process isolation

- MCP stdio processes run as **dedicated user** `prodavan-mcp`
- cwd restricted to server bundle path
- No `--force` shell from MCP servers
- ulimit: 512MB RAM per process (configurable)

## Env secrets

- Decrypted in **MCP supervisor** only, passed via pipe env
- Never in `session_bindings` table
- Rotate on disable → enable cycle optional

## Tool filter as security boundary

Даже если MCP server exposes dangerous tool, profile **denylist** removes it before `Agent.create`.

Critical denylists:

```yaml
# kp profile — never expose to operator
- s4b.cache_purge
- offers.delete
- pipeline.validate_run  # unless admin-debug
```

## Community MCP approval

Workflow:

1. tenant.admin uploads bundle
2. Platform scan + manual review queue
3. `trust_level=community`, `approved_by` set
4. Cabinet can install only after approval

## Network

- stdio: no network from catalog/offers servers
- s4b/integrations: egress allowlist (M05)
- SSE MCP: mTLS + JWT

## Audit events

```json
{ "event_type": "mcp.server_enabled", "server_id": "prodavan-s4b" }
{ "event_type": "mcp.discovery_failed", "error": "timeout" }
{ "event_type": "mcp.custom_uploaded", "installation_id": "uuid" }
```

## Threat: tool injection

Mitigation: discovery schema validation, reject tools with `eval` in description heuristics, max 100 tools per server.

## RBAC

| action | min role |
| --- | --- |
| enable official server | cabinet.admin |
| install community | tenant.admin |
| create custom profile | tenant.admin |
| admin-debug profile | tenant.admin |
