# Platform Admin — metrics

Кросс-компанийный мониторинг на вкладке «Сводка» и на `AdminCompanyDetailPage`.

## Метрики уровня компании (карточка / detail)

| Метрика | Описание |
|---------|----------|
| `employees_total` / `employees_active` | Сотрудники всего / не отключённые |
| `subscription_ends_at` / `subscription_lifetime` | Срок подписки на Prodavan |
| `cabinets_active` | Число CabinetInstance в компании |
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

Ответы агрегируются из platform DB + telemetry (без чтения содержимого файлов проектов).
