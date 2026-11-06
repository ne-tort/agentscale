# GitHub Actions (as-built)

| Workflow | Trigger | Role |
|----------|---------|------|
| `ci-gate.yml` | PR / dispatch | `prodavan-ops validate` + api/flutter/schemas (L1 unit) |
| `ci-nightly.yml` | cron 02:00 UTC | L2 `pytest -m integration` |
| `ci-e2e.yml` | label `e2e` / `[e2e]` title / dispatch | L2 + L3a k8s Job + L3b live (opt-in) |
| `ci-images.yml` | main paths / dispatch | GHCR `:latest` + SHA |
| `verify-dev.yml` | after Images / infra push | `rollout` + `wait` + HTTP smoke |
| `auto-merge.yml` | Gate success | squash + dispatch Images |

Подробнее: [`e2e.md`](e2e.md).

Deploy is **Argo CD** from git. Runners: **systemd-сервисы на dev-VM** (`vm-*`, [`infra/github-runner-vm`](../../infra/github-runner-vm/README.md); Docker Desktop pack [`infra/github-runner`](../../infra/github-runner) — исторический).  
После ребута VM раннеры (`actions.runner.*`) поднимаются сами; kubeconfig `/home/runner/.kube/prodavan-dev.yaml` кладёт terraform — Windows-шаги (`Sync-KubeForDocker.ps1`, `Start-Runners.ps1`) не нужны (легаси Docker Desktop-эры). Ops: [`infra/ops`](../../infra/ops).

## PostgreSQL integration suite (`postgres-integration`)

`tests/migration/postgres_integration` доказывает то, чего не докажет ни SQLite,
ни моки: `FOR UPDATE SKIP LOCKED`, aggregate CAS на живом движке, откат
транзакции, deferrable-цикл M05, partial unique и обратимость миграций. Без
`PRODAVAN_TEST_DATABASE_URL` каталог **скипается целиком**, поэтому до
MIG-CI-PG-01 сьют в CI не исполнялся — и найденный им дефект (lease store
возвращал «применено», ничего не записав) остался бы незамеченным.

Job **`postgres-integration`** живёт в [`ci-api.yml`](../../.github/workflows/ci-api.yml)
рядом с job'ом `test` и вызывается из CI Gate через `api` (`workflow_call`), то
есть на каждом PR. Что он делает:

| Шаг | Смысл |
|-----|-------|
| service `postgres:16.15-alpine` | `5432` контейнера публикуется на **эфемерный** хост-порт (все раннеры делят один docker daemon — фиксированный порт сталкивался бы с параллельным прогоном), healthcheck `pg_isready -U test -d prodavan_test` |
| `Resolve DSNs` | `PRODAVAN_TEST_DATABASE_URL` + `PRODAVAN_TEST_MIGRATION_DATABASE_URL`; хост из `PRODAVAN_CI_HOST` (Docker Desktop) или `127.0.0.1`, порт — из `docker port <service>` |
| `Wait for Postgres` | TCP-проба *с раннера*: healthcheck контейнера не доказывает, что опубликованный порт доступен |
| `Create empty migration-cycle database` | пересоздаёт пустую `prodavan_migrate` — без неё `test_alembic_cycle.py` скипается по контракту фикстуры |
| `Pytest (PostgreSQL integration — must not skip)` | `pytest tests/migration/postgres_integration -q --no-header --strict-markers -rs --junitxml=…` |
| `Assert the suite actually executed` | читает JUnit и требует `collected > 0` **и** `skipped == 0` |

Почему отдельный шаг-ассерт, а не флаг pytest: **скипнутый прогон — это exit 0**.
`--strict-markers` ловит опечатку в маркере, но не скипы; `-rs` только печатает
причины. Единственный надёжный признак «сьют действительно выполнился» — счётчики
в JUnit-отчёте, поэтому job падает, если хоть один тест скипнут или если не
собрано ни одного.

Порт хоста задан одной переменной `PG_PORT`: пусто в CI (порт берётся из
`docker port`, чтобы параллельные прогоны не сталкивались), `55432` — при
локальном прогоне тех же скриптов против контейнера.

### Локально — то же, что в CI

```bash
docker run -d --name pg-test \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=prodavan_test \
  -p 55432:5432 postgres:16.15-alpine
docker exec pg-test createdb -U test prodavan_migrate

cd apps/api
export PRODAVAN_TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/prodavan_test
export PRODAVAN_TEST_MIGRATION_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/prodavan_migrate
pytest tests/migration/postgres_integration -q --no-header --strict-markers -rs
```

Windows: `$env:PRODAVAN_TEST_DATABASE_URL = "..."`. Alembic-цикл оставляет
`prodavan_migrate` непустой — перед повторным полным прогоном её надо пересоздать
(`dropdb`/`createdb`), иначе два теста скипнутся. Подробности и разбор того, что
сьют вскрыл на живом движке: [`tests/migration/postgres_integration/README.md`](../../apps/api/tests/migration/postgres_integration/README.md).

### Что новый job не ломает

Job'ы `test` (ci-api), nightly и e2e запускают свои наборы без этого DSN — сьют
там скипается, и это ожидаемо. Обязателен к исполнению только `postgres-integration`;
изменение схемы/путей сьюта должно обновлять и его `pytest`-шаг.

