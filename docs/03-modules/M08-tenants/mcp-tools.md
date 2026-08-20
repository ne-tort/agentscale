# M08 — MCP tools (admin)

Server `prodavan-tenants` — **platform.admin** and **tenant.admin** only. Not in profile `kp`.

## Tools

### `tenants.get_context`

Current JWT context (tid, cid, roles, perms).

### `tenants.list_cabinets`

**Args:** none (uses JWT tid)

### `tenants.list_members`

**Args:** `cabinet_id?`

### `tenants.invite_user` (tenant.admin)

**Args:** `email`, `cabinet_role`, `cabinet_id`

Returns invite link (one-time display).

## Not exposed via MCP

- Tenant delete
- Password reset
- Platform suspend

These — HTTP + UI only with MFA.

## Agent usage

Operators **не** вызывают tenant tools в обычном kp-flow.  
Cabinet context устанавливается до старта сессии (login UI).

## Commerce migration

| Commerce | Prodavan |
| --- | --- |
| global repo root | `tenants/{tid}/cabinets/{cid}/` |
| `/проект` bot command | JWT switch-cabinet |
| single operator | multi-user RBAC |
| shared `catalogs/db` | per-cabinet catalogs |
