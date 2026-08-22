# Project contract

## Семантика

Project = **work unit** внутри **CabinetInstance**: metadata + workspace + agent session + triggers + attachments.

Owner tuple: `(company_id, cabinet_id, owner_employee_id)`.

## Сущность Platform `Project`

| Поле | Описание |
|------|----------|
| `id` | `proj_*` |
| `company_id` | |
| `cabinet_id` | Dynamic CabinetInstance |
| `slug` / `name` | |
| `owner_employee_id` | |
| `status` | `active` \| `paused` \| `deleted` |
| `workspace_key` | FS / volume key |
| `container_ref` | Runtime id |
| `agent_provider` | Optional override |

**Нет** `profile_id` code-module — тип кабинета = содержимое instance (meta + packages).

## Lifecycle

```text
create → materialize (prompts/skills/AGENTS + platform cabinet.* + enabled MCP packages)
      → ensure container_ref
      → accept triggers / agent sessions
      → pause | resume | archive
```

## Обязанности

| Сторона | Делает |
|---------|--------|
| Platform | Project CRUD; container; triggers; AI keys; attachments meta; agent routing |
| Cabinet Runtime | Meta/data contracts; package sandbox; materialize inputs |
| Agent (in project) | May call `cabinet.*` + deployed packages to extend cabinet |

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

Headers: `X-Cabinet-Id`, `X-Project-Id` as needed.

## Bot

Telegram/Commerce bot = optional **trigger transport**, не owner sessions.
