# Project contract

## Семантика

Project = **work unit** внутри **CabinetInstance**: metadata + workspace + agent session + triggers + attachments.

Иерархия: `cabinet_id` (FK, NOT NULL). `created_by_employee_id` (= `owner_employee_id` в ORM) — metadata создателя, не ownership.

## Сущность Platform `Project`

| Поле | Описание |
|------|----------|
| `id` | `proj_*` |
| `company_id` | org-контекст (quota/metrics) |
| `cabinet_id` | Dynamic CabinetInstance |
| `slug` / `name` | |
| `created_by_employee_id` | metadata creator (`owner_employee_id` в API alias) |
| `visibility_mode` | `cabinet_shared` (default) \| `restricted` |
| `status` | `active` \| `paused` \| `completed` \| `deleted` |
| `workspace_key` | FS / volume key |
| `container_ref` | legacy opaque ref (→ `primary_runtime_unit`) |
| `primary_runtime_unit_id` | nullable FK → `ProjectRuntimeUnit` |
| `agent_provider` | Optional override |

**Нет** `profile_id` code-module — тип кабинета = содержимое instance (meta + packages).

## Access

| Mode | Кто видит |
|------|-----------|
| `cabinet_shared` | employee с cabinet assignment |
| `restricted` | cabinet assignment **и** project assignment (Relations) |

Company admin / platform admin — все проекты в org scope.

## Lifecycle

```text
create → materialize (metadata + workspace stub; runtime unit optional)
      → attach runtime unit (0..N) → project.started
      → accept triggers / agent sessions
      → pause | resume | complete | delete
```

## BC `project_service` (in-process)

| Facade | Назначение |
|--------|------------|
| `ProjectCommand` | writes: CRUD, lifecycle, visibility, assignments |
| `ProjectQuery` | reads + visibility filter |
| `ProjectAccessPolicy` | ACL |
| `ProjectLifecycleEmitter` | `project.*` platform events |
| → `PodCommand` | runtime: делегирует в [`pod_service`](../14-project-containers/pod-service.md) (`sync_desired`) |

Другие BC вызывают только facades — не `ProjectRow` / SQL напрямую.  
Runtime orchestration (k8s Pod) — **не** в project_service после [P1](../11-implementation-plan/P1-pod-service.md) Phase 1.

## API (логический)

```http
POST   /api/v1/cabinets/{cabinet_id}/projects
GET    /api/v1/cabinets/{cabinet_id}/projects
GET    /api/v1/projects/{id}
PUT    /api/v1/projects/{id}/visibility
POST   /api/v1/projects/{id}/assignments
DELETE /api/v1/projects/{id}/assignments/{employee_id}
POST   /api/v1/projects/{id}/runtime-units
GET    /api/v1/projects/{id}/runtime-units
POST   /api/v1/projects/{id}/pause
POST   /api/v1/projects/{id}/resume
POST   /api/v1/projects/{id}/complete
DELETE /api/v1/projects/{id}
POST   /api/v1/projects/{id}/triggers
POST   /api/v1/projects/{id}/attachments
```

Headers: `X-Cabinet-Id`, `X-Project-Id` as needed.

## Bot

Telegram/Commerce bot = optional **trigger transport**, не owner sessions.
