# P0 — Platform infrastructure

| Поле | Значение |
|------|----------|
| Priority | **P0** (выше обычных LNN-волн при конфликте ресурсов) |
| Canon | [13-platform-infra/](../13-platform-infra/) |
| Refactor | **Significant refactor allowed** для L00, L03 (blob-adjacent), L07, L08 |
| Status | `doing` (волны 1–3: lifespan + Redis + ObjectStorage subset) |

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
| 4 | Celery | todo | WorkerManager; выпил in-process как единственного executor |
| 5 | Kafka | todo | C-EVENT-BUS |

## Definition of Done

- [x] Пакет `core` с managers: Redis + ObjectStorage (**live subset**); Kafka / Worker — **hole** волны 4–5
- [x] FastAPI lifespan только через `LifespanManager`; ресурсы зарегистрированы
- [x] Redis live (health + settings); Celery broker — **hole** (волна 4)
- [x] MinIO/S3 manager + attachments/packages via object store; materialize workspace tree — **hole** (всё ещё local-ws FS)
- [ ] Celery: trigger drain / idle sweep / rematerialize jobs; in-process worker не единственный executor
- [ ] Kafka: envelope для triggers + platform events (или dual-write с явным cutover в as-built)
- [x] Контракты: `C-CACHE` + `C-OBJECT-STORE` → **live** (subset); `C-EVENT-BUS` / `C-JOBS` — planned
- [x] As-built L00/L07 обновлены (w1–w3); L08 Gaps — transitional worker
- [ ] Checklist master: строка P0 → `done`

## Дыры логики (следующая итерация)

- Materialize (`AGENTS.md`, packages unzip, mcp.json) всё ещё пишет напрямую в `storage_root` FS — не через object store keys как единственный SoT.
- `OBJECT_STORE_BACKEND=local` по умолчанию; MinIO в compose/k3s ещё не подключён.
- S3 + `mirror_local`: dual-write для local-ws agent cwd; выпил mirror после pod mount из MinIO.
- Нет MinIO/Kafka/Celery в deps runtime-стеке деплоя (boto3 в API есть).
- In-process `TriggerWorkerResource` остаётся канон-дефектом до Celery.
- CORS middleware ещё не через core register.
- Application-код почти не использует Redis (нет cache helpers / lock facade).

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

| ID | Status на старте P0 | Назначение |
|----|---------------------|------------|
| [C-OBJECT-STORE](contracts-index.md) | planned | MinIO/S3 put/get/delete + refs |
| [C-EVENT-BUS](contracts-index.md) | planned | Kafka envelopes triggers/platform |
| [C-JOBS](contracts-index.md) | planned | Celery task names / idempotency |
| [C-CACHE](contracts-index.md) | planned | Redis cache/lock keys conventions |

Существующие `C-MATERIALIZE`, `C-TRIGGERS`, `C-ATTACH` обновляются по мере cutover (Compatibility log).

## Вне скоупа этого документа

Helm/k3s manifests и реальный деплой кластера — следующая итерация после кода managers; канон стека уже зафиксирован в [stack.md](../13-platform-infra/stack.md).
