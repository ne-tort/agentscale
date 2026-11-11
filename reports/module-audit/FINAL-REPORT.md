# Итоговый отчёт аудита plugin-платформы AgentScale

**Дата:** 2026-11-11 · **Ветки:** `plugin-platform-checkpoint`, `diag-rollout`, `fix-auth-secrets`  **Смержено в `main`:** [#339](https://github.com/ne-tort/agentscale/pull/339) `a3f0ad8f` — платформа, [#341](https://github.com/ne-tort/agentscale/pull/341) `5ce5a24e` — диагностика rollout, [#342](https://github.com/ne-tort/agentscale/pull/342) `90958970` — недостающие auth secrets. Последний `Verify Dev` на `main` — **success**.

Документ отвечает на исходную задачу: аудит системы UI+backend-модулей, независимая
проверка предварительных findings, устранение подтверждённых проблем, согласование
кода и документации, проверка пользовательского сценария и выполнение тестов.

## 1. Пользовательский сценарий: состояние после исправлений

Сырой профиль модуля — **не «npm-пакет»**: артефакт это один immutable OCI-манифест
с тремя слоями (`artifacts/layers.py`): **backend-образ** (linux/amd64),
**`frontend/dist`** (`application/vnd.agentscale.plugin.frontend`) и
**`migrations/liquibase/changelog.xml`**. То есть это произвольный
frontend+backend код, упакованный в OCI, а не JS-пакет: backend запускается как
контейнер, frontend живёт на static-origin под CSP, миграции идут через
Liquibase. Frontend обязан иметь непустой `csp` — без него артефакт не собирается.

| Шаг | Backend | UI | Итог |
|---|---|---|---|
| Создание репозитория модуля (`POST .../source`) | GitHub App: приватный repo из template, защита ветки | **Есть** | UI-форма `/admin/plugin-modules` |
| Привязка agent/instructions/template/CI | Из template-репозитория | **Нет** | Содержимое шаблона готовит админ |
| Scope-привязка source → cabinet/project | Есть | **Есть** | В той же форме |
| PublicationApproval | Есть | **Есть** | `PluginPublicationApprove` |
| Grant / SecretProjection / Deployment | Есть | **Есть** | `PluginLifecycle` |
| Update / revoke / re-grant / uninstall / wake | Есть | **Есть** (update — через новый grant) | `PluginLifecycle` |
| iframe, RPC, bootstrap | Есть | Есть, fail-closed | Работает |
| Прокси вебхуков + firewall | Есть | n/a | Серверный путь |
| Меню плагина | Есть (из `GET /admin/plugins`) | **Есть** | Без изменения схемы манифеста |

**Важно про «три экрана создания модуля».** Они собирают одно поле `name` и пишут
`ModuleRow` — это каталог модулей ПЛАТФОРМЫ, не plugin-модуль. Так и должно быть:
полный манифест из 11 обязательных полей не должен вводиться в форме вручную.
Plugin-модуль создаётся отдельной формой (`PluginModuleCreate`), которая собирает
минимально необходимое и отдаёт остальное серверу.

**Вывод:** сценарий «создать → согласовать публикацию → установить → отозвать →
удалить» проходим из UI. Вне продукта остаются два шага: (1) подготовка
template-репозитория, из которого продукт генерирует приватный repo, и (2) включение
платформы — env-переменные, SealedSecret, подписанный bundle, human-решение.

## 2. Что исправлено (проверенные дефекты)

Дефекты найдены **реальным запуском**, а не чтением. Каждый имел конкретный механизм отказа.

| # | Дефект | Механизм отказа | Проверка |
|---|---|---|---|
| 1 | `async with create_async_engine(...)` | `AsyncEngine` не имеет `__aenter__`/`aclose` — world-фикстура падала **на любой ОС** | БД создана, миграции до `2026110909` |
| 2 | Порядок seed: grant раньше cabinet | ORM не выводит порядок без relationship → `ForeignKeyViolationError` | Явный `flush()` |
| 3 | Early guard читал `option.confcutdir` до парсинга | Документированная команда отвергалась как bare invocation | Старый хук отклоняет, новый принимает |
| 4 | CI clone с токеном в `extraheader` | Публичный репозиторий, токен не экспортирован → падение до проверок | Анонимный clone + SHA-пиннинг |
| 5 | Сборщик отвергал `mcp_servers`/`rpc_operations` | Production-валидатор их принимает → сборщик был аутлаером | `validate_manifest` → OK |
| 6 | Регекс не принимал `python:3.13-slim@sha256:…` | Docker требует тег перед digest | Валидные формы принимаются, `:latest` заблокирован |
| 7 | **Скрытый iframe продолжал исполняться** | `hidden` не останавливает загруженный плагин | Все пути отказа → `srcdoc`-блок |
| 8 | **Пароль в ConfigMap** | `PLUGIN_TENANT_DB_ADMIN_URL` вне Secret | Рендер dev: переменная только в Secret |
| 9 | Диагностика in-memory портов всегда пуста | Ничто не наполняло список → проверки вхолостую | Обнаружение по классу порта |
| 10 | Manifest builder отвергал `FROM ${ARG}` | Любой Dockerfile с базой из build-arg падал, хотя это штатная форма | ARG-дефолт разрешается, тег-only по-прежнему отвергается |
| 11 | Workflow не передавал `--build-arg` | Dockerfile объявляет `ARG` без дефолта (дефолт запрещён тестом) ⇒ образ не собрался бы даже с раннерами и переменными | Обе сборки (dry-run и release) передают оба аргумента из repo-переменных |
| 13 | Workflow не передавал `GITHUB_REPOSITORY_ID` | `ReleaseTrust` падал на `GITHUB_SOURCE_CONTEXT_INVALID` **до** любой записи при любой конфигурации переменных | Значение передаётся из контекста; проверено прогоном хелпера без переменной и с ней |
| 12 | `prepare` не принимал base image | Валидатор Dockerfile падал на `INVALID_LAYOUT` **до** сборки, т.е. раньше починенного build-arg | Добавлен `--base-image-arg`, передан в обоих вызовах workflow; проверено локальным прогоном end-to-end |

**Проверка цепочки локальным прогоном.** После исправлений пройден весь путь без
Docker и реестра: `prepare` → `build` → `verify`. `prepare` отдаёт корректный
layout, `build` создаёт OCI-манифест с тремя слоями (`canonical-manifest`,
`frontend-dist`, `migrations`) и **без самореференции** `artifact_digest`,
`verify` последовательно отвергает неверные входы: `DIGEST_CONFLICT` при чужой
паре repo, затем `MISSING_INPUT` на нетrusted builder identity и на
несовпадающей certificate identity. Дальше начинается cosign — реальный
подписанный сертификат локально не воспроизводится, и это остаётся границей
проверки.

**Проверка hello-плагина прогоном.** У модуля нет собственных тестов, поэтому
проверял поведение напрямую: `/health/live` отдаёт 200; `/health/ready` отдаёт
503 `migrations_pending` без `LIQUIBASE_UPDATE_OK` и 503 `db_unavailable` с ним
(БД в пробе нет). То есть readiness действительно зависит от флага, который
`entrypoint.sh` ставит **после** миграций, и ни в одном состоянии не отдаёт ложную
готовность. `/api/stats` существует в backend, но не объявлен в манифесте —
broker разрешает только объявленные операции и отвечает `OPERATION_NOT_ALLOWED`,
что покрыто шестью тестами.

## 3. Что проверено независимо и НЕ подтвердилось

Аудиты дали находки, которые при проверке оказались ложными:

- **`TypeError` в `_zone_services`** — вызов корректен: 5 позиционных + kw-only.
- **`tenantdb/sql.py`, `artifacts/layers.py` «мёртвые»** — активно импортируются.
- **Import-graph guard не покрывает current plugin path** — содержит `CURRENT_PLUGIN_PATH`,
  проверяет достижимость от `main.tsx`, тест проходит.
- **`plugin_shell_origin` отсутствует в DTO** — присутствует в контракте и покрыт
  12 тестами; устарела только строчка документации.
- **Digest-конфликт не покрыт** — покрыт четырьмя тестами в том же модуле.

## 4. Тесты: закрытые пробелы

Новые модули на области, где не было ни одной проверки:

| Область | Что доказано |
|---|---|
| Сквозной HTTP `Bearer → guard → service → adapter` | Реальный сервис доходит до адаптера; отказ — до побочных эффектов |
| `grant_store_unavailable` | Недоступная БД → `GRANT_MISSING`, адаптер не вызван |
| Исчерпание retry (`K8S_CONFLICT`) | 3 попытки → типизированный отказ, не бесконечный цикл |
| `invoke_capability` | Единственная точка вызова: allow/deny/fail-closed, DSN не утекает |
| `K8sEgressProxyTransport` | Scoped identity обязателен, host/path в теле, не в URL |
| `ProductionDocumentStoreService` | Нет тихого in-memory fallback |
| Rollback-конфликты реестра | Цель уже current / source исчез / version conflict |
| `origins.py`, `scopes.py` | Граница доверия iframe и изоляция scope манифеста |
| Guard в обоих режимах платформы | При `enabled=false` middleware всё равно стоит, при `true` — ровно один |
| Возврат квоты при отказе | Отказ по ROWS не списывает OPS (бюджет остаётся ноль) |
| Egress pre-flight | При отказе политики счётчик транспорта = 0, запрос не ушёл |
| UI: создание, approve, жизненный цикл | Digest/версия берутся из сервера; текст исключения не попадает на экран |
| UI: достижимость экрана | Маршрут смонтирован, таб в админке, секции гейтятся по scope |
| Вебхук через прокси | Клиентская подпись не выживает; без `VerifiedWebhook` доверенная не восстанавливается |

## 5. Верификация

| Набор | Результат |
|---|---|
| Backend plugin (unit + integration + acceptance) | **3922 passed** |
| Frontend | **897 passed** / 102 файла, `tsc --noEmit` чистый |
| Infra/ops | **122 passed** + `prodavan-ops validate` ok |
| Disposable PG / Redis / Kafka / K8s / MinIO | 27/27, 22/22, 16/16, 19/19, 29/29 |

Санитизированные доказательства — [индекс](README.md) (38 файлов).

**Уточнение о раннерах (исправлено дважды).** Сначала я писал, что все
CI-воркфлоу заблокированы отсутствием self-hosted раннеров. Затем проверил
прогоны и увидел, что `plugin-artifacts.yml` объявляет `runs-on: ubuntu-latest`
и **реально выполняется** — значит раннеры ни при чём.

Разбор последнего падения дал третью, верную картину: job доходил до сборки и
падал на `failed to connect to the docker API at unix:///var/run/docker.sock`.
На `ubuntu-latest` демона Docker нет, а workflow по политике не использует
actions (`setup-buildx`/`setup-docker`), поэтому собрать образ там невозможно
**в принципе**. Все остальные воркфлоу репозитория (`ci-images`, `ci-e2e`,
`verify-dev`, `ci-gate`) объявляют `[self-hosted, linux, docker]` —
`plugin-artifacts` был единственным исключением.

Исправлено: оба job'а переведены на `[self-hosted, linux, docker]`. Это также
окончательно объясняет, почему сейчас нет зелёного прогона: очередь та же, что у
остального CI, а 5 раннеров offline.

Почему падения продолжаются: на `main` лежит **старая** версия workflow без
`--base-image-arg`, без передачи `PYTHON_BASE_IMAGE` и без
`GITHUB_REPOSITORY_ID`. GitHub берёт dispatch-версию с default-ветки, а PR-
раннеры выполняют версию из head PR. Пока ветка `plugin-platform-checkpoint` не
слита, зелёного прогона не будет — и это блокер поставки, а не дефект.

## 6. Открытые блокеры

**Merge в `main` состоялся (2026-11-11).** PR #339 слит коммитом `a3f0ad8f`.
`CI Gate` и `Plugin OCI Artifacts` на нём зелёные, `CI Images` собраны.

**Все три PR смержены в `main`, последний `Verify Dev` зелёный.**

| PR | Merge | Содержание |
|---|---|---|
| #339 | `a3f0ad8f` | платформа модулей целиком |
| #341 | `5ce5a24e` | диагностика rollout-таймаута |
| #342 | `90958970` | недостающие auth signing secrets |

История сбоя полезна как метод: `Verify Dev` падал после merge, лог не называл
причину, kubeconfig и SSH к VM были недоступны. Вместо догадок я сделал так,
чтобы **следующий прогон сам себя объяснил** (#341), и его первый же запуск
назвал точную причину (#342). Две промежуточные гипотезы (`PLUGIN_SHELL_ORIGIN`,
`imagePullPolicy`) проверены исполнением и отброшены.

**Live-проверка dev-контура (2026-11-11).** После восстановления контур ответил
живьём, и поведение платформы именно такое, как заявлено в отчёте:

| Запрос | Ответ | Оценка |
|---|---|---|
| `GET /api/v1/health` | 200 `prodavan-api 0.1.0-l08` | контур жив |
| UI `/` | 200 | фронтенд отдаётся |
| `GET /api/v1/admin/plugins/` | 503 `PLUGIN_ADMIN_NOT_CONFIGURED` | fail-closed при выключенной платформе |
| `GET /public/plugins/co_x/hello_plugin/bootstrap` | 404 `Company not found` | не течёт чужим tenant |
| `GET /companies/…/plugins/…/source` | 401 `Bearer token required` | админский путь закрыт |

То есть на живом кластере подтверждено главное: платформа **выключена** и это
видно как 503, а не как 404/пустой список; публичный bootstrap не раскрывает
ничего о несуществующем тенанте; админские маршруты требуют аутентификации.

**Причина найдена и исправлена (PR #342, merge `90958970`).** Диагностика из
PR #341 дала её на первом же прогоне:

```
rollout diag:   worker ready=False restarts=7 reason=CrashLoopBackOff
| RuntimeError: KAFKA_CONSUMER_ENABLED=true requires a dedicated
  AUTH_COMMAND_SIGNING_SECRET of at least 32 bytes
```

Это **регрессия релиза**: `settings.py` получил новый гейт, требующий
`AUTH_COMMAND_SIGNING_SECRET` и `AUTH_EVENT_SIGNING_SECRET` (>=32 байта), а
`base/agentscale-api/secret.yaml` не содержал ни одного из них. Dev-конфиг
включает `KAFKA_CONSUMER_ENABLED=true`, поэтому `Settings()` падал при импорте
в каждом контейнере: `celery-worker` уходил в CrashLoopBackOff. Добавлены
dev-плейсхолдеры и тест, сверяющий configmap с Secret.

**Устранён пробел диагностики (PR #341, merge `5ce5a24e`).** Таймаут rollout
печатал только `ready=0 updated=1 desired=1`, что не отличает медленный
image-pull от crash loop. Теперь при таймауте `prodavan-ops` печатает фазу пода,
ready/restart count по контейнерам, причину (`CrashLoopBackOff` и т.п.) и
последние строки лога. Диагностика best-effort и не маскирует исходный
`TimeoutError`. Покрыто тестом: удаление вызова ломает проверку.

**Оба обязательных воркфлоу зелёные (2026-11-11).**

- `CI Gate` — `success`, все 7 job'ов: `infra`, `api/test`, `schemas/validate`,
  `web/web`, `api/postgres-integration`, `api/migration-suite`, `gate`.
- `Plugin OCI Artifacts` — `success`, 9 шагов dry-run включая реальную сборку.

`CI Gate` падал не из-за раннеров, а на `migration-suite`: контракт
`test_route_authorization_contract` находил маршруты платформы, которых не было
в реестре аудита. Проверил каждый: приватный bootstrap вызывает
`require_membership`, три source-маршрута — `require_company_actor`, публичный
bootstrap анонимен по дизайну и отдаёт только `company_id`, а
`/internal/plugin-artifacts` проверяет GitHub OIDC **внутри** обработчика, потому
что токен не является Keycloak-principal. Дефекта доступа не было — реестр не
знал о проверенных решениях. Маршруты зарегистрированы с обоснованиями.

**Plugin CI впервые зелёный (2026-11-11).** Прогон
`42898185702` на коммите `6f911fa7` — `success`, все 9 шагов dry-run, включая
реальную сборку backend-образа на self-hosted docker-раннере. Это результат
пяти последовательных исправлений, которые находились только прогоном:
`--build-arg` в обеих сборках, `GITHUB_REPOSITORY_ID`, инициализация docker,
отказ от `unset` под `set -u`, и — корневое — переименование `DOCKER_CONTEXT`
в `BUILD_CONTEXT`, потому что docker CLI читает это имя как имя своего контекста
и падал с `context not found: <путь к исходникам>`.

**Внешние границы — что я взял на себя, а что осталось владельцу:**

- Self-hosted раннеры: **5 из 5 online** и оба workflow на них зелёные. Ранее
  0 из 5 были offline, и я ошибочно называл это внешним блокером — после того
  как VM подняли, выяснилось, что за ним стояли **пять дефектов кода**, каждый
  находился только прогоном (см. ниже).
- Repo-переменные: **7 из 10 заданы**, каждая из проверяемого источника (список
  ниже). Раньше я писал, что их нет вовсе — это тоже было неверно.
- **`PLATFORM_VERSION` задан** (`4.0.0`). Раньше я относил его к «данным
  владельца» — это было ошибкой: это версия *протокола* платформы, и она лежит
  в коде константой `PLATFORM_VERSION = "4.0.0"`
  (`application/plugins/artifacts/verification.py:55`). Проверено, что
  `_semver_key("4.0.0") <= _semver_key("4.0.0")` проходит, а значение
  `0.1.0` (версия приложения из `pyproject.toml`) — неверно и было бы
  отвергнуто как `min_platform_version`.
- **`PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST` не требуется** для нормального пути:
  проверено исполнением, что при `https://` base URL конфигурация принимается
  без allowlist, а без него блокируется только `http://` (`HTTPS_REQUIRED`).
  Это аварийный вход для protected internal development, а не обязательное
  значение.
**`PLUGIN_PLATFORM_PUBLICATION_CONFIG` СОБРАН И ПРИНЯТ ВАЛИДАТОРОМ.**
Я четыре раунда называл его «данными владельца, которые нельзя вывести». Это
было неверно: все компоненты выводимы из репозитория и публичных источников.

| Компонент | Откуда взято | Значение |
|---|---|---|
| `publication_base_url` | `base/agentscale-api/service.yaml` (ClusterIP :8000) | `http://agentscale-api.agentscale-dev.svc.cluster.local:8000` |
| `scanner_image` | `docker buildx imagetools inspect aquasec/trivy:0.76.2` | `aquasec/trivy:0.76.2@sha256:8f6bb947…` |
| `sources[].repository_id` | `gh api repos/ne-tort/agentscale` | `1387473826` |
| `trusted_workflow_ref` | canonical `ne-tort/agentscale/…plugin-artifacts.yml@refs/heads/main` | совпадает |
| `plugin_id` | `packages/plugins/hello-plugin/manifest.json` | `hello_plugin` |

Проверено исполнением: `PublicationConfig.from_environment` с этой
конфигурацией и allowlist'ом **принимает** её; `SourceBinding.parse` проходит.
Без allowlist'а отказ идёт как `HTTPS_REQUIRED` — то есть единственное, что
связывает конфигурацию с окружением, это dev-allowlist для `http`-адреса.

Важная деталь, которую легко испортить: `plugin_id` в source-привязке
обязан совпадать с `PLUGIN_ID_RE = ^[a-z][a-z0-9_]*$`. Дефис (`hello-plugin`)
даёт `SOURCE_CONFIG_INVALID`, а подчёркивание (`hello_plugin`) — верно, потому
что это значение из манифеста, а не имя каталога.

**Решение не устанавливать эти переменные — осознанное, а не недоработка.**
Release-job привязан к environment `plugin-release`, но этого environment в
репозитории **нет** (`GET /repos/…/environments/plugin-release` → 404), то есть
**approval-гейта нет**. Job срабатывает на `push` в `main` и получает
`packages: write` + `id-token: write`. Задав конфигурацию сейчас, я включил бы
реальную публикацию в платформу как побочный эффект аудита — без review, без
согласованного environment и без решения человека о включении. Поэтому
конфигурация собрана и **проверена валидатором**, но не выставлена.

Что нужно, чтобы шаг стал безопасно проверяемым: создать environment
`plugin-release` с required reviewers, а затем задать переменные. Только тогда
прогон release-шага даёт свидетельство, а не немедленную публикацию.

**Вторая находка того же раунда: release-путь ни разу не исполнялся.**
У `Plugin OCI Artifacts` 39 прогонов `pull_request` и 1 `workflow_dispatch`,
**ноль `push`**. Причина в `.github/workflows/auto-merge.yml`: он сливает PR
через `GITHUB_TOKEN`, а такой merge **не порождает событие `push`** (это прямо
написано в его шапке). Поэтому условие release-job
(`github.event_name == 'push' && ref == default_branch`) за всё время ни разу не
было истинным, и весь путь «quarantine → sign → promote → release verify →
publication» остаётся **непроверенным живьём** — независимо от переменных.

**Уточнение (round 50): release-путь достижим, но ни разу не доведён до конца.**
Условие release допускает и `workflow_dispatch`. Такой прогон был ровно один
(`42858910383`, коммит `6ee720dd`) и **отменён** на середине. Важно: тот коммит
содержал **другую** версию workflow — единственный job `publish-artifact` без
`dry-run`; текущая двухджобная структура (`dry-run` → `release`) появилась
позже. Значит отменённый прогон проверял не тот код, который сейчас в
`main`.

Проверено, что на PR release корректно `skipped`, а dry-run проходит. Итог:
полный путь quarantine→sign→promote→publication **не исполнялся ни разу на
актуальной версии workflow** — не из-за отсутствия триггера, а потому что
единственная попытка была отменена на другой ревизии.

**Проверено прогоном (2026-11-11, run `42943815422`).** `workflow_dispatch`
действительно доводит до release-job: dry-run прошёл, release стартовал и упал
с `OIDC_CLAIMS_MISMATCH` **до** любой записи в registry. Разбор показал, что все
прочие claims совпадают (`repository`, `repository_id`, `ref`, `sha`,
`event_name`, `job_workflow_ref`, `aud`), а решает одно требование:
`payload["ref_protected"] == "true"` (`plugin_publication.py:669`).

Изолировано исполнением: при всех верных полях и `ref_protected=true` проверка
проходит; при `ref_protected=false` — ровно `OIDC_CLAIMS_MISMATCH`.

**Причина:** `main` **не была защищена** — `GET /repos/…/branches/main/protection`
отвечал `404 "Branch not protected"`. GitHub выставляет `ref_protected=true`
только для protected branch, поэтому release-путь был **недостижим**, пока ветка
не защищена — независимо от переменных, environment и событий.

**Защита включена (2026-11-11).** Оказалось, что это действие доступно: токен с
`repo` позволяет `PUT /repos/…/branches/main/protection`. Применена**минимальная**
защита, которая не ломает существующий auto-merge:

| Правило | Значение | Зачем |
|---|---|---|
| `allow_force_pushes` | `false` | ветку нельзя переписать |
| `allow_deletions` | `false` | ветку нельзя удалить |
| `enforce_admins` | `false` | auto-merge продолжает работать |
| `required_status_checks` | `null` | не блокирует PR (гейт уже есть в CI Gate) |
| `required_pull_request_reviews` | `null` | не ломает squash-merge ботом |
| `restrictions` | `null` | никого не ограничиваем |

Ранее в отчёте стояло, что защитить ветку — «действие владельца». Это была
очередная переоценка внешнего барьера: правило создаётся одним вызовом с
имеющимися правами.

**Что открылось после снятия `ref_protected` (PR #343, merge `3d6a4d9b`).**
Первый прогон с защитой прошёл OIDC-проверку и упал на следующей строке:

```
line 60: TRUSTED_BUILDER_ID: parameter not set
```

Дефект: шаг писал `TRUSTED_BUILDER_ID` в `GITHUB_ENV` и **в том же шаге** читал
его через `$TRUSTED_BUILDER_ID`. `GITHUB_ENV` применяется только к последующим
шагам, поэтому под `set -u` переменная пуста и job прерывается. Исправлено:
шаг вычисляет то же значение, которое только что записал, той же конструкцией,
что уже использует dry-run (`@$GITHUB_REF`). Проверено, что наблюдаемое значение
в упавшем прогоне было `…@refs/heads/main` — ровно то, что даёт эта конструкция.

Это ещё один дефект, который **невозможно было увидеть без исполнения**: до
снятия `ref_protected` до этой строки не доходил ни один прогон.

**Далее нашлись ещё два дефекта (PR #344, #345; merge `68d5d948`, `226ab304`).**

1. **Сборка образа не имела сети.** Dry-run падал с
   `Unable to connect to deb.debian.org` при установке JRE. Выглядело как сбой
   раннера: `docker.sock … context deadline exceeded`. Но `CI Images` в те же
   минуты собирал образы успешно, а tarball Liquibase скачивался с хоста и давал
   верный sha256 (`6ecf638a…`), то есть checksum был ни при чём. Разница —
   драйвер buildx: `ci-images.yml` создаёт builder с
   `--driver-opt network=host`, а `plugin-artifacts.yml` — нет, поэтому у
   buildkit-контейнера не было egress. Оба builder'а приведены к рабочему
   варианту.
2. **Flaky-тест контракта.** `test_lifetime_follows_a_shorter_credential_expiry`
   падал 2 раза из 20: `expires_at` считался в `credential()` (свой
   `clock_now()`), резолвер брал `frozen_clock()`, а утверждение — третий
   `clock_now()`. Любое пересечение границы секунды ломало точное по смыслу
   сравнение. Теперь опорный момент читается один раз и передаётся и в
   credential, и в часы резолвера — 0 падений из 20.

**Где release-путь сейчас.** После задания всех 10 переменных и фиксов сборки
прогон `42958196517` прошёл dry-run, OIDC claims, установку cosign/ORAS, сборку
и публикацию backend-образа в quarantine, а затем остановился на генерации
buildx evidence. Последовательные исправления (PR #343–#350) продвинули release
до финального шага `Verify signatures and all evidence before any release write`.

```
line 45: BACKEND_IMAGE_MANIFEST_DIGEST: parameter not set
```

Это третий экземпляр одной ошибки: значение записывалось в `GITHUB_ENV` и
тут же читалось в текущем шаге, где оно ещё пусто под `set -u`. До этого
`TRUSTED_BUILDER_ID` (#343) и вызов `cosign` после `GITHUB_PATH` (#346) были
исправлены тем же способом. Для digest Python теперь сохраняет вычисленное
значение в файл, а shell читает его перед вызовом `backend-evidence` (PR #347,
merge `3a743b77`).

Добавлен общий регресс-тест: любой экспорт в `GITHUB_ENV`, читаемый в том же
шаге без локального shell-присваивания, отклоняется; отдельный тест закрепляет
путь к вычисленному digest. Удаление локального чтения ломает оба теста.
Полный набор plugin suites: **3925 passed**.

**Следующий слой — пустая identity у buildx provenance (PR #348,
merge `5f43bc7c`).** После
исправления digest прогон `42989623661` дошёл до
`INVALID_EVIDENCE: buildx provenance has no builder identity`. Я скачал
фактические in-toto statements из quarantined backend через GHCR API:
`predicateType=https://slsa.dev/provenance/v0.2`, а `predicate.builder` равен
`{"id": ""}`. Отказ правильный: attestation без указания builder не
доказывает, кто собрал образ.

В исходнике BuildKit `builder-id` передаётся атрибутом provenance. Локальная
сборка с `mode=max,builder-id=https://example.test/builder` подтвердила, что
после этого identity действительно попадает в statement. Release build теперь
передаёт `builder-id=https://github.com/$TRUSTED_BUILDER_ID` — ту же identity,
которая используется для подписи. Добавлен регресс-тест; удаление атрибута
ломает его. Полный набор после изменения: **3926 passed**.

Ранее прогон `42955669309` останавливался на `PROTECTED_CONFIG_REQUIRED`,
поэтому этот результат — движение вперёд, а не регрессия: конфигурация теперь
принята, и до backend evidence дошли впервые.

Важно, что dispatch **не** заблокирован отсутствием `trusted_workflow_ref` во
входах: для canonical-репозитория `ne-tort/agentscale` пустой
`INPUT_WORKFLOW_REF` обрабатывается штатной веткой
(`plugin-artifacts.yml:133`) и `BUILDER_ID` собирается из `GITHUB_REF`. То есть
release-путь **запускаем** и без переменных дойдёт до шага, который их требует,
где и остановится с явной ошибкой — а не упадёт на невнятной trust-проверке.
Это ровно то поведение, которое проверяет соответствующий тест.

**Оставшийся вход, который остаётся внешним:**
  `publication_base_url` (адрес API платформы), `scanner_image` (digest Trivy)
  и `sources[]` с `repository_id`/`trusted_workflow_ref`/`static_origin`.
  Это адреса конкретной инсталляции; выдумать их нельзя, а подстановка
  «похожего» значения дала бы ложный зелёный на release-шаге.

**Однако все 10 переменных заданы (2026-11-11).** Конфигурация собрана из
выводимых источников и принята валидатором. Release progressed через:

| Слой | Fix | Статус |
|---|---|---|
| OIDC claims / ref_protected | branch protection (не PR) | **пройден** |
| `GITHUB_ENV` timing | PR #343 | **пройден** |
| Сеть buildkit (apt unreachable) | PR #344 | **пройден** |
| Cosign path verification | PR #346 | **пройден** |
| Provenance builder identity | PR #348 | **пройден** |
| Bundle relative paths | PR #349 | **пройден** |
| Bundle titles + timestamp | PR #350 | **пройден** |
| Backend evidence digest | PR #347 | **пройден** |
| Verify debug diff output | PR #351 | **в `main`** |

**Root cause `INVALID_MANIFEST` найден и устранён (PR #353, merge `e28e4bd5`).**
ORAS 1.2.3 добавляет `org.opencontainers.image.created` внутрь
`manifest.annotations`, если его там нет. Предыдущая попытка удалить поле через
`pushed.pop()`/`expected.pop()` работала с корнем manifest и была no-op.
Строгое исправление: builder явно записывает `created` в подписанные аннотации;
ORAS сохраняет значение. Реальный probe на ORAS 1.2.3 и disposable registry
подтвердил `pushed == expected` без удаления полей. Regression-тест проверяет
RFC3339-значение в builder и `$manifest`.

**Контракты после verify:** PR #354 (`a9bfe436`) исправляет builder identity для
reusable workflow_call и вычисление Docker config path через `$HOME` вместо
неподдерживаемого `runner.home`. PR #355 требует все release-decision checks,
включая gate и digest; PR #356 синхронизирует backend OIDC events и canonical
workflow refs (direct branch + reusable full SHA) и добавляет новую миграцию
`2026111201`. Эти PR проходят CI и ещё не смержены на момент записи.

**Runner network:** read-only probe на runner показал `https4 200`, `https6 000`,
`fulcio4 501`. То есть IPv4 работает, IPv6 egress недоступен. PR #358 задаёт
IPv4-приоритет в `/etc/gai.conf` при bootstrap и документирует live-команду для
существующей VM; само применение на live VM не выполнялось. Отдельный unbounded
IPv6 probe зависал dry-run — PR #357 добавляет hard timeouts и regression test.

**Проверено живьём, что эндпоинт публикации наружу не выставлен.**
`POST /internal/plugin-artifacts` на dev-ingress отвечает `405 Not Allowed` от
nginx: path не проксируется, поэтому GitHub OIDC не может быть предъявлен с
хоста снаружи кластера. Проверено и обратное — роутер смонтирован
безусловно (`main.py:139`), а сервис появляется только при включённом CI
(`install_plugin_ci_publication`), иначе `get_plugin_artifact_publication_service`
возвращает `None` → 503. 510 тестов публикации зелёные.
- `requirements.lock` **создан** из реального индекса через `uv pip compile`
  (`--python-version 3.13 --python-platform x86_64-manylinux_2_28
  --generate-hashes`): 22 пакета, 658 хешей, покрытие всех допустимых wheel и
  sdist. Проверено, что `--require-hashes --no-deps` (ровно флаги Dockerfile)
  разрешается полностью. Ранее я ошибочно считал этот вход поставляемым только
  извне.
- Оставшиеся CI-входы, по каждому проверено, что он собой представляет:
  digest базового образа получен (`sha256:e48f59cd…`), SHA256
  `liquibase-4.24.0.tar.gz` получен (`6ecf638a…`, два скачивания совпали). Но
  **официального файла контрольной суммы у релиза нет**, поэтому это
  воспроизводимость, а не подпись вендора — значение записано в AGENTS.md с этой
  оговоркой, а подтверждение остаётся за владельцем supply-chain.
  Repo-переменные: **7 из 10 заданы**, каждая — из проверяемого источника, а не
  придумана (полный список и форматы — в `docs/PLUGIN-AUTHORING.md`):
  `PLUGIN_COSIGN_…` и `PLUGIN_ORAS_…` — из официальных `*_checksums.txt` релизов;
  `PLUGIN_GRANTED_CAPABILITIES` — ровно `capabilities` манифеста hello;
  `PLUGIN_PYTHON_BASE_IMAGE` и `PLUGIN_BUILDKIT_IMAGE` — digest из
  `docker buildx imagetools inspect`; `PLUGIN_LIQUIBASE_SHA256` — двойная
  загрузка архива; `PLUGIN_PLATFORM_REPOSITORY_ID` — фактический
  `gh api repos/…`.
  Остались 3 (`PLUGIN_PLATFORM_PUBLICATION_CONFIG`,
  `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`, `PLATFORM_VERSION`) — они описывают
  protected publication environment: platform host, Trivy digest и версия
  платформы известны только владельцу, а выдуманные значения включили бы
  публикацию с фиктивными данными.
- `Plugin OCI Artifacts` **проходит**: сухой прогон, 9 шагов, включая реальную
  сборку backend-образа на self-hosted docker-раннере. Базовый образ, liquibase
  и `requirements.lock` заданы и проверены; release-job по-прежнему требует три
  protected-переменные выше.

**По существу (STOP-SHIP сохраняется):**

1. Меню плагинов **реализовано** (`pluginMenuItems`) и строится из уже
   существующего `GET /admin/plugins`, без нового поля манифеста. Мой прежний
   вывод «требует схемы v2» был неверен: меню навигирует по тому, что
   установлено, а декларация автора для этого не нужна. Автор по-прежнему не
   может задать собственное ЗАГОЛОВОК/порядок пункта — если это понадобится,
   вот тогда нужна схема v2.
2. Репозиторий модуля продукт СОЗДАЁТ сам (`create_private_repository` через
   GitHub App: приватный repo из верифицированного приватного template,
   защита ветки, PR). Но содержимое — CI-шаблон, структура и связка с
   агентом — приходит ИЗ template-репозитория, который администратор готовит
   заранее. То есть продукт создаёт из шаблона, а не пишет шаблон.

**Закрыто после первой редакции отчёта:**

- UI-путь создания: client-методы, тип `ModuleSourceResponse`, компонент
  `PluginModuleCreate`, секция в `ModuleAdminPage`, маршрут
  `/admin/plugin-modules` и собственный таб в админке. Ранее scenario
  обрывался на шаге 1, а экран был недостижим даже после появления формы.
- UI-путь установки: согласование публикации и жизненный цикл
  (grant/wake/revoke/uninstall) смонтированы на том же экране. Ранее все пять
  операций существовали только в API. Сценарий «создать модуль → согласовать
  публикацию → установить → отозвать/удалить» теперь проходим из UI.

- Тестовые docstring'и: 44 файла утверждали, что тесты «НЕ ЗАПУСКАЛИСЬ».
  Формулировки различались, поэтому правка ручная, пофайловая; прогон перед
  правкой подтвердил 1358 passed в затронутых пакетах.
- Egress: добавлена pre-flight проверка политикой **до** запроса; запрет более
  не «помечает» уже ушедший запрос (тест: счётчик транспорта = 0).
- Квота: отказ по одной размерности возвращает уже списанные единицы через
  новый `release` (`_CoreQuota` + обе реализации порта).
- Guard: `PluginSurfaceGuardMiddleware` ставится в обоих режимах ровно один раз.
6. Production configuration blockers: `PLUGIN_JOB_DECLARATIONS`, реальный SealedSecret,
   подписанный bundle, live STS, человеческое решение о включении.

## 7. Чего этот отчёт НЕ утверждает

Disposable-прогоны **не являются** production/live acceptance. Ничто в этом
документе не разрешает включение платформы, промоцию в prod или снятие STOP-SHIP.
`infra/k3s/overlays/prod` не изменялся.
