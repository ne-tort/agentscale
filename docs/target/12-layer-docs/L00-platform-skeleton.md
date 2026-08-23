# L00 — Platform skeleton

| Поле | Значение |
|------|----------|
| Status | done |
| Quality | 8 |
| Quality note | DoD закрыт: layout, health+meta, AppError, Alembic up/down CI, features placeholder, contract tests; veto пуст. P0 w1–w3: core lifespan + Redis + ObjectStorage (subset) |
| Plan | [L00](../11-implementation-plan/L00-platform-skeleton.md), [P0](../11-implementation-plan/P0-platform-infra.md) |
| Canon | [STUB](../../../STUB.md), [AGENTS](../../../AGENTS.md), [LAYOUT.md](../../../apps/api/src/prodavan/LAYOUT.md), [13](../13-platform-infra/) |
| Last updated | 2026-08-24 — stack compose brokers + hydrate + CORS/cache |
| Owners | — |

---

## Семантика

Фундамент репозитория приложений: процесс API, цепочка Alembic, каркас Flutter, единый error envelope, CI smoke.  
**Не** identity, не домен кабинетов, не agent. Точка, с которой остальные слои добавляют модули, не таща legacy.

## Что сделано

| Сделано | Не сделано (следующие слои) |
|---------|------------------------------|
| Package layout api / application / domain / infrastructure | L01+ domain entities |
| `GET /health` + `/api/v1/health` с version/build/service | — |
| `AppError` → problem+json; 404 envelope | — |
| Settings + `.env.example` (DB, build meta, слоты KC/vault) | KC/vault clients |
| Alembic `stub_bootstrap`; CI upgrade + downgrade smoke | Product migrations |
| Flutter stub home + empty `lib/features/` | L02 EntityCollection / gallery |
| Pytest health/error; flutter analyze+test gate | — |
| `prodavan.core`: `LifespanManager` / `LifespanResource` | Kafka / MinIO / Celery managers |
| `main.py` lifespan → `build_lifespan_manager()` | — |
| `RedisManager` + REDIS_URL / readiness checks | Redis required in all envs; Celery broker |
| `ObjectStorageManager` local\|s3 + attach/packages | Materialize workspace tree via object store |
| DB + trigger_worker as `LifespanResource` | Celery replaces in-process worker |

## Как сделано

1. FastAPI factory в `main.py`; probes без префикса + те же под `/api/v1`.
2. Health читает `settings.app_version` / `build_id` / `app_name`.
3. `AppError` в `domain/errors.py`; handler в `exception_handlers.py`.
4. CI API: Postgres → alembic upgrade → downgrade base → upgrade → ruff → pytest.
5. CI Flutter: analyze + test + запрет raw Color / modals в features.
6. Legacy feature/shell/cabinets деревья не подключены к `app.dart` (stub only).
7. **P0:** `core/wiring.build_lifespan_manager` регистрирует `DatabaseEngineResource` → `RedisManager` → `TriggerWorkerResource`; `main.py` только делегирует.
8. **P0:** `/health/ready` проверяет DB; Redis — если `REDIS_URL` задан или `REDIS_REQUIRED`; отчёт `resources` из lifespan.
9. **P0:** `ObjectStorageManager` в lifespan; C-ATTACH/C-MCP-PKG пишут `object://` keys (local backend = same paths under `storage_root`).

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-API-HEALTH | `GET /api/v1/health` (+ live/ready) + problem+json | live |
| C-CACHE | RedisManager + settings; ready checks when URL set | **live** (subset; URL optional) |
| C-OBJECT-STORE | ObjectStorageManager local\|s3; `object://` refs | **live** (subset) |
| C-JOBS | WorkerManager + Celery tasks (drain/idle/rematerialize) | **live** (subset) |
| C-EVENT-BUS | KafkaManager + EventEnvelope dual-write | **live** (subset) |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| — | — | — |

## Связи

Все слои опираются на L00. Обратных доменных зависимостей нет.

## Инварианты

- Нет password-login / HS256 issuer / procurement domain в skeleton.
- Миграции не воскрешают legacy pack tables.
- Feature shells не обгоняют L02 (пустой `features/` + stub home).

## Карта кода

```text
apps/api/src/prodavan/
  LAYOUT.md
  main.py              # lifespan → core.wiring
  core/                # P0 LifespanManager + infra managers
    lifespan/
    infra/             # database, redis, trigger_worker resources
    wiring.py
  api/                 # routes, exception_handlers
  application/
  domain/errors.py
  config/settings.py   # + REDIS_URL / REDIS_REQUIRED
  infrastructure/persistence/
apps/api/alembic/versions/2026082301_stub_bootstrap.py
apps/flutter/lib/
  main.dart, app.dart
  core/theme, core/widgets
  features/
.github/workflows/ci-api.yml
.github/workflows/ci-flutter.yml
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| API health + OpenAPI/contract tests | done | pytest envelope |
| Alembic bootstrap up/down CI | done | |
| Flutter stub + no Dialog in core for entity pick | done | DangerConfirmPage is page |
| Documented layout | done | LAYOUT.md |
| Lint/test CI green | done | workflows updated |
| No legacy login/procurement | done | not in running app |
| `LifespanManager` + `LifespanResource` register | **done** (P0 w1) | `core/lifespan/`; see [13](../13-platform-infra/core-managers.md) |
| Infra managers в `prodavan.core` | **partial** | Redis + ObjectStorage + Worker + Kafka; hole: Kafka sole-path cutover |
| Redis обязателен во всех окружениях | **partial** | stack compose + k8s sketch set REDIS_URL; default local still empty |
| Object store SoT для всех blobs | **partial** | materialize + hydrate + delete_prefix; archive wipe packages; readiness when required; live MinIO mount — hole |
| Celery executor для background jobs | **partial** | CLI bootstrap + beat drain/idle; stack/k8s worker; default off → in-process |
| Middleware register в core | **done** (subset) | `register_cors` in `core/middleware.py` |
| Redis cache facade | **done** (subset) | policy/sub/quota peek; HMAC secrets never in Redis |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/ -q
# with Postgres: alembic upgrade head && alembic downgrade base && alembic upgrade head
cd apps/flutter && flutter analyze --no-fatal-infos && flutter test
```

## Оценка качества

Рубрика: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 2 | DoD L00 закрыт |
| B. Контракты | 2 | C-API-HEALTH live + tests |
| C. Инварианты и проверки | 2 | CI migrate/down + pytest + flutter |
| D. As-built ясность | 2 | эта карточка |
| **Quality (итог)** | **8** | минимум done; hardening L01+ later |
