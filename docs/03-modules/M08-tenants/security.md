# M08 — Безопасность

## JWT

| param | value |
| --- | --- |
| algorithm | RS256 |
| access TTL | 1h |
| refresh TTL | 30d, rotated |
| issuer | prodavan |

Keys in KMS. JWKS endpoint: `/.well-known/jwks.json`

## Password

- bcrypt cost 12
- min length 12, breach list check (HIBP API)
- MFA TOTP optional → required for tenant.owner (enterprise)

## Isolation tests (mandatory CI)

1. User A tenant 1 → GET cabinet tenant 2 → 403
2. SQL RLS: SET wrong tenant_id → 0 rows
3. Storage path traversal → 400
4. JWT cid mismatch path → 403

## No global data

| forbidden | alternative |
| --- | --- |
| `SELECT * FROM offers` without tenant | RLS empty |
| Shared catalog sqlite | per-cabinet path |
| Platform cache of prices | per-cabinet s4b-cache |
| Cross-tenant MCP env | CABINET_ID from JWT only |

## Break-glass

platform.support access to tenant data:

- Requires ticket id
- Time-limited elevation token (15 min)
- Full audit in M09 `platform.break_glass_access`

## Invite tokens

- 256-bit random, store SHA256 only
- Single use, 7 day expiry
- Rate limit 10 invites/hour per tenant

## Session fixation

- New refresh token on login
- Revoke all refresh on password change

## RBAC enforcement

Middleware order:

1. Authenticate JWT
2. Load user status (not suspended)
3. Load tenant status
4. Resolve permissions from roles
5. Check permission for route
6. Set PG GUC + storage context

## Compliance

- Data residency: tenant.region flag (future)
- Export: tenant backup package (GDPR)
- Delete: right to erasure via hard delete job
