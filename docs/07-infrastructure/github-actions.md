# GitHub Actions (as-built)

| Workflow | Триггер | Что делает |
|----------|---------|------------|
| [`ci-gate.yml`](../../.github/workflows/ci-gate.yml) | `pull_request` → `main`, dispatch | требует зелёными пять job'ов: `infra`, `api`, `web`, `flutter`, `schemas`; job `gate` агрегирует их результаты |
| ├ `infra` | | `prodavan-ops` unit-тесты + `prodavan-ops validate` (kustomize render, пины образов, запрет `.sh`/compose/k3d) |
| ├ `api` | `workflow_call` → `ci-api.yml` | `test` → `postgres-integration` → `migration-suite` (см. ниже) |
| ├ `web` | `workflow_call` → `ci-web.yml` | pnpm: `tsc --noEmit && vite build` + vitest (`apps/web`) |
| ├ `flutter` | `workflow_call` → `ci-flutter.yml` | `pub get`, `analyze`, `test`, плюс запреты на raw `Color(0x…)` вне палитры и entity-модалки в `lib/features` |
| └ `schemas` | `workflow_call` → `ci-schemas.yml` | `tools/validate_schemas.py` (cabinet pack + profile schema) |
| [`ci-nightly.yml`](../../.github/workflows/ci-nightly.yml) | cron 02:00 UTC, dispatch | L2: `alembic upgrade head` + `alembic check` + `pytest tests/integration -m integration` на эфемерном Postgres (порт 55433) |
| [`ci-e2e.yml`](../../.github/workflows/ci-e2e.yml) | label `e2e` / `[e2e]` в заголовке / dispatch | L2 + L3a k8s Job + L3b live + bridge (opt-in, merge не блокирует) |
| [`ci-images.yml`](../../.github/workflows/ci-images.yml) | push в `main` (paths api/flutter/packages/schemas/.gitmodules) / dispatch | GHCR `:latest` + SHA: `build-api` → `build-web` → `build-searchnet` (только dispatch с `build_searchnet=true`) / `trigger-verify` (`needs: [build-api, build-web]`) → dispatch Verify Dev |
| [`verify-dev.yml`](../../.github/workflows/verify-dev.yml) | `workflow_run` после **CI Images**, push в `main` c путями `infra/k3s/**`, `infra/argocd/**`, `infra/ops/**` | `rollout` → `wait` → `dns-check` → HTTP smoke |
| [`auto-merge.yml`](../../.github/workflows/auto-merge.yml) | `workflow_run` после **CI Gate** | squash-merge PR из этого же репо + dispatch CI Images |

Подробнее про e2e: [`e2e.md`](e2e.md).

Deploy is **Argo CD** from git. Runners: **systemd-сервисы на dev-VM** (`vm-*`, [`infra/github-runner-vm`](../../infra/github-runner-vm/README.md); Docker Desktop pack [`infra/github-runner`](../../infra/github-runner) — исторический).  
После ребута VM раннеры (`actions.runner.*`) поднимаются сами; kubeconfig `/home/runner/.kube/prodavan-dev.yaml` кладёт terraform — Windows-шаги (`Sync-KubeForDocker.ps1`, `Start-Runners.ps1`) не нужны (легаси Docker Desktop-эры). Ops: [`infra/ops`](../../infra/ops).

## Гигиена workflow'ов

Почти в каждом файле появились две вещи, которых раньше не было:

- **`permissions:`** — least privilege по умолчанию. `contents: read` во всех CI-прогонах
  (на self-hosted раннере токен доступен любому коду из PR, а писать в репозиторий
  этим workflow'ам нечего). Исключения: `ci-images` (`packages: write`, `actions: write`
  — push в GHCR и dispatch Verify Dev), `verify-dev` (`packages: read`), `auto-merge`
  (`contents/pull-requests/actions: write`).
