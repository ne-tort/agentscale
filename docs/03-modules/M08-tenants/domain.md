# M08 — Доменная модель

## Сущности

### Tenant

```json
{
  "id": "uuid",
  "slug": "acme-corp",
  "display_name": "ACME Corp",
  "plan": "business",
  "status": "active",
  "created_at": "2026-08-20T10:00:00Z"
}
```

**status:** `active` | `suspended` | `deleted`

### Cabinet

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "slug": "main",
  "display_name": "Основной кабинет",
  "timezone": "Europe/Moscow",
  "created_at": "2026-08-20T10:00:00Z"
}
```

### User

```json
{
  "id": "uuid",
  "email": "user@acme.ru",
  "display_name": "Иван",
  "status": "active",
  "password_hash": "(bcrypt)",
  "mfa_enabled": false,
  "created_at": "2026-08-20T10:00:00Z"
}
```

Users belong to platform; linked to tenants via membership.

### TenantMembership

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "user_id": "uuid",
  "tenant_role": "tenant.admin",
  "created_at": "2026-08-20T10:00:00Z"
}
```

**tenant_role:** `tenant.owner` | `tenant.admin` | `tenant.member`

### CabinetMembership

```json
{
  "id": "uuid",
  "cabinet_id": "uuid",
  "user_id": "uuid",
  "cabinet_role": "cabinet.operator",
  "created_at": "2026-08-20T10:00:00Z"
}
```

**cabinet_role:** `cabinet.admin` | `cabinet.operator` | `cabinet.viewer`

## RBAC matrix (effective permissions)

Permissions = union(tenant_role scopes, cabinet_role scopes) scoped to resources.

| permission | tenant.owner | tenant.admin | tenant.member | cabinet.admin | cabinet.operator | cabinet.viewer |
| --- | --- | --- | --- | --- | --- | --- |
| tenant.settings | ✓ | ✓ | — | — | — | — |
| tenant.billing | ✓ | ✓ | — | — | — | — |
| cabinet.create | ✓ | ✓ | — | — | — | — |
| cabinet.settings | ✓ | ✓ | — | ✓ | — | — |
| integrations.manage | ✓ | ✓ | — | ✓ | — | — |
| mcp.manage | ✓ | ✓ | — | ✓ | — | — |
| project.create | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| agent.chat | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| agent.view_all | ✓ | ✓ | — | ✓ | — | ✓ |
| users.invite | ✓ | ✓ | — | ✓ | — | — |
| audit.read | ✓ | ✓ | — | ✓ | — | ✓ |

**tenant.member** без cabinet membership → no cabinet access.

## Platform roles (separate)

| role | scope |
| --- | --- |
| `platform.admin` | server_definitions, tenant suspend |
| `platform.support` | read-only metadata, no data |

Platform roles **not** in tenant JWT by default.

## Isolation invariants

1. Every business row has `tenant_id` and/or `cabinet_id`.
2. PostgreSQL RLS enforces `app.tenant_id` / `app.cabinet_id`.
3. Storage paths prefixed `tenants/{tenant_id}/`.
4. No `SELECT` without tenant context — connection pool sets GUC.
5. Cross-tenant query returns empty, not error (prevent enumeration).

## Invite flow

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "email": "new@acme.ru",
  "cabinet_id": "uuid",
  "cabinet_role": "cabinet.operator",
  "token_hash": "...",
  "expires_at": "2026-08-27T10:00:00Z"
}
```

## JWT claims

```json
{
  "iss": "prodavan",
  "sub": "user_uuid",
  "tid": "tenant_uuid",
  "cid": "cabinet_uuid",
  "tr": "tenant.admin",
  "cr": "cabinet.admin",
  "perms": ["integrations.manage", "agent.chat"],
  "iat": 1692520800,
  "exp": 1692524400
}
```

Refresh token: opaque, rotated, stored hashed.

## Deleted tenant

Soft delete → 30 day grace → hard delete job wipes storage + DB cascade.
