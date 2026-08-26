# Materialize meta → Pod workspace (канон)

Как содержимое кабинета попадает в изолированный Pod проекта.

Связано: [entity.md](entity.md) · [14 lifecycle](../14-project-containers/lifecycle.md) · [mcp-contracts](mcp-contracts.md).

## Источник истины

| Что | Где |
|-----|-----|
| Meta + data rows | Cabinet schema (PG) / JSONB tables |
| Blob-файлы | **MinIO** (object key / file id в meta) |
| Workspace проекта | MinIO `projects/{workspace_key}/…` → hydrate → Pod `/workspace` |

## Что копируется при create/resume Pod

Из meta кабинета (по контракту materialize):

| Вид | Примеры в workspace |
|-----|---------------------|
| Agent docs | `AGENTS.md`, `prompts/`, индекс промптов |
| Rules / skills | `rules/`, `skills/` (MD из meta или MinIO refs) |
| MCP packages | код/конфиг пакетов, разрешённых кабинету |
| File refs | файлы, на которые ссылаются meta-строки (`file_ref` → object key) |
| Seed data files | если meta явно пометила «materialize into workspace» |

**Не** копировать в Pod: сырой dump всей platform DB, чужие кабинеты, AI provider secrets в plaintext вне scoped channel.

## Агент и meta после старта

```text
Pod agent
  → cabinet.* MCP (строгое API)
      → read/write data rows
      → manage allowed meta (tables/tabs/tools) если контракт разрешает
  → UI кабинета интерпретирует то же meta и рисует экраны
```

Materialize файлов ≠ доступ агента к meta: файлы в `/workspace` для cwd/агента; **живые** таблицы — только через MCP.

## Инварианты

1. Writer blobs: platform materialize job / Container port — не ad-hoc kubectl.
2. Повторный resume: hydrate из актуального MinIO (+ при необходимости re-materialize из meta).
3. Delete project/cabinet: wipe соответствующих MinIO prefixes + Pod.