- **`timeout-minutes`** — job-потолок вместо шестичасового дефолта: `ci-api test` 60,
  `postgres-integration`/`migration-suite` 45, `ci-gate infra` 15, `ci-gate gate` 5,
  `ci-web` 20, `ci-schemas` 10, `ci-flutter` 45, `ci-nightly` 45, `ci-images`
  `build-api` 60 / `build-searchnet` 90, `verify-dev` 60, `auto-merge` 10, `ci-e2e`
  от 5 до 25 по job'ам. Причина одна: раннеров пять на одну VM, и зависший шаг держал
  слот до ручного вмешательства.
- **`concurrency`** — новая запись вытесняет старую в `ci-gate`, `ci-api`, `ci-web`,
  `ci-schemas`, `ci-flutter`, `ci-e2e`, `ci-images`; в `ci-nightly`
  (`cancel-in-progress: false`) и `verify-dev` — наоборот, не отменять идущий прогон.

## Цепочка job'ов в `ci-api.yml`

Три job'а, запускаются **последовательно**, а не параллельно:

```text
test  (timeout 60)
  └─ postgres-integration  (needs: [test], timeout 45)
       └─ migration-suite  (needs: [test, postgres-integration], timeout 45)
```

`migration-suite` зависит и от `test`, и от `postgres-integration`, поэтому порядок
линейный: одновременного пика из двух pytest-прогонов не бывает.

**Почему последовательно.** Раннеры и dev-кластер живут на одной VM (15 ГиБ, из них
кластер занимает ~12 ГиБ). 2026-11-06 три pytest+Postgres сьюта на трёх разных
раннерах стартовали одновременно: все три умерли на 21-й минуте, и VM перестала
отвечать по SSH и ICMP разом. Комментарий в `ci-api.yml:199-204` фиксирует вывод:
wall clock вырос, но гейт, который убивает машину, на которой бежит, стоит дороже.
Отсюда же и эскалация таймаутов: у `postgres-integration` было 20 минут, из которых 19
уходило в pytest (каждый кейс создаёт и мигрирует реальные схемы) — джоб три прогона
подряд снимался таймером, а не падал на ассерте; теперь 45, как у `migration-suite`.

`test` в этой цепочке — единственный, кто гоняет `tests/unit/`; `migration-suite`
запускает `tests/migration` + `tests/api` (без `postgres_integration`).

## PostgreSQL integration suite (`postgres-integration`)

`tests/migration/postgres_integration` доказывает то, чего не докажет ни SQLite,
ни моки: `FOR UPDATE SKIP LOCKED`, aggregate CAS на живом движке, откат
транзакции, deferrable-цикл M05, partial unique и обратимость миграций. Без
`PRODAVAN_TEST_DATABASE_URL` каталог **скипается целиком**, поэтому до
MIG-CI-PG-01 сьют в CI не исполнялся — и найденный им дефект (lease store
возвращал «применено», ничего не записав) остался бы незамеченным.

Job **`postgres-integration`** живёт в [`ci-api.yml`](../../.github/workflows/ci-api.yml)
и вызывается из CI Gate через `api` (`workflow_call`), то есть на каждом PR. Что он
делает:

| Шаг | Смысл |
|-----|-------|
| service `postgres:16.15-alpine` | `ports: - "5432"` — контейнер слушает 5432, а хост-порт выдаётся **эфемерный** (все раннеры делят один docker daemon; фиксированный порт сталкивался бы с параллельным прогоном), healthcheck `pg_isready -U test -d prodavan_test` |
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

### Что остальные job'ы не ломают

Job'ы `test` (ci-api), nightly, e2e и `migration-suite` запускают свои наборы без этого
DSN — сьют там скипается, и это ожидаемо (`migration-suite` намеренно не выставляет
`PRODAVAN_TEST_DATABASE_URL`; его ассерт требует лишь `collected > 0`). Обязателен к
исполнению только `postgres-integration`; изменение схемы/путей сьюта должно обновлять
и его `pytest`-шаг.

`test` и `migration-suite` работают каждый со своим эфемерным Postgres
(`POSTGRES_USER=agentscale`, БД `agentscale`, роль приложения `agentscale_app`) и
именем контейнера `agentscale-ci-pg-<run_id>-<job>`, поэтому не делят состояние с
`postgres-integration` (`test`/`prodavan_test`).
