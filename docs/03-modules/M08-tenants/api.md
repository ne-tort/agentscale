# M08 — HTTP API

## Auth

### POST `/api/v1/auth/login`

**Body:** `{ "email", "password" }`

**Response:**

```json
{
  "access_token": "eyJ...",
  "refresh_token": "opaque",
  "expires_in": 3600,
  "user": { "id", "email", "display_name" },
  "tenants": [{ "id", "slug", "display_name" }]
}
```

### POST `/api/v1/auth/refresh`

### POST `/api/v1/auth/logout`

### POST `/api/v1/auth/switch-cabinet`

**Body:** `{ "cabinet_id": "uuid" }`

New access_token with updated `cid`, `cr`, `perms`.

## Tenants

### POST `/api/v1/tenants` (platform.admin or self-signup)

**Body:** `{ "slug", "display_name" }`

Creates tenant + default cabinet `main` + caller as `tenant.owner`.

### GET `/api/v1/tenants/{tenant_id}`

Requires membership.

### PATCH `/api/v1/tenants/{tenant_id}`

tenant.owner | tenant.admin

### DELETE `/api/v1/tenants/{tenant_id}`

tenant.owner — soft delete.

## Cabinets

### GET `/api/v1/tenants/{tenant_id}/cabinets`

### POST `/api/v1/tenants/{tenant_id}/cabinets`

**Body:** `{ "slug", "display_name", "timezone?" }`

Side effect: create storage prefix + default integration policy.

### GET/PATCH/DELETE `/api/v1/cabinets/{cabinet_id}`

## Users & membership

### GET `/api/v1/tenants/{tenant_id}/members`

### POST `/api/v1/tenants/{tenant_id}/invites`

**Body:**

```json
{
  "email": "user@acme.ru",
  "tenant_role": "tenant.member",
  "cabinet_memberships": [
    { "cabinet_id": "uuid", "cabinet_role": "cabinet.operator" }
  ]
}
```

### POST `/api/v1/invites/{token}/accept`

### PATCH `/api/v1/tenants/{tenant_id}/members/{user_id}`

Change tenant_role.

### DELETE `/api/v1/tenants/{tenant_id}/members/{user_id}`

### GET `/api/v1/cabinets/{cabinet_id}/members`

### PUT `/api/v1/cabinets/{cabinet_id}/members/{user_id}`

**Body:** `{ "cabinet_role": "cabinet.viewer" }`

### DELETE `/api/v1/cabinets/{cabinet_id}/members/{user_id}`

## RBAC introspection

### GET `/api/v1/me`

User + current tenant + cabinet + permissions[].

### GET `/api/v1/me/permissions`

Effective permission list for current context.

## Platform admin

### GET `/api/v1/platform/tenants`

platform.admin only — metadata only, no project paths.

### POST `/api/v1/platform/tenants/{id}/suspend`

## Error codes

| code | HTTP |
| --- | --- |
| `TENANT_SLUG_TAKEN` | 409 |
| `CABINET_ACCESS_DENIED` | 403 |
| `LAST_OWNER` | 409 |
| `INVITE_EXPIRED` | 410 |
| `TENANT_SUSPENDED` | 403 |

## OpenAPI security

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
```

All `/api/v1/cabinets/*` require `cid` in JWT matching path resource.
