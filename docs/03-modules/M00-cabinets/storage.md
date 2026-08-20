# M00 — Storage: кабинеты

Файловое хранилище кабинета — изолированный prefix в object storage или локальном томе. Все пути **обязаны** содержать `{tenant_id}/{cabinet_id}`.

## Корневая схема

```text
storage/
└── cabinets/
    └── {tid}/
        └── {cid}/
            ├── .cabinet.json           # метаданные, pack_version, checksum
            ├── prompts/                # M03 seed (см. M03)
            │   ├── AGENTS.md
            │   ├── profiles/
            │   └── skills/
            ├── catalogs/               # M04 user catalogs mount point
            │   └── user/
            ├── projects/               # M01 — проекты кабинета (default empty)
            ├── pack-manifest.json      # список файлов seed
            └── .seed-complete          # маркер успешного pack seed
```

## .cabinet.json

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "tenant_id": "acme-corp",
  "profile_id": "electronics-procurement",
  "pack_version": "1.2.0",
  "pack_checksum": "sha256:abc...",
  "seeded_at": "2026-08-01T10:00:05Z",
  "capabilities_hash": "sha256:def..."
}
```

## Pack seed pipeline (файловые шаги)

| Шаг | Действие | Источник |
| --- | --- | --- |
| 1 | Создать prefix `{tid}/{cid}/` | API |
| 2 | Распаковать `packages/cabinet-packs/{profile_id}/` | pack tarball |
| 3 | Записать `.cabinet.json`, `pack-manifest.json` | deterministic |
| 4 | Создать `prompts/` из pack | M03 hook |
| 5 | Создать `catalogs/user/.gitkeep` | M04 hook |
| 6 | **Не** создавать S4B credentials — только capability flag | M04 |
| 7 | Touch `.seed-complete` | finalize |

### Идемпотентность

Повтор seed с тем же `(cid, pack_version)`:

- файлы с совпадающим checksum — skip;
- новые файлы в pack — merge с audit;
- удалённые из pack — **не** удалять автоматически (migration script).

## URI scheme

```text
prodavan://storage/cabinets/{tid}/{cid}/prompts/AGENTS.md
```

Agent runtime и MCP резолвят URI относительно активного `workspace_key`.

## Квоты

| Ресурс | Default per cabinet |
| --- | --- |
| Общий объём | 10 GiB (tenant plan) |
| Файлов в seed pack | ≤ 500 |
| Max single file seed | 5 MiB |

## Изоляция (cross-cabinet)

Sandbox правила для agent/shell:

1. `cwd` ∈ `storage/cabinets/{tid}/{active_cid}/**`
2. Запрет `..` выхода за prefix
3. Запрет symlink за пределы prefix (проверка `realpath`)
4. Read другого `{cid}` → `CABINET_ISOLATION_VIOLATION`

### Negative test IDs (storage)

| Test ID | Действие | Ожидание |
| --- | --- | --- |
| NEG-CAB-ST-001 | Read `../{other_cid}/.cabinet.json` | deny |
| NEG-CAB-ST-002 | Symlink `projects -> /etc` | reject at seed |
| NEG-CAB-ST-003 | Write в чужой tid prefix | deny |
| NEG-CAB-ST-004 | Delete `.seed-complete` без admin | deny |

## Репликация и DR

- Prefix `{tid}/{cid}/` — unit of restore.
- При archive кабинета storage **не** удаляется 90 дней (retention policy).
- Hard delete — только admin + legal hold check.

## Связь с M01

Проекты живут в `storage/cabinets/{tid}/{cid}/projects/{pid}/` — см. M01 storage. Workspace key: `cab:{tid}:{cid}:{pid}`.
