# P0 — Platform infrastructure

| Поле | Значение |
|------|----------|
| Priority | **P0** (выше обычных LNN-волн при конфликте ресурсов) |
| Canon | [13-platform-infra/](../13-platform-infra/) |
| Refactor | **Significant refactor allowed** для L00, L03 (blob-adjacent), L07, L08 |
| Status | `doing` (волны 1–5 subset + cache use + Kafka dispatch mode + k8s sketches) |

## Цель

Ввести канонический стек и backend core:

- **Kafka** — durable bus для project triggers + platform events (+ межмодульные контракты)
- **MinIO** (S3) — blobs: attachments, workspace materialize, MCP package artifacts
- **Celery** + **Redis** — фоновые jobs (drain, idle-pause, rematerialize, …)
- **`prodavan.core`**: infra managers + `LifespanManager` / `LifespanResource`

Postgres и Keycloak/Vault роли не меняются.

## Прогресс волн

| # | Волна | Статус | Заметки |
|---|-------|--------|---------|
| 1 | core + lifespan | **done** | `DatabaseEngineResource`, `TriggerWorkerResource`, wiring |
| 2 | Redis | **done** (subset) | URL optional; `REDIS_REQUIRED` для prod; нет docker-compose Redis в этом PR |
| 3 | MinIO | **done** (subset) | ObjectStorage + attach/packages + materialize AGENTS/mcp/zip via store; sandbox extract local |
| 4 | Celery | **done** (subset) | `WorkerManager` + tasks drain/idle/rematerialize; beat schedule; in-process skipped when Celery executor active |
| 5 | Kafka | **done** (subset) | dual-write + consumer `kick`\|`dispatch` (`claim_by_id` + Celery `dispatch_trigger`); PG claim still SoT |

## Definition of Done

- [x] Пакет `core` с managers: Redis + ObjectStorage + Worker + Kafka (**live subset**)
- [x] FastAPI lifespan только через `LifespanManager`; ресурсы зарегистрированы
- [x] Redis live (health + settings); Celery broker = Redis URL when Celery enabled
- [x] MinIO/S3 manager + attachments/packages + materialize text/zip via object store; sandbox extract local — **hole**
- [x] Celery: trigger drain / idle sweep / rematerialize tasks; in-process — transitional fallback
- [x] Kafka: dual-write + consumer kick|dispatch; **hole:** PG outbox still claim SoT (SPI delivery not Kafka-only)
- [x] Контракты: `C-CACHE` + `C-OBJECT-STORE` + `C-JOBS` + `C-EVENT-BUS` → **live** (subset)
- [x] As-built L00/L07/L08 обновлены (w1–w5 subset)
- [ ] Checklist master: строка P0 → `done` (осталось: full Kafka cutover без PG claim SoT + Helm/prod hardening)

## Дыры логики (следующая итерация)

- Kafka consumer **ускоряет** Celery (`kick` drain или `dispatch` per-id); PG outbox остаётся claim SoT; SPI fan-out не Kafka-only.
- Package sandbox: hydrate-from-zip есть; **live mount** workspace из MinIO в pod — hole.
- C-CACHE: company agent policy + subscription peek закэшированы; другие hot paths ещё без cache.
- k8s sketches в `deploy/k8s/{redis,minio,kafka,celery}` — не Helm, без PVC/NetworkPolicy/TLS.

## Волны реализации

| # | Волна | Содержание | Затрагивает |
|---|-------|------------|-------------|
| 1 | core + lifespan | `LifespanManager`, `LifespanResource`, register; вынести ad-hoc из `main.py` | L00 |
| 2 | Redis | manager, settings, health | L00 |
| 3 | MinIO | ObjectStorageManager; C-ATTACH → keys; затем materialize/packages | L07, L06 packages |
| 4 | Celery | WorkerManager; перенос TRIGGER/idle workers | L07, L08 |
| 5 | Kafka | topics + publishers/consumers; cutover с PG outbox | L07, platform events |

Порядок жёсткий по зависимостям; внутри волны — крупные PR допустимы.

## Явное разрешение рефакторинга

Разрешено ломать transitional API internals:

- `local-ws:` container_ref → object-store-backed refs
- PG-only trigger claim loop → Kafka + Celery
- secrets `file://` для **product blobs** не расширять; storage_root для projects — выпил

Не ломать без migration note: публичные HTTP контракты C-ATTACH / C-TRIGGERS (менять совместимо или major в Compatibility log).

## Связь с контрактами

| ID | Status | Назначение |
|----|--------|------------|
| [C-OBJECT-STORE](contracts-index.md) | **live** (subset) | MinIO/S3 put/get/delete + refs |
| [C-EVENT-BUS](contracts-index.md) | **live** (subset) | Kafka envelopes triggers/platform |
| [C-JOBS](contracts-index.md) | **live** (subset) | Celery task names / idempotency |
| [C-CACHE](contracts-index.md) | **live** (subset) | Redis cache/lock keys conventions |

Существующие `C-MATERIALIZE`, `C-TRIGGERS`, `C-ATTACH` обновляются по мере cutover (Compatibility log).

## Вне скоупа этого документа

Prod Helm charts и hardening кластера — после sketches; канон стека в [stack.md](../13-platform-infra/stack.md). Sketches: [deploy/k8s/README.md](../../deploy/k8s/README.md).
