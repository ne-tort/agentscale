# M09 — Безопасность

## Audit PII

| field | policy |
| --- | --- |
| email | store actor_id only |
| message content | not in audit |
| S4B creds | never |
| tool args | hash or omit |
| ip_address | store /32, tenant admin visible |

Compliance mode `audit_full_ip=false` → store /24 only.

## Access control

| resource | permission |
| --- | --- |
| tenant audit | `audit.read` + tenant membership |
| cabinet audit | scoped to cabinet_id |
| platform metrics | network + platform role |
| retention admin | platform.admin |

## Tamper resistance

- Application DB user: INSERT on audit_events, no UPDATE/DELETE
- Retention job: separate DB role with DELETE on partitions only
- Archive manifests signed (HMAC platform key)

## Export security

- Signed URLs 24h
- Export watermarked with `requested_by`, `tenant_id`
- Audit `operations.audit_exported` always

## Metrics endpoint

- Not public internet
- scrape from VPC / Prometheus SA
- Optional: bearer token rotation

## Token usage privacy

- Usage visible to tenant.admin + cabinet.admin (own cabinet)
- cabinet.viewer — aggregate only, no run drill-down

## Agent actor

When `actor_type=agent`:

- `actor_id` = session_id or service account
- Distinguish human vs automated actions in UI

## Rate limits

- Audit query: 60 req/min per user
- Export: 2 concurrent per tenant
- Prevent audit enumeration: cursor opaque, max 10k rows/query

## GDPR

- Export includes all audit for tenant
- Erasure: hard delete removes audit + archives for tenant_id

## Fail-safe

If audit emit fails:

- Increment `prodavan_audit_emit_failures_total`
- Buffer retry 3x
- **Do not block** user operation (except compliance strict mode)

Strict mode flag per tenant: block if audit unavailable.
