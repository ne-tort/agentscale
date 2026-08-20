# M01 — Проекты (Projects)

Модуль **M01-projects** управляет проектами внутри кабинета: CRUD, открытие/архивация, workspace key `cab:{tid}:{cid}:{pid}` и файловое хранилище `inbox/`, `runs/`, `export/`.

## Назначение

| Задача | Описание |
| --- | --- |
| CRUD проектов | Создание, переименование, метаданные, архивация |
| Open project | Установка активного проекта в сессии (аналог switch для pid) |
| Workspace key | Единый идентификатор контекста агента |
| Storage layout | Изолированные inbox/runs/export на проект |
| Archive | Мягкое закрытие; runs сохраняются |

## Границы

**Входит:** Project entity, API, SQLite `commerce.sqlite` per project, storage paths.

**Не входит:** pipeline спеки (M02), промпты кабинета (M03), глобальные catalogs (M04).

## Workspace key

```text
cab:{tenant_id}:{cabinet_id}:{project_id}
```

Пример: `cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890:proj_7f3a9c2e`

Все MCP-вызовы M02/M04 в контексте проекта **обязаны** передавать полный workspace key или derive из session `(tid, cid, pid)`.

## Storage layout (кратко)

```text
storage/cabinets/{tid}/{cid}/projects/{pid}/
├── inbox/              входящие спеки
├── runs/{run_id}/      артефакты прогонов (M02)
├── export/             выгрузки КП
├── commerce.sqlite     варианты офферов
└── project.json        метаданные
```

## Зависимости

```text
M00-cabinets (active cid, capabilities)
  → M01-projects
    → M02-specs-kp (runs, inbox)
    → M04-catalogs (search scope — cabinet-level, run — project-level)
```

## Документация

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | Project, states, invariants |
| [api.md](api.md) | REST |
| [persistence.md](persistence.md) | project.json + optional PG mirror |
| [storage.md](storage.md) | inbox/runs/export |
| [mcp-tools.md](mcp-tools.md) | list/open/create project |
| [ui.md](ui.md) | project selector |
| [security.md](security.md) | isolation по cid/pid |
| [checklist-implementation.md](checklist-implementation.md) | |
| [checklist-review.md](checklist-review.md) | |

## Статус

Версия документа: 0.1.0-draft
