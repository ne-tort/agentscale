# Meta-table syntax — индекс

**Meta-table syntax** — декларативный язык описания UI кабинета, схемы данных и runtime-операций (MinIO, Pod hydrate, MCP).  
UI строится **исключительно** из meta; backend и агент интерпретируют **тот же** каталог.

Карта: [Module entity](../entity.md) · UI renderers: [meta-and-ui](../../05-cabinets/meta-and-ui.md) · Materialize: [materialize-from-meta](../../05-cabinets/materialize-from-meta.md).

## Аналоги (откуда взяты паттерны)

| Паттерн | Аналог | Что заимствуем |
|---------|--------|----------------|
| Data + UI schema split | [Retool](https://retool.com/docs), Appsmith, Budibase | `tables`/`columns` vs `views`/`tabs` |
| JSON Form Schema | [React JSON Schema Form](https://rjsf-team.github.io/react-jsonschema-form/) | типы полей → preference widgets |
| Headless CMS fields | Directus, Strapi | `ref`, `file_ref`, conditional visibility |
| Capability-safe DDL | Supabase RLS + typed RPC | MCP `cabinet.*`, no raw SQL |
| Workspace artifact sync | dbt project + seed files | `materialize` rules → MinIO → Pod |

Prodavan **не** копирует SQL или React — только **идею**: один декларативный каталог → три потребителя (Flutter, API, materialize job).

## Четыре оси meta

```text
┌─────────────┐   ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
│ 1. Schema   │   │ 2. UI       │   │ 3. Runtime   │   │ 4. Scope    │
│ tables      │   │ views       │   │ actions      │   │ bindings    │
│ columns     │   │ tabs        │   │ materialize  │   │ enabled     │
└─────────────┘   └─────────────┘   │ mcp_tools    │   │ project_*   │
       │                 │           └──────────────┘   └─────────────┘
       └──────── data rows (per cabinet schema) ────────┘
```

| Ось | Slug(s) | Кто пишет | Кто читает |
|-----|---------|-----------|------------|
| Schema | `tables`, `columns` | Admin / agent (MCP) | Validator, MCP rows.*, UI field types |
| UI | `views`, `tabs` | Admin / agent | Flutter interpreters |
| Runtime | `actions`, `materialize`, `mcp_tools` | Admin / agent | Materialize job, MCP gateway |
| Scope | поля `enabled`, `scope` на любой сущности | Admin | UI filter, runtime filter |

## Хранение (Module)

Shared template в platform DB — `module_meta_documents`:

| Slug | Содержимое |
|------|------------|
| `tables` | `TableDefinition[]` |
| `columns` | `ColumnDefinition[]` |
| `views` | `ViewDefinition[]` |
| `tabs` | `TabDefinition[]` |
| `actions` | `ActionDefinition[]` (optional) |
| `materialize` | `MaterializeRule[]` (optional) |
| `mcp_tools` | `McpToolDefinition[]` (optional) |

Данные строк — **не** в meta: `cab_inst_*.module_data_rows` (per cabinet).

## Документы (читать по порядку)

| # | Документ | Содержание |
|---|----------|------------|
| 1 | [overview](01-overview.md) | Поток данных, версии, инварианты |
| 2 | [tables-and-columns](02-tables-and-columns.md) | Schema, типы колонок, storage_kind |
| 3 | [views-ui](03-views-ui.md) | `ui_json`, collection/form/hub |
| 4 | [tabs-navigation](04-tabs-navigation.md) | Shell, system tabs, nav labels |
| 5 | [actions-runtime](05-actions-runtime.md) | Декларативные операции |
| 6 | [materialize-workspace](06-materialize-workspace.md) | MinIO, file_ref, Pod hydrate |
| 7 | [scope-bindings](07-scope-bindings.md) | enabled, project scope, module bind |
| 8 | [mcp-tools](08-mcp-tools.md) | Declarative tools + packages |
| 9 | [validation-rules](09-validation-rules.md) | JSON Schema правила, allowlists |
| 10 | [ai-authoring-guide](10-ai-authoring-guide.md) | Инструкции для ИИ-автора |

## Примеры

| Сценарий | Файл |
|----------|------|
| Каталог поставщиков (UI + data) | [examples/suppliers-module.md](examples/suppliers-module.md) |
| Agent context (prompts/rules → Pod) | [examples/agent-context-module.md](examples/agent-context-module.md) |
| Простая data-table для MCP | [examples/data-only-module.md](examples/data-only-module.md) |
| Project-scoped вкладка | [examples/project-scoped-tab.md](examples/project-scoped-tab.md) |

## Версионирование

| Поле | Значение |
|------|----------|
| `syntax_version` | `1` — текущий канон |
| `ui_json.version` | `1` — внутри ViewDefinition |

Breaking changes → новый `syntax_version`; интерпретаторы поддерживают N и N-1.

## Инварианты (кратко)

1. **No domain screens** — только interpreters + meta.
2. **No raw SQL** от модели — `cabinet.*` MCP или declarative actions.
3. **Template shared, data isolated** — один Module, разные `module_data_rows` per cabinet.
4. **Laconic UI meta** — labels 1–3 слова; без instructional paragraphs.
5. **Invalid meta** → `EmptyPlaceholder` + audit; не silent fallback на hardcoded UI.
