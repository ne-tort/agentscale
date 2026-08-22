# Cabinets — architecture (dynamic)

Слои после смены концепции. Детали идеи: [dynamic-cabinets.md](dynamic-cabinets.md).

## Слои

```text
┌──────────────── Platform ─────────────────────────────────────────┐
│ Identity · Company · Employee · AI keys · Quotas · Agent runtime  │
│ Cabinet Runtime API (contracts) · Bundle import/export            │
│ Dynamic Cabinet Shell (Flutter) — ONE UI engine                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │ MCP / HTTP contracts only
┌───────────────────────────────▼───────────────────────────────────┐
│ CabinetInstance (per employee ownership — default)                  │
│  meta catalog (tables/columns/tabs/views/mcp_tools)                 │
│  data tables / json documents                                       │
│  projects → containers (materialize from cabinet + registry MCP)    │
└─────────────────────────────────────────────────────────────────────┘
```

## Что в коде платформы (статично)

| Компонент | Роль |
|-----------|------|
| Cabinet Runtime | DDL/DML/meta CRUD по контрактам |
| Dynamic Shell | Tabs + EntityCollection/Form interpreters |
| Base template seeder | Создаёт instance с системными tabs/tools |
| Bundle codec | zip/json import-export |
| Agent bridge | Подключает cabinet MCP в project |

## Чего больше нет в каноне как обязательного

- Отдельный Flutter/Python **доменный pack** на каждый тип кабинета.
- Hardcoded `CabinetUiModule` registry по `equipment-procurement`.

Опционально: **Starter bundles** (файлы) в platform catalog — не код.

## Ownership & isolation

| Actor | Access |
|-------|--------|
| Owner Employee | Full operate |
| Company | Org ownership: metrics, quotas, policy; no peer data by default |
| Platform Admin | Oversee / break-glass + audit |
| Other employees | **No** schema access |

Data plane: **schema per CabinetInstance** — independent copies; edits never leak to peers.

Готовность вынести instance в отдельную БД/сервис — без смены bundle и UI contracts ([packaging](packaging.md)).

## Проекты и агент

1. Project принадлежит CabinetInstance.  
2. Materialize: AGENTS/prompts/skills + **mcp.json из registry** кабинета.  
3. Агент вызывает `cabinet.*` MCP → меняет meta/data → UI refresh.

## Связь с 07 UI

Dynamic views обязаны использовать EntityCollection / core widgets; `ui_json` не вводит новую визуальную систему.
