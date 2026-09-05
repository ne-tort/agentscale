# Platform Admin — metrics

Кросс-компанийный мониторинг на вкладке «Сводка» и на `AdminCompanyDetailPage`.

## Два слоя observability

| Слой | Что | Где |
|------|-----|-----|
| **k8s metrics-server** | CPU/RAM pod'ов (`metrics.k8s.io`) | Cluster addon (kube-system), **не** отдельный Prodavan Deployment |
| **Metrics BC** | Presence, overview counters, pod runtime samples | Изолированный слой в `prodavan-api` (`application/metrics/`) |

Pipeline pod runtime:

```text
PodReconcileService / PodMetricsSampler
  → metrics-server (read)
  → Kafka prodavan.metrics.events (pod.metrics.sample)
  → MetricsConsumerResource → Redis (latest + series)
  → GET .../containers/{project_id}/metrics | runtime summary
```

Pipeline overview counters (Kafka-first):

```text
Auth / cabinet heartbeat / agent session / storage sampler / relation.*
  → platform | metrics | relation Kafka topics
  → MetricsConsumer → entity link graph + counter accumulator (Redis)
  → GET .../metrics overview (store overlay; no live FS for storage)
  → GET .../metrics/series (counter deltas)
```

### Metric facts (`prodavan.metrics.events`)

| event_type | Meaning |
|------------|---------|
| `metrics.presence.heartbeat` / `clear` | employee online |
| `metrics.counter.delta` | named counter (`agent_requests`, …) + cascade |
| `metrics.usage.turn` | tokens for one turn (`request_only` for request count) |
| `metrics.storage.snapshot` | absolute bytes for project/cabinet/company |
| `pod.metrics.sample` | CPU/RAM |

Named counters: `employees_total`, `projects_total`, `cabinets_total`, `agent_requests`, `agent_tokens`, `storage_bytes`.

`agent_requests` = user turns (`user_message` / counter delta), **never** SSE `text_delta` chunks.

## Presence (online)

Auth Service публикует `auth.login`, `auth.token_refreshed`, `auth.logout` (с roles) в platform Kafka bus.
Employee cabinet shell: `POST /cabinets/{id}/presence/heartbeat` каждые ~2 мин.
Metrics consumer обновляет Redis:

- `presence:employee:{id}` — сотрудник онлайн (TTL `metrics_presence_ttl_sec`, default 900)
- `presence:company:{login}` — org principal компании онлайн

Read path: `MetricsReadService` batch MGET при list/metrics. Без Redis — `online: false`.

## Метрики уровня компании (карточка / detail)

| Метрика | Описание |
|---------|----------|
| `employees_total` / `employees_active` | Сотрудники всего / не отключённые |
| `employees_online` | Сотрудники с активной Redis-presence (login/refresh/heartbeat в TTL) |
| `subscription_ends_at` / `subscription_lifetime` | Срок подписки на Prodavan |
| `cabinets_active` | Число CabinetInstance в компании |
| `running_cabinets` | ACTIVE кабинеты с ≥1 ACTIVE (не paused) проектом |
| `cabinets_quota` | Лимит create/import (если задан) |
| `projects_total` | Проекты по всем кабинетам компании |
| `agent_tokens_used` | Accumulated tokens (Metrics BC; SQL usage как fallback до backfill) |
| `agent_requests` (`agent_messages` alias) | User→agent turns |
| `storage_bytes` | Last storage snapshot (sampler / rebuild) |
| `last_activity_at` | Последняя активность |

## Pod / container runtime metrics

| Поле | Источник |
|------|----------|
| `cpu_millicores`, `memory_bytes` | metrics-server via sampler |
| `phase`, `restarts`, `ready` | k8s Pod status |
| `metrics_degraded` | metrics-server недоступен |

Delta optimization: publish/store только при interval elapsed, status change или CPU/RAM delta > threshold.

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
GET /api/v1/admin/metrics/series?metric=agent_tokens&entity_type=company&entity_id=...&window=7d
POST /api/v1/admin/metrics/rebuild
GET /api/v1/cabinets/{id}/metrics
GET /api/v1/cabinets/{id}/metrics/series?metric=agent_requests&window=7d
POST /api/v1/cabinets/{id}/presence/heartbeat
GET /api/v1/companies/{company_id}/containers/{project_id}/metrics?window=1h
```

Overview fields prefer Redis counters; SQL/FS used for backfill/rebuild and transitional fallbacks (not `text_delta`, not per-request storage scan).

List endpoints обогащаются полем `online: bool` (компания или сотрудник).
