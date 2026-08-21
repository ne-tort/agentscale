# Triggers

Триггер — событие, которое запускает/продолжает агента и/или домен кабинета.

## Две шины (не смешивать)

| Шина | Scope | Примеры |
|------|-------|---------|
| **Project triggers** | Всегда `project_id` | `chat.message`, `project.prepare`, `webhook.http` |
| **Platform events** | Company / employee / project lifecycle | `company.suspended`, `employee.disabled`, `project.created` |

Project triggers: `POST /api/v1/projects/{id}/triggers`.  
Platform events: internal emit → cabinet `on_platform_event`.

## Project trigger kinds (platform)

| kind | Источник | Auth |
|------|----------|------|
| `chat.message` | Employee UI | Bearer + membership |
| `chat.regenerate` | UI | Bearer |
| `project.prepare` | Platform after create | Internal |
| `system.schedule` | Cron | Service token |
| `telegram.message` | Bot transport | Bot secret / signed |
| `webhook.http` | External | HMAC / cabinet webhook secret |

## Dispatch

```text
accept trigger → audit
  → optional cabinet domain handler (SPI)
  → optional AgentProviderPort.send / create
```

## Инварианты

- Project trigger всегда scoped к `project_id` + cabinet membership.
- Кабинет не подписывается на чужой project.
- Bot = transport, не session store.
- Побочные эффекты аудируются.
