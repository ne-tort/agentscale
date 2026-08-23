# Triggers

Триггер — событие, которое запускает/продолжает агента и/или домен кабинета.

## Две шины (не смешивать)

| Шина | Scope | Примеры |
|------|-------|---------|
| **Project triggers** | Всегда `project_id` | `chat.message`, `project.prepare`, `webhook.http` |
| **Platform events** | Company / employee / project lifecycle | `company.suspended`, `employee.disabled`, `project.created` |

**Durable bus (канон):** обе шины — **Kafka** ([13-platform-infra](../13-platform-infra/)). PG outbox-lite / таблица `project_triggers` — transitional implementation, не prod-канон шины. Исполнение фоновых drain/jobs — **Celery**, не in-process API worker.

Project triggers: `POST /api/v1/projects/{id}/triggers`.  
Platform events: emit → Kafka → cabinet `on_platform_event` (и прочие consumers).

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
