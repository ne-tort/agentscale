# M08 — Tenants (мультитенантность)

> **LEGACY.** Канон компаний/сотрудников: [docs/target/03-companies](../../target/03-companies/), [04-employees](../../target/04-employees/). Auth: [10-identity-keycloak](../../target/10-identity-keycloak/).

Модуль **tenant CRUD**, пользователи, **RBAC**, роли на уровне кабинета, **JWT auth**, **полная изоляция** — нет глобальных боевых данных между tenant.

## Принципы

1. **Tenant** — верхняя граница биллинга и изоляции данных.
2. **Cabinet** — рабочее пространство закупок (бывший «проект организации»).
3. **Project** (M07) — спека/прогон внутри cabinet.
4. **Нет shared catalogs/offers/runs** между tenant.
5. Platform admin не видит содержимое без break-glass audit.

## Иерархия

```text
Tenant
  └── Cabinet (1..N)
        └── Project (M07)
        └── Users + Roles
        └── Integrations (M05)
        └── MCP installations (M06)
```

## Документация

| файл | тема |
| --- | --- |
| [domain.md](domain.md) | Tenant, User, Role, Membership |
| [api.md](api.md) | CRUD + auth |
| [persistence.md](persistence.md) | schemas tenants.* |
| [storage.md](storage.md) | tenant filesystem root |
| [mcp-tools.md](mcp-tools.md) | admin meta-tools |
| [ui.md](ui.md) | admin console |
| [security.md](security.md) | JWT, RLS, isolation |
| checklists | |

## JWT overview

```json
{
  "sub": "user_uuid",
  "tid": "tenant_uuid",
  "cid": "cabinet_uuid",
  "roles": ["cabinet.admin"],
  "exp": 1692524400
}
```

Cabinet context switch → new access token with updated `cid`.

## Зависимости

M08 — **foundation module**. M05–M07 depend on tenant/cabinet context.
