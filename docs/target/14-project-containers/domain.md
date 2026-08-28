# ProjectRuntimeUnit — domain

## Сущность

`ProjectRuntimeUnit` — учётная запись **изолированного runtime** (0..N на Project). Заменяет канонический 1:1 `ProjectContainer`.

| Поле | Смысл |
|------|--------|
| `id` | `pru_*` |
| `project_id` | FK → Project |
| `kind` | `primary` \| `sandbox` \| `worker` |
| `status` | `pending` \| `running` \| `paused` \| `failed` \| `terminating` \| `deleted` |
| `runtime_ref` | nullable opaque: pod name/uid (когда Pod есть) |
| `last_error` | последняя ошибка оркестратора |

Project хранит `primary_runtime_unit_id` + legacy `container_ref` (transitional).

Проект **можно создать без runtime unit** — только metadata + workspace stub. Attach/detach — отдельные команды.

## Статусы ↔ Project

| Project.status | Runtime units (желаемое) |
|----------------|--------------------------|
| `active` | units могут быть `running` |
| `paused` | все units → pause (**нет** Pod; blobs в MinIO) |
| `completed` | read-only; units paused |
| `deleted` | units terminated; blobs keep до purge |

## Port `ContainerRuntimePort`

Единственный контракт наружу (`ProjectRuntimeManager` → adapter):

| Method | Эффект |
|--------|--------|
| `ensure_running` | start Pod / object-ws |
| `pause` | остановить compute |
| `terminate` | удалить Pod |

Запрещено: AiKeys → Port; UI → kubectl; размазывать client по `ProjectCommand`.

## As-built

Stub adapter: `object-ws:{workspace_key}` — см. gap **P-POD-01**.
