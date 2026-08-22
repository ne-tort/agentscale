# L06 — Cabinet Runtime

## Цель

Динамический CabinetInstance: schema-per-instance, meta catalog, data plane, declarative MCP + **MCP packages** deploy, Base seed, bundle export/import, platform `cabinet.*` tools. UI interpreters (EntityCollection из `ui_json`). **Без** agent chat и без container lifecycle.

## Канон

- [05-cabinets/](../05-cabinets/) — обязательно:
  - [dynamic-cabinets.md](../05-cabinets/dynamic-cabinets.md)
  - [meta-and-ui.md](../05-cabinets/meta-and-ui.md)
  - [mcp-contracts.md](../05-cabinets/mcp-contracts.md)
  - [mcp-packages.md](../05-cabinets/mcp-packages.md)
  - [bundle-format.md](../05-cabinets/bundle-format.md)
  - [module-contract.md](../05-cabinets/module-contract.md)
  - [packaging.md](../05-cabinets/packaging.md)
  - [default-cabinets.md](../05-cabinets/default-cabinets.md)
  - backend/frontend/architecture

## Зависимости

| Нужно | Даёт |
|-------|------|
| L00; L01 (owner employee + company + ACL) | Runtime API + `cabinet.*` MCP surface + materialize hook stub |

L02 нужен для interpreters UI; **API Runtime закрывается изолированно**.

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| Instance API | create(Base)\|import(bundle), rename, archive |
| Schema isolation | `cab_inst_<id>`; **запрет** cross-schema SQL |
| Meta API | tables/columns/tabs/views CRUD + validation allowlist types |
| Data API | query/upsert/delete по meta |
| `cabinet.*` MCP | стабильные tool names/schemas из mcp-contracts.md |
| MCP packages | manifest + deploy/list/disable; sandbox roots |
| Bundle | export/import deep copy → **new** schema |
| Events | `cabinet.meta.changed`, `cabinet.imported`, … |
| `materialize_project` hook | interface: input project_id → workspace files + mcp.json (**реализация FS может быть L07**) |

## Изоляция — делать в полную силу

Закрыть **весь** data/meta/bundle/MCP registry/packages **до** L07/L08:

- HTTP/MCP contract tests;
- import/export round-trip;
- peer ACL;
- Base template seed;
- package zip validation (deny non-allowlist runtime).

UI interpreters — полный DoD по meta-and-ui (не «одна таблица вручную»).

## DoD

- [ ] Create from Base: projects/chat/context/Tables/Tools seed по default-cabinets.
- [ ] Meta → Dynamic shell tabs/collections (L05 host).
- [ ] Data plane соблюдает types; ref integrity по канону.
- [ ] `cabinet.*` tools: list/create table, tabs, rows, bundle export — contract tests.
- [ ] MCP package deploy: strict manifest; enable per project binding.
- [ ] Bundle zip round-trip (meta+packages+optional data).
- [ ] Quotas hook: reject create/import при превышении (L04 quota service).
- [ ] Нет raw SQL tool; нет static Flutter domain module.
- [ ] Packaging invariants: no cross-instance reads.

## Не считать готовым, если…

- «Cabinet = row + profile_id + git pack».
- Работает только seed JSON без meta interpreters.
- `cabinet.*` частично, «остальное потом», при том что уже в mcp-contracts.
- Export без import (или наоборот).
- Packages — «только declarative» вразрез mcp-packages.md без явной phase note в contracts-index.

## Exit gate

Contract suite Runtime зелёный; materialize **interface** стабилен для L07; L05 может открыть Base cabinet.
