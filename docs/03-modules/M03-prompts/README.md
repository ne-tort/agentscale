# M03 — Промпты (Prompts)

Модуль **M03-prompts** управляет инструкциями агента на уровне кабинета: master `AGENTS.md`, профили задач (`profiles/`), модульные MD-файлы, MD-редактор (icon-only toolbar), import/export (zip и отдельные файлы), версионирование.

## Назначение

| Задача | Описание |
| --- | --- |
| Per-cabinet prompts | Изолированное дерево `prompts/` в storage кабинета |
| Master + profiles + modules | Иерархия: AGENTS.md → profiles/{id}/ → modules |
| MD editor | Icon-only UI без текстовых кнопок на toolbar |
| Import/export | Zip bundle или selective files |
| Versioning | Snapshot history, diff, rollback |

## Структура (кратко)

```text
storage/cabinets/{tid}/{cid}/prompts/
├── AGENTS.md                 # master
├── profiles/
│   ├── kp/                   # electronics procurement profile
│   │   ├── README.md
│   │   └── 01-ingest.md …
│   └── …
├── skills/                   # optional skill snapshots
└── .prompts-version.json     # current version pointer
```

## Seed

При создании кабинета M00 pack seed вызывает M03 для материализации из `packages/cabinet-packs/{profile_id}/prompts/`.

## Зависимости

```text
M00-cabinets (storage root)
M03-prompts
  → consumed by agent runtime on cabinet switch
  → M02 reads profiles/kp/ for pipeline hints
```

## Документация

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | PromptBundle, Version |
| [api.md](api.md) | CRUD files, import/export |
| [persistence.md](persistence.md) | version index |
| [storage.md](storage.md) | tree layout |
| [mcp-tools.md](mcp-tools.md) | read_prompt, list_prompts |
| [ui.md](ui.md) | MD editor icon-only |
| [security.md](security.md) | injection, path safety |
| [checklist-implementation.md](checklist-implementation.md) | |
| [checklist-review.md](checklist-review.md) | |

## Статус

0.1.0-draft
