# Meta syntax — overview

## Зачем отдельный синтаксис

Кабинет = **data product**, не Dart/Python pack. Один и тот же meta-документ должен:

| Потребитель | Что извлекает |
|-------------|---------------|
| **Flutter** | tabs, views, columns → `AppEntityCollection`, preferences |
| **HTTP API** | validate row body по ColumnDefinition |
| **Materialize job** | что скопировать в MinIO → Pod `/workspace` |
| **MCP agent** | `cabinet.rows.*`, declarative tools, file refs |
| **ИИ-автор** | правила + примеры → новый module без кода платформы |

## Поток данных

```text
Admin/agent writes module_meta_documents (platform DB)
        │
        ├── bind module → cabinet
        │       └── module_installations + module_data_rows
        │               (empty, or prefilled from optional seed_rows meta)
        │
        ├── employee UI reads template + queries data rows
        │
        └── project create/resume
                └── materialize rules → MinIO projects/{key}/…
                        └── Pod hydrate → /workspace
```

## Module vs Cabinet vs Project

| Слой | Хранит | Пример |
|------|--------|--------|
| **Module meta** | Шаблон (shared) | «Таблица suppliers, колонки name/status» |
| **Cabinet data** | Строки (isolated) | Cab A: Alpha Ltd; Cab B: Beta Ltd |
| **Project binding** | Видимость module в project | Module X только в Project P1 |
| **Workspace** | Snapshot для агента | `AGENTS.md`, seed JSON, file copies |

## Document slugs (канон)

Все документы — JSON **array** или **object** в `body` JSONB (см. [validation-rules](09-validation-rules.md)).

### Обязательные (минимальный модуль)

| Slug | Min content |
|------|-------------|
| `tables` | ≥1 TableDefinition |
| `columns` | ≥1 column per table |
| `views` | ≥1 view per exposed table |
| `tabs` | ≥1 tab pointing to view |

### Опциональные (runtime)

| Slug | Когда нужен |
|------|-------------|
| `actions` | Кнопки/триггеры: copy file, export, invoke |
| `materialize` | Agent docs, seeds, file_ref → Pod |
| `mcp_tools` | Declarative wrappers без custom package |
| `seed_rows` | Предзаполнить `module_data_rows` при MC bind (идемпотентно) |

### Дефолты данных (без отдельного store)

| Цель | Как |
|------|-----|
| Default поля при create_row | В `columns`: `"default": …` — см. [tables-and-columns](02-tables-and-columns.md) |
| Стартовый набор строк в кабинете | Meta slug `seed_rows` → копируется в `module_data_rows` при bind |
| JSON в workspace агента | `materialize` rules / actions на `project.created` (не строки БД) |

**Не** класть runtime rows в `tables`/`columns` — шаблон остаётся shared; данные per cabinet.

## System vs dynamic tabs

**System tabs** (фиксированы платформой, не в module meta):

| `view_slug` | Назначение |
|-------------|------------|
| `projects` | ProjectList |
| `chat` | redirect to project chat |
| `context` | prompts/skills/rules/MCP seeds |
| `tables` | meta admin browser (DDL) |
| `tools` | MCP tools registry |

**Dynamic tabs** — из `tabs` slug module: title, order, `view_id`, optional `table_slug`, optional `nav.contour` для Admin/Company shell — см. [04-tabs-navigation](04-tabs-navigation.md#shell-navigation-admin--company).

Module **не заменяет** system tabs — **добавляет** вкладки в shell.

## Authoring manifest (удобство для ИИ)

При создании meta ИИ может собрать **один** JSON, затем split по slugs:

```json
{
  "syntax_version": 1,
  "module_slug": "suppliers",
  "tables": [ "…" ],
  "columns": [ "…" ],
  "views": [ "…" ],
  "tabs": [ "…" ],
  "materialize": [ "…" ]
}
```

Platform API / MCP `cabinet.meta.put_bundle` (future) принимает manifest и раскладывает по slugs.

## Сценарии (checklist)

| # | Сценарий | Нужные slugs |
|---|----------|--------------|
| S1 | Таблица данных + list UI | tables, columns, views, tabs |
| S2 | Form edit row | view `kind=form` + `row_tap.open_form` |
| S3 | Disabled tab | `tabs[].enabled=false` или `visibility=hidden` |
| S4 | Project-only module tab | `tabs[].scope.projects=bound` |
| S5 | File in row → Pod | column `file_ref` + materialize rule |
| S6 | AGENTS.md from meta row | materialize `format=template` |
| S7 | Agent CRUD via MCP only | tables + columns, view optional |
| S8 | Custom tool without code | `mcp_tools` declarative wrapper |
| S9 | Same module, 2 cabinets | shared meta, different data rows |

## Связь с UI core

| Meta | Flutter |
|------|---------|
| `view.kind=collection` | `AppEntityCollection` |
| `view.kind=form` | `AppValuePreference`, `AppChoicePreference`, `AppSwitchPreference` |
| `view.kind=hub` | `AppNavPreference` list |
| `column.type=bool` | `AppSwitchPreference` |
| `column.type=enum/ref` | `AppChoicePreference` |
| Tab bar | Cabinet shell (bottom on narrow, top/segmented on wide — TBD) |

Дальше: [tables-and-columns](02-tables-and-columns.md)
