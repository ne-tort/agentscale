# Project contract

## Семантика

Project = **work unit** внутри cabinet: metadata + workspace + agent session + triggers + attachments.

Owner tuple: `(company_id, cabinet_id, owner_employee_id)`.

## Сущность Platform `Project`

| Поле | Описание |
|------|----------|
| `id` | `proj_*` |
| `company_id` | |
| `cabinet_id` | Instance кабинета |
| `profile_id` | Тип модуля |
| `slug` / `name` | |
| `owner_employee_id` | |
| `status` | `active` \| `paused` \| `deleted` |
| `workspace_key` | FS / volume key |
| `container_ref` | Runtime id (`local-ws:…` сейчас; k8s pod later) |
| `agent_provider` | Optional override preferred provider |

## Lifecycle (platform)

```text
create → materialize_project (cabinet SPI)
      → ensure container_ref
      → accept triggers / agent sessions
      → pause | resume | archive
```

## Обязанности

| Сторона | Делает |
|---------|--------|
| Platform | CRUD metadata; container_ref; route triggers; AI key resolve; attachment metadata; agent session routing |
| Cabinet | Domain rows; materialize; domain trigger handlers |

## API (логический)

```http
POST   /api/v1/cabinets/{cabinet_id}/projects
GET    /api/v1/cabinets/{cabinet_id}/projects
GET    /api/v1/projects/{id}
POST   /api/v1/projects/{id}/pause
POST   /api/v1/projects/{id}/resume
DELETE /api/v1/projects/{id}
POST   /api/v1/projects/{id}/triggers
POST   /api/v1/projects/{id}/attachments
```

Context headers: `X-Cabinet-Id` (+ `X-Project-Id` когда нужно).  
Домен кабинета — SPI commands/queries, не раздувание platform API.

## Bot

Commerce / Telegram bot = **transport** внешних triggers (`telegram.message`), не источник истины для agent sessions.
