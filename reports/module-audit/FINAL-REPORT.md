# Итоговый отчёт аудита plugin-платформы AgentScale

**Дата:** 2026-11-10 · **Ветка:** `plugin-platform-checkpoint` · **PR:** [#339](https://github.com/ne-tort/agentscale/pull/339) → `main`

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
| Меню плагина | **Нет** | **Нет** | Требует схемы манифеста v2 |

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
| Backend plugin (unit + integration + acceptance) | **3920 passed** |
| Frontend | **897 passed** / 102 файла, `tsc --noEmit` чистый |
| Infra/ops | **122 passed** + `prodavan-ops validate` ok |
| Disposable PG / Redis / Kafka / K8s / MinIO | 27/27, 22/22, 16/16, 19/19, 29/29 |

Санитизированные доказательства — [индекс](README.md) (38 файлов).

## 6. Открытые блокеры

**Внешние (не дефекты кода):**

- 0 из 5 self-hosted раннеров online → `CI Gate` / `CI E2E` не запускаются.
  Последний успешный Gate — 2026-11-07.
- `Plugin OCI Artifacts` падает **до** сборки, потому что в репозитории вообще нет
  required переменных: `gh variable list` пуст, а workflow читает 7 значений
  (`PLUGIN_COSIGN_2_4_1_LINUX_AMD64_SHA256`, `PLUGIN_ORAS_1_2_3_LINUX_AMD64_SHA256`,
  `PLUGIN_BUILDKIT_IMAGE`, `PLUGIN_PLATFORM_PUBLICATION_CONFIG`,
  `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`, `PLUGIN_PLATFORM_REPOSITORY_ID`,
  `PLUGIN_GRANTED_CAPABILITIES`). Это environment-owned конфигурация, а не код.
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
  Repo-переменные: **3 из 7 заданы** мной из официальных источников —
  `PLUGIN_COSIGN_2_4_1_LINUX_AMD64_SHA256` (из `cosign_checksums.txt` релиза),
  `PLUGIN_ORAS_1_2_3_LINUX_AMD64_SHA256` (из `oras_1.2.3_checksums.txt`),
  `PLUGIN_GRANTED_CAPABILITIES` (ровно `capabilities` манифеста hello).
  Остальные 4 (`PLUGIN_BUILDKIT_IMAGE`, `PLUGIN_PLATFORM_PUBLICATION_CONFIG`,
  `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`, `PLUGIN_PLATFORM_REPOSITORY_ID`) я
  намеренно не задавал: они описывают protected publication environment, и
  выдуманные значения включили бы публикацию с фиктивными данными.
- `Plugin OCI Artifacts` корректно падает на незакреплённом базовом образе: нужны
  реальные `PYTHON_BASE_IMAGE`, `LIQUIBASE_SHA256` и полный `requirements.lock`.

**По существу (STOP-SHIP сохраняется):**

1. Меню плагинов не реализовано и требует решения владельца продукта, а не
   доработки. Схема манифеста v1 заморожена с `additionalProperties: false`
   и строгим списком из 11 обязательных полей; поля `menu` нет ни в ней, ни в
   `MANIFEST_ALLOWED_FIELDS`, ни в контрактах gateway/admin, ни в UI.
   Добавление поля задевает минимум 5 python-модулей, 12 TS-файлов и 24
   фикстуры манифеста, то есть это версионирование схемы v1→v2, а не правка.
   Модуль открывается по прямому адресу `/plugins/<id>/<company>`;
   `pluginpage_path` и `public_pages` — явные пути, а не пункты меню.
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
