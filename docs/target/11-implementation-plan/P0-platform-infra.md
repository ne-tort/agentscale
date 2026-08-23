# P0 — Platform infrastructure

| Поле | Значение |
|------|----------|
| Priority | **P0** (выше обычных LNN-волн при конфликте ресурсов) |
| Canon | [13-platform-infra/](../13-platform-infra/) |
| Refactor | **Significant refactor allowed** для L00, L03 (blob-adjacent), L07, L08 |
| Status | `doing` (волны 1–4: lifespan + Redis + ObjectStorage + Celery subset) |

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
| 3 | MinIO | **done** (subset) | `ObjectStorageManager` local|s3; attachments + cabinet packages → `object://`; materialize FS tree ещё local |
| 4 | Celery | **done** (subset) | `WorkerManager` + tasks drain/idle/rematerialize; beat schedule; in-process skipped when Celery executor active |
| 5 | Kafka | todo | C-EVENT-BUS |

## Definition of Done

- [x] Пакет `core` с managers: Redis + ObjectStorage + Worker (**live subset**); Kafka — **hole** волна 5
- [x] FastAPI lifespan только через `LifespanManager`; ресурсы зарегистрированы
- [x] Redis live (health + settings); Celery broker = Redis URL when Celery enabled
- [x] MinIO/S3 manager + attachments/packages via object store; materialize workspace tree — **hole**
- [x] Celery: trigger drain / idle sweep / rematerialize tasks; in-process — transitional fallback
- [ ] Kafka: envelope для triggers + platform events (или dual-write с явным cutover в as-built)
- [x] Контракты: `C-CACHE` + `C-OBJECT-STORE` + `C-JOBS` → **live** (subset); `C-EVENT-BUS` — planned
- [x] As-built L00/L07/L08 обновлены (w1–w4)
- [ ] Checklist master: строка P0 → `done`

## Дыры логики (следующая итерация)

- Materialize workspace tree всё ещё local FS SoT (не только object store).
- Celery worker/beat не в docker-compose/k3s; API только конфигурирует app — нужен отдельный процесс.
- `CELERY_ENABLED` default false; без Redis+worker jobs не крутятся в prod until wired.
- Kafka ещё нет (C-EVENT-BUS).
- S3 + `mirror_local` dual-write; MinIO в compose ещё не обязателен.
- CORS middleware ещё не через core register.
- Redis cache/lock facade почти не используется application-кодом.

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
| [C-EVENT-BUS](contracts-index.md) | planned | Kafka envelopes triggers/platform |
| [C-JOBS](contracts-index.md) | **live** (subset) | Celery task names / idempotency |
| [C-CACHE](contracts-index.md) | **live** (subset) | Redis cache/lock keys conventions |

Существующие `C-MATERIALIZE`, `C-TRIGGERS`, `C-ATTACH` обновляются по мере cutover (Compatibility log).

## Вне скоупа этого документа

Helm/k3s manifests и реальный деплой кластера — следующая итерация после кода managers; канон стека уже зафиксирован в [stack.md](../13-platform-infra/stack.md).
