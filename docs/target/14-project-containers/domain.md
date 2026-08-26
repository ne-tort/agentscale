# Project Containers — domain

## Семантика

`ProjectContainer` = учётная запись **изолированного runtime** одного Project: identity в платформе + opaque handle в k8s + связь с workspace blobs (`object-ws` / MinIO).

**Не** Cabinet. **Не** starter bundle. **Не** AgentSession.

## Иерархия (канон)

```text
Company
  └── CabinetInstance
        └── Project  ──(1:1 MVP)──▶  ProjectContainer
                                         └── k8s Pod/Job (+ PVC/ephemeral)
```

Связь иерархическая: Container не «владеет» Project снаружи. Platform Admin UI может открывать Container и **каскадно** дергать Project ops.

## Сущность `ProjectContainer`

| Поле (логическое) | Описание |
|-------------------|----------|
| `id` | `pctr_*` |
| `project_id` | FK → `projects.id` UNIQUE (1:1 MVP) |
| `status` | см. ниже |
| `workspace_key` | совпадает с Project.workspace_key (blob prefix) |
| `runtime_ref` | opaque k8s id (pod name / uid); transitional: `object-ws:{key}` |
| `image` / `resources` | профиль isolator (requests/limits) |
| `last_error` | последняя ошибка оркестратора |
| `created_at` / `updated_at` | |

Project продолжает хранить `container_ref` как **opaque** указатель (или FK `container_id`) — без парсинга pod internals вне модуля 14.

## Статусы

| Status | Смысл |
|--------|-------|
| `pending` | Запись есть; runtime ещё не создан |
| `running` | Pod Ready / compute up |
| `paused` | Compute stopped; volume/blobs **keep** |
| `failed` | Terminal/retryable failure (см. errors-ops) |
| `terminating` | Delete in progress |
| `deleted` | Soft / gone (pod removed) |

Маппинг к Project.status:

| Project | Container (ожидание) |
|---------|----------------------|
| `active` | `running` (или `pending`→start) |
| `paused` | `paused` |
| `deleted` | `deleted` / wipe |

Расхождение Project↔Container = **drift** → reconcile.

## Границы модуля

**Владеет:**

- CRUD учётной записи Container
- Все вызовы k8s API для project sandboxes (`ContainerRuntimePort`)
- Reconcile orphans / zombies
- Сбор runtime metrics (CPU/RAM/disk/pod conditions)

**Не владеет:**

- Project business rules (AI key gate, triggers, chat)
- Cabinet schema / MCP packages
- AI key inventory
- Starter bundle catalog

## Порт `ContainerRuntimePort`

Единственный контракт наружу (вызывает `ProjectService` / admin reconcile job):

| Method | Эффект |
|--------|--------|
| `ensure_created` | Row + materialize hydrate contract + (P3) create runtime |
| `start` | Start/resume compute |
| `pause` | Stop compute; keep volume |
| `stop` | Alias pause или hard stop per profile |
| `delete` | Remove runtime + optional wipe blobs (по флагу Project delete) |
| `force_kill` | Grace=0 delete pod; clear stuck |
| `get` / `get_metrics` | Status + k8s metrics snapshot |
| `list_orphans` | Pods by label without matching DB row / project deleted |
| `reconcile` | Align desired (from Project) vs actual |

Реализация: stub (object-ws) → k8s client. **Запрещено** создавать Pod из `AiKeysService`, session adapter, Flutter напрямую.
