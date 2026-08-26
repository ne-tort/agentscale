# Alembic (platform Postgres)

Канон миграций **core** БД Prodavan. Cabinet pack SQL — только через SPI `POST /migrate` ([ADR-001](../02-architecture/ADR-001-platform-core-vs-cabinet-spi.md)).

## Источник истины

| Слой | Роль |
|------|------|
| **SQLAlchemy models** (`apps/api/src/.../persistence/models/`) | Декларативная схема — единственный источник истины |
| **`alembic/versions/*.py`** | Версионированные DDL/data steps, генерируются из моделей |
| **`alembic_version`** в Postgres | Факт применённой ревизии |

Модели регистрируются через `from prodavan.infrastructure.persistence.models import Base` в `alembic/env.py` (`target_metadata = Base.metadata`).

## Поток разработки (локально / PR)

```bash
cd apps/api
export DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@127.0.0.1:5432/prodavan

# 1. Изменить ORM-модель
# 2. Сгенерировать ревизию из diff модель ↔ текущий head
alembic revision --autogenerate -m "short_snake_name"

# 3. Ревью файла в alembic/versions/ — autogenerate не идеален
#    Ручное редактирование — только data migration, backfill, особые индексы

alembic upgrade head
alembic check          # модели ≡ БД после head
alembic downgrade -1   # опционально — smoke отката
alembic upgrade head
```

**Autogenerate в деплое — антипаттерн:** не детерминирован, без ревью в PR, гонки в k8s, риск DROP. В деплое только `upgrade head` по закоммиченным файлам.

## Деплой (единственный путь на shared env)

```text
PR merge → CI Images (API образ) → Argo sync
  → prodavan-api initContainer: ./scripts/migrate.sh
       1. alembic upgrade head
       2. alembic check
  → uvicorn (main container)
```

| Место | Поведение |
|-------|-----------|
| `infra/k3s/base/prodavan-api/deployment.yaml` | initContainer `migrate` → `./scripts/migrate.sh` |
| `infra/k3s/overlays/dev/patch-dev.yaml` | `imagePullPolicy: Always` на **api и migrate** (иначе кэш старого `:latest` → upgrade noop) |
| `apps/api/scripts/migrate.sh` | `upgrade head` → **current == head** → `alembic check` |
| `apps/api/Dockerfile` | `src` + `alembic` в **одном COPY** (buildx cache не должен рассинхронить ORM и revisions) |
| `.github/workflows/ci-api.yml` | Postgres → `upgrade head` → **`alembic check`** → downgrade/upgrade smoke → pytest |
| `.github/workflows/ci-images.yml` | После сборки образа — migration smoke (`migrate.sh` на ephemeral Postgres) |

Readiness `/health/ready` проверяет доступность Postgres, **не** содержимое схемы. Схема — ответственность initContainer.

## Правила ревизий

1. **Одна линейная цепочка** `down_revision` — без веток без явной необходимости.
2. **Не править** уже применённые ревизии на dev/staging/prod — только новый файл.
3. **Data migration** (UPDATE/backfill) — явный `op.execute` / batch; autogenerate не заменяет ревью.
4. Role `prodavan_app`: CI выдаёт GRANT после migrate; в k3s — bootstrap/Terraform.

## Если миграция «не доехала»

Симптом: 500 на API (missing column), initContainer в CrashLoop, `alembic check` падает в CI.
Типичный dev-баг: **api** тянет новый `:latest` (`Always`), а **migrate** сидит на кэше (`IfNotPresent`) → `upgrade` noop на старом head, UI ловит ORM 500.

**Делать:** исправить цепочку поставки (Dockerfile, migrate.sh, Always на migrate, CI smoke, образ `:latest` после merge) и **передеплоить** через GitOps.

**Не делать:** `kubectl exec alembic upgrade`, ручной `ALTER`, `alembic stamp` на shared env, обход Argo.

## Cutover со stub (legacy)

Цепочка `2026080801` … удалена. Старый `alembic_version` на томе без пересоздания схемы не поддерживается — см. исторический блок в git / L00 docs.

## Ссылки

- [runbook.md](runbook.md) — GitOps, без ручного кластера  
- [AGENTS.md](../../AGENTS.md) — запреты для агента  
- `apps/api/alembic.ini`, `apps/api/alembic/env.py`
