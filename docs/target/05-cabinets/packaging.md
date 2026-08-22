# Cabinets — packaging & isolation (runtime)

Актуально после [dynamic-cabinets](dynamic-cabinets.md): домен больше не «code pack», но **изоляция runtime** и готовность к выносу — те же принципы.

## Решение

| Вопрос | Ответ |
|--------|--------|
| Кабинет = microservice сейчас? | **Нет** |
| Что изолируем? | **CabinetInstance data plane** (schema) + meta; runtime в монолите |
| Доменный код на кабинет? | **Нет** — dynamic meta + bundles |
| Готовность к отдельному data service? | Контракты `cabinet.*` + bundle format стабильны |

## Границы

```text
Platform code (static)
  Cabinet Runtime · Dynamic Shell · Agent bridge · Base seeder

CabinetInstance (dynamic, per owner)
  schema cab_inst_* · meta · data · mcp registry
```

## Запреты

- Доменные Flutter screens вне dynamic interpreters.  
- Cross-schema SQL между instances.  
- Raw SQL MCP.  
- Import executable payloads (v1).

## Эволюция

| Сейчас | Потом |
|--------|-------|
| Schema in shared Postgres | Dedicated DB / service per instance or pool |
| In-process Runtime | Runtime as microservice; shell talks HTTP |
| Declarative MCP only | Optional sandboxed external MCP |

Bundle format и meta schemas — **не ломать** без major version.
