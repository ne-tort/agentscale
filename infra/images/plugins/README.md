# Базовые образы инфраструктуры плагинов (задача 16)

Каталог `infra/images/plugins/**` — **ИНФРАСТРУКТУРНЫЕ** образы платформы для
tenant-контура плагинов (дизайн v4, §2.1 / §6.1 / §6.3 / §7.4 / §14 п.2, п.5,
п.6). Собираются и пушатся нашим workflow
[`.github/workflows/plugin-base-images.yml`](../../.github/workflows/plugin-base-images.yml).
В проде/k3s образы **НЕ тянутся напрямую с Docker Hub**: Docker Hub (и PGDG)
используются только как источник базы/пакетов **при сборке на self-hosted
раннере**, а нода тянет готовый ghcr-образ, запиненный по digest.

> Находка задачи 14: `ghcr.io/ne-tort/postgres:16.15` — зеркало ванильного
> образа, **`pgaudit` в нём нет**. В `tenant-postgres.yaml` явно задан
> `shared_preload_libraries=pgaudit`, поэтому без `plugin-base-postgres` сервер
> упадёт в `FATAL` на старте. Это намеренный fail-closed: аудит DDL/DML не
> отключается «тихо».

---

## 1. Таблица образов

| Образ (каталог) | База (ЗАПИНЕНА ПО DIGEST) | Что добавляем | Куда пушим | Теги |
|---|---|---|---|---|
| [`postgres-pgaudit/`](postgres-pgaudit/Dockerfile) | `postgres:16.15@sha256:f9a7b9eb…657` | `postgresql-16-pgaudit` **16.0** из PGDG (bookworm-pgdg), `01-bootstrap.sql`, эталоны `pg_hba.conf.template` / `pg_ident.conf` / `tenant_hba.conf`, `HEALTHCHECK` | `ghcr.io/ne-tort/plugin-base-postgres` | `:sha12`, `:latest` |
| [`pgbouncer/`](pgbouncer/Dockerfile) | `pgbouncer/pgbouncer:1.24.1@sha256:3d6fbf93…2ba` | `pgbouncer.ini` (transaction, scram-sha-256), пустой `auth_file` = deny all, `HEALTHCHECK` | `ghcr.io/ne-tort/plugin-base-pgbouncer` | `:sha12`, `:latest` |
| [`egress-proxy/`](egress-proxy/Dockerfile) | `envoyproxy/envoy:v1.31.10@sha256:caa5b411…865` | Стартовый `envoy.yaml` = `direct_response 403` + `clusters: []` (deny all), `HEALTHCHECK` | `ghcr.io/ne-tort/plugin-base-envoy` | `:sha12`, `:latest` |

**Все три образа — non-root в runtime** (`postgres:postgres`, `pgbouncer:pgbouncer`,
`envoy:envoy`), с явным `HEALTHCHECK` у postgres (п.1 задачи) и у остальных.
Имена выбраны намеренно **НОВЫМИ**: не затираем ghcr-зеркала
`postgres:16.15` / `pgbouncer:1.24.1` / `envoy:v1.31.2`, которыми пользуются
платформенный инстанс и манифесты задачи 14 до перепинивания.

### Версии и их обоснование

