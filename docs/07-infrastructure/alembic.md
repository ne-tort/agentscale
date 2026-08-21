# Alembic (core DB migrations)

Канон ops для **platform** Postgres. Доменная схема продукта — из [`docs/target/`](../target/); текущий код API — [STUB](../../STUB.md).

## Где лежит

| Путь | Роль |
|------|------|
| `apps/api/alembic.ini` | Конфиг Alembic (`script_location=alembic`, `prepend_sys_path=src`) |
| `apps/api/alembic/env.py` | Async URL из `settings.database_url`, `target_metadata = Base.metadata` |
| `apps/api/alembic/versions/` | Цепочка ревизий (сейчас только `stub_bootstrap`) |
| `apps/api/docker-entrypoint.sh` | При `RUN_MIGRATIONS=1` (default): `alembic upgrade head` |

## Команды (из `apps/api`)

```bash
# URL: postgresql+asyncpg://… (см. Settings.database_url / CI DATABASE_URL)
alembic upgrade head
alembic current
alembic history
alembic downgrade -1
```

Новая ревизия (после появления ORM-моделей по target):

```bash
alembic revision -m "short_snake_name"
# или autogenerate, когда модели снова появятся в Base.metadata:
# alembic revision --autogenerate -m "…"
```

Правила:

1. **Одна линейная цепочка** `down_revision` — без веток без явной необходимости.
2. Миграции **идемпотентны** по смыслу upgrade на чистой БД; не править уже применённые ревизии на shared env — только новые файлы.
3. **Pack / cabinet SQL** не в core Alembic — через cabinet SPI `POST /migrate` (см. [ADR-001](../02-architecture/ADR-001-platform-core-vs-cabinet-spi.md)).
4. Role `prodavan_app`: в CI создаётся отдельным шагом до `upgrade`; в k3s — init job / Terraform (см. env-matrix).

## CI / runtime

| Место | Поведение |
|-------|-----------|
| `.github/workflows/ci-api.yml` | Postgres → create role → `alembic upgrade head` → pytest |
| API container entrypoint | `alembic upgrade head` затем uvicorn |
| k3s readiness | `/health/ready` = DB reachable (**не** проверка содержимого схемы) |

## Cutover со stub (важно)

Цепочка legacy-ревизий (`2026080801` …) **удалена**. На уже развёрнутом Postgres с старым `alembic_version`:

1. Остановить API.
2. Сбросить том / схему: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` (или пересоздать PVC).
3. Выдать права роли `prodavan_app`.
4. Запустить API → `upgrade head` применит `stub_bootstrap` (`stub_meta`).

Не пытаться `alembic stamp` старыми id — их больше нет в репозитории.

## Что дальше (продукт)

1. Модели SQLAlchemy по `docs/target/` (companies, employees, AI keys, …).
2. Новые файлы в `alembic/versions/` с `down_revision = "stub_bootstrap"` (или после squash).
3. Не тащить S4B/pipeline таблицы из git history без требований target.
