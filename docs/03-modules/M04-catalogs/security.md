# M04 — Security: каталоги

## Credential vault

| Requirement | Implementation |
| --- | --- |
| Encryption at rest | AES-256-GCM, KMS-managed keys |
| Access | Dedicated s4b-worker service account only |
| Logging | Never log username/password; username_hint masked |
| Rotation | Support key_version field |

## SQL injection

- MCP query_database: prepared statements only
- Allowlist SQL verbs: SELECT
- Max rows 1000 per query

## User catalog uploads

- Scan for zip bombs / oversized
- SQLite attach sandbox — no ATTACH outside catalog file
- xlsx macro stripped

## System database protection

- API DELETE → 403 SYSTEM_DATABASE_NON_DELETABLE
- Filesystem ACL: s4b-cache paths not writable by operator UID
- DB trigger on system_databases table

## S4B scope enforcement

Defense in depth (all layers must agree):

1. M00 capabilities
2. M04 list_databases filter
3. MCP tool registration
4. commerce-s4b worker checks profile + cred state
5. UI hide on generic

## Rate limiting

- Respect S4B 429 → state rate_limited
- Exponential backoff worker
- Per-tenant quota config

## Audit

- credentials.updated (no secret)
- credentials.validation_failed
- catalog.uploaded, catalog.archived
- s4b_cache_refresh

## Negative test IDs

| Test ID | Focus |
| --- | --- |
| NEG-CAT-SEC-001 | Vault secret in API response |
| NEG-CAT-SEC-002 | DELETE s4b-cache filesystem |
| NEG-CAT-SEC-003 | SQL injection in catalog query |
| NEG-CAT-SEC-004 | S4B call from generic worker |
