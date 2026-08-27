# Platform Admin — metrics

Кросс-компанийный мониторинг на вкладке «Сводка» и на `AdminCompanyDetailPage`.

## Presence (online)

Auth Service публикует `auth.login`, `auth.token_refreshed`, `auth.logout` в platform Kafka bus.
Metrics consumer (`application/metrics/`) обновляет Redis:

- `presence:employee:{id}` — сотрудник онлайн (TTL `metrics_presence_ttl_sec`, default 900)
- `presence:company:{login}` — org principal компании онлайн

Read path: `MetricsReadService` batch MGET при list/metrics. Без Redis — `online: false`.

## Метрики уровня компании (карточка / detail)

| Метрика | Описание |
|---------|----------|
| `employees_total` / `employees_active` | Сотрудники всего / не отключённые |
| `employees_online` | Сотрудники с активной Redis-presence (login/refresh в TTL) |
| `subscription_ends_at` / `subscription_lifetime` | Срок подписки на Prodavan |
| `cabinets_active` | Число CabinetInstance в компании |
| `running_cabinets` | ACTIVE кабинеты с ≥1 ACTIVE (не paused) проектом |
| `cabinets_quota` | Лимит create/import (если задан) |
| `projects_total` | Проекты по всем кабинетам компании |
| `agent_tokens_used` | Суммарные токены (если провайдер отдаёт) |
| `agent_messages` | Сообщения в чатах агента |
| `storage_bytes` | Объём workspace (оценка) |
| `last_activity_at` | Последняя активность сотрудника |

## Алерты

| Алерт | Условие |
|-------|---------|
| Subscription expiring | `ends_at` в ближайшие N дней, не lifetime |
| Key expiring | AI key `next_renewal_at` скоро |
| No keys bound | У компании 0 bindings при активных сотрудниках |
| High usage | Токены/проекты выше порога (конфиг) |

## API (логический контракт)

```http
GET /api/v1/admin/metrics/companies
GET /api/v1/admin/companies/{id}/metrics
```

Ответы агрегируются из platform DB + Redis presence + telemetry (без чтения содержимого файлов проектов).

List endpoints обогащаются полем `online: bool` (компания или сотрудник).