| Компонент | Версия | Почему |
|---|---|---|
| PostgreSQL | `16.15` | Та же версия, что и платформенный инстанс (`infra/k3s/base/plugins/tenant-postgres.yaml`, `overlays/dev/postgres.yaml`). Не `16-alpine`: у alpine другой dpkg и пути PGDG-пакета. |
| pgaudit | `16.0-N.pgdg120+M` | Релиз для ветки PG16. `ARG PGAUDIT_VERSION=16.0`, а `apt-get install` принимает **ТОЧНУЮ** строку из `apt-cache madison`; если PGDG отдал другую версию — сборка **ПАДАЕТ** (fail-closed), а не ставит «что нашлось». Точная строка попадает в SBOM. |
| PgBouncer | `1.24.1` | Официальный Docker Official Image (не форк), версия зафиксирована в `pgbouncer.yaml`. Сборка с поддержкой SCRAM (libpq) обязательна для `auth_type=scram-sha-256`. |
| Envoy | `1.31.10` | В ТЗ был `v1.31-latest` — **плавающий тег, запрещён**. Взята актуальная стабильная ветки v1.31: [upstream `VERSION.txt`](https://raw.githubusercontent.com/envoyproxy/envoy/release/v1.31/VERSION.txt) → `1.31.10`; тег и digest подтверждены Docker Hub API (last_pushed 2025-07-18, amd64+arm64). Задача 14 пока пинует `v1.31.2` (2024-09-19) — родитель перепинит после первой сборки. |

Digests баз проверены через Docker Hub API 2026-11-08 (multi-arch
manifest-list). `ARG *_BASE_IMAGE` позволяет подставить другую версию, но
**только вместе с `@sha256:<digest>`**.

---

## 2. Куда пушатся и как пинуются

Workflow `.github/workflows/plugin-base-images.yml`:

- trigger: `workflow_dispatch` + `push` по `infra/images/plugins/**` (и по
  самому workflow), `branches: [main]`;
- `permissions: contents: read, packages: write`;
- `runs-on: [self-hosted, linux, docker]`, тот же долгоживущий buildx-билдер
  `agentscale-ci`, что и в `ci-images.yml`;
- `matrix` ровно из 3 образов;
- `docker/build-push-action@v6` с **`provenance: true`** и **`sbom: true`**
  (§14 п.5), `docker/login-action@v3` в ghcr через
  `GHCR_PUSH_TOKEN || GITHUB_TOKEN`;
- теги `:sha12` + `:latest`;
- **digest печатается в `$GITHUB_STEP_SUMMARY`** вместе с готовой строкой
  `ghcr.io/ne-tort/plugin-base-<name>@sha256:<digest>`; при пустом/невалидном
  `outputs.digest` job **падает** (fail-closed).

`ci-images.yml` **не редактируется** (правило 1). `plugin-artifacts.yml`
тоже не тронут: он про OCI-артефакты плагинов (cosign keyless, только digest).

### Почему pin по digest в манифестах

Тег — **мутабельная** ссылка: тот же `:latest` (и даже `:1.24.1`) может быть
перезалит другим содержимым. Для границы безопасности плагинов это
недопустимо: подменившийся образ может потерять `pgaudit.so`, открыть
`trust`-аутентификацию или принести статический allowlist egress. `sha256:…`
— содержимое, а не имя: он неизменяем, проверяем через provenance/SBOM и
даёт воспроизводимый откат (предыдущий digest). Поэтому
`infra/k3s/base/plugins/*.yaml` должны использовать
`image: ghcr.io/ne-tort/plugin-base-postgres@sha256:<digest из summary>`,
а не `:latest` и не `:sha12`. Тег `:sha12` нужен для читаемости логов и
отката, `:latest` — только как удобный указатель для человека/раннера.

### Про `:latest` — что именно запрещено

| Что | `:latest` |
|---|---|
| **Артефакты плагинов** (OCI backend/frontend/migrations, §5.1, F4, `plugin-artifacts.yml`) | **ЗАПРЕЩЁН**. Только `@sha256:<digest>` + cosign verify + CompatibilityGate. Нарушение → `QUARANTINED`. |
| **Инфраструктурные базовые образы** (этот каталог) | **Разрешён по ТЗ** как движущийся указатель: workflow пушит `:sha12` + `:latest`. |
| **Манифесты k3s** (`infra/k3s/base/plugins/*.yaml`) | **Не использовать**: пин по `@sha256:<digest>`. Запрет `:latest` для артефактов плагинов и требование digest-пина для инфры — разные правила, не путать. |

---

## 3. Что где лежит (связь с задачами 05 / 08 / 14)

| Файл | Роль |
|---|---|
| `postgres-pgaudit/01-bootstrap.sql` | `COPY` в `/docker-entrypoint-initdb.d/`. Исполняется **один раз при пустом PGDATA**. Сначала `GRANT CONNECT` роли `plugin_platform_admin`, **ЗАТЕМ** `REVOKE CONNECT, TEMPORARY ... FROM PUBLIC` для `template1`, `postgres`, `agentscale`, `agentscale_userdb` и всех `tenant_*` (DO-блок по `pg_database`). Порядок критичен: иначе платформа потеряет доступ для `CREATE SCHEMA` (42501). **`prodavan` не трогаем** — это платформенный инстанс, REVOKE там делает bootstrap-Job задачи 14 (`platform-revoke.sql`). |
| `postgres-pgaudit/pg_hba.conf.template` | Эталон: `local` + `scram-sha-256`, пара `(plugin_platform_admin, all)`, `include tenant_hba.conf` для пар `(tenant_<company>, tenant_<company>_plugin_*)`, финальный `reject all`. Явно документировано: **`map=` несовместим со `scram-sha-256`**, пары задаются полями `DATABASE`/`USER`, `pg_ident.conf` — пустая заглушка. |
| `postgres-pgaudit/{pg_ident.conf,tenant_hba.conf}` | Пустые заглушки (fail-closed): ни одного разрешения до первого grant. Боевые файлы приходят из ConfigMap `tenant-postgres-config` задачи 14, пары генерирует задача 05 + `pg_reload_conf()`. |
| `pgbouncer/pgbouncer.ini` | Эталон и fail-closed дефолт. ОДНА запись `[databases] * = host=tenant-postgres port=5432` = **один пул на tenant-кластер**. `pool_mode=transaction`, `auth_type=scram-sha-256`, `auth_file` из Secret `pgbouncer-credentials`. В k3s перекрывается ConfigMap `pgbouncer-config`. |
| `egress-proxy/envoy.yaml` | Стартовый deny-all: `clusters: []`, `direct_response 403`, единая схема аудита `plugin_egress_request`. **Конфиг генерируется задачей 08 из `CapabilityGrant`**, НЕ статический `webhooks.example.com`. В k3s перекрывается ConfigMap `egress-proxy-config` (задача 08 + reload). |

`pg_hba.conf` / `pg_ident.conf` / `tenant_hba.conf` **НЕ подставляются из образа
в `/etc/postgresql`**: источник истины — ConfigMap задачи 14 (GitOps). Образ
лишь хранит эталоны и пустые заглушки; при отсутствии ConfigMap postgres не
стартует (fail-closed).

---

## 4. Unit-тесты / чек-лист первого реального прогона (НЕ ЗАПУСКАТЬ)

> По правилу 2 задачи и «Verification freeze» миграции: **тесты написаны, но НЕ
> запускались**. Ниже — приёмочный чек-лист для первого боевого прогона
> workflow и первого `Argo sync`. Каждый пункт — отдельная проверка с
> ожиданием; расхождение = fail, а не «пропустим».

### 4.1 `postgres-pgaudit`

- [ ] **pgaudit загрузился.** `SHOW shared_preload_libraries;` → содержит
      `pgaudit`; `SELECT * FROM pg_available_extensions WHERE name='pgaudit';`
      → `installed_version` не NULL; `test -f
      /usr/lib/postgresql/16/lib/pgaudit.so` и `HEALTHCHECK` → `healthy`.
      `dpkg-query -W postgresql-16-pgaudit` совпадает с версией из SBOM.
      Никакого `FATAL: could not access file "pgaudit"` в логах старта.
- [ ] **REVOKE PUBLIC применён.** Для `template1`, `postgres`, `agentscale`,
      `agentscale_userdb` и каждой `tenant_*`:
      `SELECT has_database_privilege('public', datname, 'CONNECT')` → **false**,
      то же для `TEMPORARY`. `SELECT datacl FROM pg_database` не содержит
      `=Tc/...` у PUBLIC. Новая БД, созданная после bootstrap (`CREATE
      DATABASE tenant_probe`), **наследует закрытый ACL** из `template1`.
- [ ] **Админская роль может CONNECT.** Живым подключением
      `psql -U plugin_platform_admin -d tenant_admin -c 'SELECT 1'` → успех;
      `has_database_privilege('plugin_platform_admin', ..., 'CONNECT')` → true
      на КАЖДОЙ целевой БД. `CREATE SCHEMA` от её имени не даёт 42501.
      Роль `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`.
- [ ] **pg_hba отклоняет чужую пару.** Роль плагина компании A с верным
      паролем, но `dbname=tenant_co_B` → отказ (`reject all` / `no pg_hba.conf
      entry`), НЕ «password authentication failed» и НЕ успех. Пара из
      `tenant_hba.conf` своей БД → успех; после удаления пары +
      `pg_reload_conf()` → отказ. `map=` нигде не встречается:
      `grep -R 'map=' /etc/postgresql` пусто, `pg_ident.conf` пуст, postgres
      стартует без `authentication option map is not valid`.
- [ ] **Порядок bootstrap соблюдён.** `GRANT` админской роли в
      `01-bootstrap.sql` физически выше `REVOKE ... FROM PUBLIC`; проверки
      шага 4 (`has_database_privilege`) не бросают `RAISE EXCEPTION` при
      initdb на чистом PGDATA.

### 4.2 `pgbouncer`

- [ ] **transaction mode.** `SHOW POOLS;` → `pool_mode = transaction` у всех
      пулов; `SHOW CONFIG;` → `pool_mode=transaction`,
      `auth_type=scram-sha-256`, `auth_file` указывает на смонтированный
      Secret, `server_reset_query=DISCARD ALL`,
      `server_reset_query_always=1`. `SHOW DATABASES;` → **ОДНА** запись `*`
      на `tenant-postgres:5432` (не N пулов на компанию).
- [ ] **Fail-closed auth.** С пустым `userlist.txt` (дефолт образа) любая
      попытка логина получает отказ; консоль `SHOW POOLS` без пароля из
      userlist недоступна. `auth_query`/`auth_user` в конфиге отсутствуют.
- [ ] **Non-root.** `ps -o user= -p 1` → `pgbouncer`; `:6432` слушается;
      `HEALTHCHECK` → `healthy`.

### 4.3 `egress-proxy` (Envoy)

- [ ] **Стартует с пустым allowlist = deny all.** `clusters: []` и ни одного
      upstream-хоста; `curl -x egress-proxy:8080 http://webhooks.example.com/`
      → **403** с телом `egress denied: no CapabilityGrant rule`, а не 200/503
      и не connection-refused. В stdout — событие `plugin_egress_request` с
      `status=403`, `decision=403`.
- [ ] **Конфиг не статический.** В `envoy.yaml` нет `webhooks.example.com`
      и любых хостов из JWT; после записи задачей 08 правил из `CapabilityGrant`
      и reload появляется ровно разрешённый `(host, path)`, остальное → 403.
      `admin /ready` → 200 (readinessProbe). Non-root: `ps -o user= -p 1` →
      `envoy`.
- [ ] **Пин по digest.** `kubectl get deploy … -o jsonpath='{.spec.template.spec.containers[0].image}'`
      содержит `@sha256:` и НЕ содержит `:latest`; digest совпадает с
      напечатанным в `$GITHUB_STEP_SUMMARY`. `provenance`/`sbom` у образа
      присутствуют (`docker buildx imagetools inspect`).

### 4.4 Workflow

- [ ] Три job матрицы зелёные; в summary у каждого — валидный
      `^sha256:[0-9a-f]{64}$` digest и строка pin-а. Пустой digest → job
      красный (проверить подменой на невалидный output в тестовом прогоне).
- [ ] `ci-images.yml` и `infra/k3s/**` этим воркфлоу **не изменились**.

---

## 5. Риски (не закрыты, требуют проверки на первом прогоне)

1. **Доступность PGDG с self-hosted раннера.** `apt.postgresql.org` и
   `postgresql.org` (ключ ACCC4CF8.asc) должны быть достижимы с CI-VM. В
   `ci-images.yml` уже есть прецедент: PyPI с этой VM отдаёт `ReadTimeout`,
   GitHub CDN — 403. Если PGDG недоступен, сборка честно **упадёт** (не
   «поставим из Debian main», где `postgresql-16-pgaudit` может отсутствовать).
   Митигация — заранее зазеркалировать `.deb` + ключ в ghcr/внутренний
   артефакторий и переключить `ARG PGDG_REPO_URL`; **не** ослаблять пин версии.
2. **Точная строка версии `postgresql-16-pgaudit`.** `16.0-N.pgdg120+M`
   зависит от ревизии сборки PGDG для bookworm. Пин по `ARG
   PGAUDIT_VERSION=16.0` намеренно жёсткий: при обновлении ревизии в PGDG
   сборка упадёт и потребует **осознанного** bump. Не заменять на
   `apt-get install postgresql-16-pgaudit` без версии.
3. **База `postgres:16.15` уже содержит репозиторий PGDG.** Dockerfile
   проверяет это и НЕ добавляет источник повторно (иначе apt упадёт с
   `Conflicting values set for option Signed-By`). Проверить на первом
   прогоне, что `apt-cache madison` видит нужную версию.
4. **Non-root + `fsGroup: 999`.** `USER postgres` в образе работает в k3s
   только потому, что `securityContext.fsGroup: 999` (задача 14) делает PVC
   доступным uid/gid 999. При запуске образа без этого `securityContext`
   (например, локально в docker) `initdb` упрётся в права на PGDATA. Это
   ожидаемо и документировано, но легко принять за «образ не собирается».
5. **`USER envoy` + официальный entrypoint.** Upstream `/docker-entrypoint.sh`
   дропает привилегии через `su-exec`, будучи root; с `USER envoy` он должен
   просто `exec` бинарь. Проверить, что конкретная ревизия
   `envoyproxy/envoy:v1.31.10` не требует root после дропа (порты 8080/9901
   непривилегированные). При расхождении — перейти на distroless-вариант
   `envoyproxy/envoy:distroless-v1.31.10` (тоже с digest-пином).
6. **`USER pgbouncer` + переопределённый ENTRYPOINT.** Мы явно ставим
   `ENTRYPOINT ["pgbouncer"]`, минуя обёртку официального образа; каталоги
   `/etc/pgbouncer/auth` и `/var/{log,run}/pgbouncer` заранее `chown`. Если
   в базе uid пользователя отличается от 70 — `id -u` всё равно пройдёт, но
   смонтированный Secret (0444) должен читаться этим uid. Проверить
   `SHOW POOLS` под k3s-монтированием.
7. **Envoy v1.31 — EOL-линия.** `1.31.10` — последний патч ветки; при
   следующем апгрейде платформы нужно осознанно перейти на актуальную
   поддерживаемую мажорную версию Envoy с новым digest-пином и новой версией
   (проверка `@type` фильтров). Не оставлять `v1.31-latest`.
8. **Расхождение имён с задачей 14.** Манифесты сейчас указывают
   `ghcr.io/ne-tort/postgres:16.15`, `.../pgbouncer:1.24.1`,
   `.../envoy:v1.31.2`. После первой сборки родитель **перепинивает** их на
   `plugin-base-*@sha256:<digest>` (зона задачи 14, нами не трогалась). До
   этого момента `tenant-postgres` по-прежнему упадёт в FATAL из-за pgaudit.

---

**Правило задачи:** docker / build / kubectl / тесты / линт / git в этой
сессии **НЕ запускались**. Только запись файлов в
`infra/images/plugins/**` и `.github/workflows/plugin-base-images.yml`.
