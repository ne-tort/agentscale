# Cabinets — backend (dynamic)

## Cabinet Runtime

Платформенный сервис (модуль API), реализующий [mcp-contracts](mcp-contracts.md) и HTTP aliases.

| Concern | Implementation |
|---------|----------------|
| Create instance | Seed Base meta + schema `cab_inst_<id>` |
| DDL | From typed create/alter only |
| DML | rows.* |
| Bundle | import/export codec |
| Authz | Employee CRUD own; Company org metrics/policy; Admin oversee; **no peer schema access** |
| Packages | Store zip artifacts; sandbox start on materialize |

## Persistence default

- One Postgres cluster.  
- **Schema per CabinetInstance.**  
- Meta tables identical in every schema.  
- Quotas in platform schema.

## Не делать

- Отдельный Python package на каждый доменный кабинет.  
- Shared mutable tables across instances.
