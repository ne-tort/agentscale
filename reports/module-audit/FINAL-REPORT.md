# Итоговый отчёт аудита plugin-платформы AgentScale

- **Дата исходного аудита:** 2026-11-10
- **Дата актуализации:** 2026-11-12
- **Исходная ветка:** `plugin-platform-checkpoint`
- **Известный baseline `main`:** `415adf14`

> **Текущий статус: STOP-SHIP.** Исходный аудит привёл к merge PR **#339–#359**
> (21 PR) в `main`, но успешный release после исправления provenance ещё не
> получен. Публикация, production/live acceptance и готовность к промоции в prod
> **не заявляются**. Действует verification freeze: новые проверки написаны, но
> намеренно не запущены. Prod не изменялся.

Документ сохраняет результаты исходного аудита как датированные исторические
доказательства и отдельно фиксирует текущее состояние поставки. Числа тестов и
описания падений ниже не выдаются за свежий прогон 2026-11-12.

## 1. Текущее состояние release (актуализация 2026-11-12)

### 1.1. Что уже подтверждено фактическими событиями

- PR **#339–#359** (21 PR) влиты в `main`; известный baseline —
  `415adf14`.
- Release run **43144913547** дошёл до **release verify** и остановился на
  **INVALID_EVIDENCE**. Фактический cosign predicate содержал
  `metadata.buildInvocationID`, тогда как verifier принимал только
  `metadata.buildInvocationId`.
- Локальный fix в контексте PR **#360** разрешает оба casing —
  `buildInvocationID` и `buildInvocationId` — и требует **непустое** run
  identity. Успешный run на `main` после этого fix ещё **не получен**.
- Более ранний Plugin CI dry-run **42898185702** на коммите `6f911fa7`
  (2026-11-11) завершился `success`: все 9 шагов dry-run, включая реальную
  сборку backend-образа на self-hosted docker runner. Это историческое
  доказательство dry-run, а не успешный release и не live acceptance.

### 1.2. Release state: успех признаётся только по фактическому run evidence

| Стадия | Фактическое evidence на момент актуализации | Текущий вывод |
|---|---|---|
| **Release verify** | Run **43144913547** дошёл до стадии и остановился на `INVALID_EVIDENCE` из-за несовпадения casing `buildInvocationID` / `buildInvocationId` | **Не пройден в этом run; успешный post-fix run отсутствует** |
| **Promote verified backend and bundle** | Для рассматриваемого release нет evidence успешного promote после verify; run остановился на verify | **Успех не заявлен** |
| **Reverify actual release refs and copied referrers** | Нет evidence успешного reverify после исправления provenance | **Успех не заявлен** |
| **Publication** | Нет evidence успешной публикации после исправления provenance | **Публикация не заявлена** |

Следовательно, merge PR и прошлые зелёные тесты не заменяют сквозной release
verify → promote → reverify → publication. До фактического успешного run эта
цепочка остаётся незакрытой.

### 1.3. Новые findings и локальные исправления (FIXED LOCALLY / UNVERIFIED)

Дополнительно к provenance fix статически выявлены и локально исправляются
следующие проблемы. **Ни одна из них не прошла формальную проверку и не
опубликована**; regressions и acceptance tests для них написаны, но во freeze
намеренно не запущены.

**FIXED LOCALLY / UNVERIFIED:**

- **Native backend SPDX-2.2 vs verifier SPDX-2.3.** Реальный buildx emits
  SPDX-2.2, verifier требовал SPDX-2.3. Локальный fix + regressions написаны,
  не запущены.
- **Cosign .sig/.att не копировались ORAS recursive.** После ORAS recursive
  copy release refs оставались без signature/attestation referrers. Локально
  добавлены два `cosign copy --only=sig,att` шага и tests; не запущены.
- **Migrations symlink escape.** Закрыт fail-closed staging-путём; tests
  написаны, не запущены.
- **Canonical builder `buildInvocationID`.** Builder теперь эмитит canonical
  run identity; это дополняет verifier-фикс обоих casing (PR #360): verifier
  принимает оба написания, но требует непустое значение. Не запущено.
- **CI discovery расширен** на network/archive_safety/promotion области.
  Новые проверки написаны, не запущены.

К ранее перечисленным доработкам в работе также относятся: build-time IPv4
precedence в Dockerfile, bounded downloads и bounded git operations, Ubuntu apt
через HTTPS. Они **ещё не прошли формальную проверку и не опубликованы**. Их
наличие в локальной работе нельзя считать release evidence. OIDC event/workflow
mode binding и migration regressions усиливаются локально и **не проверены**.

**OPEN (не закрыты, design/fix в работе, успех не заявлен):**

- **B1 — static_origin null против fail-closed CSP.** Старые docs описывали
  `static_origin: null`, что противоречит fail-closed CSP-модели. Требуется
  exact digest predeploy; текущий workflow его не доставляет.
- **B2 — approval exact digest без stable rerun/handoff.** Статический
  same-commit resume helper и rerun flow локально implemented / UNVERIFIED under
  freeze; успешный live rerun/publication не доказан.
- **B3 — consumed approval exact PUBLISHED replay.** Локальный backend fix
  implemented / UNVERIFIED under freeze; live replay не доказан.
- **B4 — release GHCR write предшествует scan/CSP.** Статический script path
  (pre-promotion evidence helper) locally implemented и workflow integration
  локально выполнена: evidence собирается отдельным шагом ДО `id: promote`,
  publish после release_verify переиспользует snapshot через
  `--release-evidence`; guard больше не намеренно RED. **UNVERIFIED under
  freeze:** успешный CI run и live publication не доказаны, но открытый
  blocker этапа снят и становится чистой UNVERIFIED позицией, а не RED.

**OPERATIONAL:**

- `gai.conf` на live VM host не применён из-за отсутствия доступа; сетевую
  стабильность объявлять нельзя (см. 1.4).

### 1.4. Repository variables, runners и сеть

- По актуализированным сведениям, установлены **10 repository variables**.
  Прежние утверждения «`gh variable list` пуст» и «задано 7 из 10» относятся
  к более ранним снимкам и не описывают текущее состояние. В рамках этого
  обновления документации команды `gh` не запускались.
- Раннее утверждение «0 из 5 self-hosted runners online» было снимком на
  2026-11-10, а не постоянным свойством инфраструктуры. Последующий успешный
  dry-run 42898185702 подтверждает, что docker runner выполнял реальную сборку.
  Текущее число online runners в этом отчёте не переутверждается без свежей
  проверки.
- IPv4 precedence добавлен в infra в рамках **#358**, Debian HTTPS — в **#359**.
  Однако `gai.conf` на live VM host **не применён из-за отсутствия доступа**.
  Поэтому сеть нельзя объявлять полностью стабильной: код/infra-изменения и
  фактическая конфигурация live host — разные уровни evidence.

## 2. Пользовательский сценарий: результат исходного аудита

Сырой профиль модуля — **не «npm-пакет»**: артефакт это один immutable OCI-манифест
с тремя основными слоями (`artifacts/layers.py`): **backend-образ** (linux/amd64),
**`frontend/dist`** (`application/vnd.agentscale.plugin.frontend`) и
**`migrations/liquibase/changelog.xml`**. То есть это произвольный
frontend+backend код, упакованный в OCI, а не JS-пакет: backend запускается как
контейнер, frontend живёт на static-origin под CSP, миграции идут через
Liquibase. Frontend обязан иметь непустой `csp` — без него артефакт не собирается.

| Шаг | Backend | UI | Итог исходного аудита |
|---|---|---|---|
| Создание репозитория модуля (`POST .../source`) | GitHub App: приватный repo из template, защита ветки | **Есть** | UI-форма `/admin/plugin-modules` |
| Привязка agent/instructions/template/CI | Из template-репозитория | **Нет** | Содержимое шаблона готовит админ |
| Scope-привязка source → cabinet/project | Есть | **Есть** | В той же форме |
| PublicationApproval | Есть | **Есть** | `PluginPublicationApprove` |
| Grant / SecretProjection / Deployment | Есть | **Есть** | `PluginLifecycle` |
| Update / revoke / re-grant / uninstall / wake | Есть | **Есть** (update — через новый grant) | `PluginLifecycle` |
| iframe, RPC, bootstrap | Есть | Есть, fail-closed | Подтверждено проверками исходного аудита |
| Прокси вебхуков + firewall | Есть | n/a | Серверный путь |
| Меню плагина | Есть (из `GET /admin/plugins`) | **Есть** | Без изменения схемы манифеста |

**Важно про «три экрана создания модуля».** Они собирают одно поле `name` и пишут
`ModuleRow` — это каталог модулей ПЛАТФОРМЫ, не plugin-модуль. Так и должно быть:
полный манифест из 11 обязательных полей не должен вводиться в форме вручную.
Plugin-модуль создаётся отдельной формой (`PluginModuleCreate`), которая собирает
минимально необходимое и отдаёт остальное серверу.

**Вывод исходного аудита:** сценарий «создать → согласовать публикацию →
установить → отозвать → удалить» проходим из UI на уровне реализации и выполненных
тогда проверок. Вне продукта остаются два шага: (1) подготовка
template-репозитория, из которого продукт генерирует приватный repo, и (2) включение
платформы — env-переменные, SealedSecret, подписанный bundle, human-решение.
Этот вывод не является подтверждением текущего production/live сценария.

## 3. Исторические исправления и доказательства исходного аудита

Дефекты ниже были найдены реальными запусками в ходе исходного аудита
(2026-11-10 и последующие доработки до merge #339–#359), а не только чтением.

| # | Дефект | Механизм отказа | Датированная проверка |
|---|---|---|---|
| 1 | `async with create_async_engine(...)` | `AsyncEngine` не имеет `__aenter__`/`aclose` — world-фикстура падала на любой ОС | БД создана, миграции до `2026110909` |
| 2 | Порядок seed: grant раньше cabinet | ORM не выводит порядок без relationship → `ForeignKeyViolationError` | Явный `flush()` |
| 3 | Early guard читал `option.confcutdir` до парсинга | Документированная команда отвергалась как bare invocation | Старый хук отклоняет, новый принимает |
| 4 | CI clone с токеном в `extraheader` | Публичный репозиторий, токен не экспортирован → падение до проверок | Анонимный clone + SHA-пиннинг |
| 5 | Сборщик отвергал `mcp_servers`/`rpc_operations` | Production-валидатор их принимает → сборщик был аутлаером | `validate_manifest` → OK |
| 6 | Регекс не принимал `python:3.13-slim@sha256:…` | Docker требует тег перед digest | Валидные формы принимаются, `:latest` заблокирован |
| 7 | Скрытый iframe продолжал исполняться | `hidden` не останавливает загруженный плагин | Все пути отказа → `srcdoc`-блок |
| 8 | Пароль в ConfigMap | `PLUGIN_TENANT_DB_ADMIN_URL` вне Secret | Рендер dev: переменная только в Secret |
| 9 | Диагностика in-memory портов всегда пуста | Ничто не наполняло список → проверки вхолостую | Обнаружение по классу порта |
| 10 | Manifest builder отвергал `FROM ${ARG}` | Dockerfile с базой из build-arg падал, хотя это штатная форма | ARG-дефолт разрешается, tag-only по-прежнему отвергается |
| 11 | Workflow не передавал `--build-arg` | Dockerfile объявляет `ARG` без дефолта ⇒ образ не собрался бы | Обе сборки получили оба аргумента из repo variables |
| 12 | `prepare` не принимал base image | Валидатор Dockerfile падал на `INVALID_LAYOUT` до сборки | Добавлен `--base-image-arg`; исторический local end-to-end `prepare → build → verify` |
| 13 | Workflow не передавал `GITHUB_REPOSITORY_ID` | `ReleaseTrust` падал на `GITHUB_SOURCE_CONTEXT_INVALID` до записи | Значение передано из контекста; helper проверен с переменной и без неё |

**Историческая локальная цепочка.** После исправлений был пройден путь без Docker
и реестра: `prepare` → `build` → `verify`. `prepare` отдал корректный layout,
`build` создал OCI-манифест с тремя основными слоями (`canonical-manifest`,
`frontend-dist`, `migrations`) и без самореференции `artifact_digest`,
`verify` последовательно отверг неверные входы: `DIGEST_CONFLICT` при чужой
паре repo, затем `MISSING_INPUT` на нетrusted builder identity и на
несовпадающей certificate identity. Дальше начинался cosign — реальный
подписанный сертификат локально не воспроизводился, и это оставалось границей
той проверки. Последующий release run 43144913547 выявил уже другое, реальное
несоответствие provenance, описанное в разделе 1.

**Историческая проверка hello-плагина.** `/health/live` отдавал 200;
`/health/ready` отдавал 503 `migrations_pending` без `LIQUIBASE_UPDATE_OK`
и 503 `db_unavailable` с ним (БД в пробе не было). Readiness зависел от флага,
который `entrypoint.sh` ставит после миграций, и не отдавал ложную готовность.
`/api/stats` существовал в backend, но не был объявлен в манифесте — broker
разрешал только объявленные операции и отвечал `OPERATION_NOT_ALLOWED`, что
покрывалось шестью тестами.

**Что было закрыто после первой редакции отчёта:**

- UI-путь создания: client-методы, тип `ModuleSourceResponse`, компонент
  `PluginModuleCreate`, секция в `ModuleAdminPage`, маршрут
  `/admin/plugin-modules` и собственный таб в админке.
- UI-путь установки: согласование публикации и жизненный цикл
  (grant/wake/revoke/uninstall) смонтированы на том же экране.
- Тестовые docstring'и: 44 файла утверждали, что тесты «НЕ ЗАПУСКАЛИСЬ».
  Формулировки исправлялись пофайлово; прогон перед той правкой подтвердил
  1358 passed в затронутых пакетах.
- Egress: добавлена pre-flight проверка политикой до запроса; запрет более не
  «помечает» уже ушедший запрос (счётчик транспорта = 0).
- Квота: отказ по одной размерности возвращает уже списанные единицы через
  `release` (`_CoreQuota` и обе реализации порта).
- Guard: `PluginSurfaceGuardMiddleware` ставится в обоих режимах ровно один раз.

## 4. Findings, которые независимо НЕ подтвердились

В исходном аудите следующие предварительные находки оказались ложными:

- **`TypeError` в `_zone_services`** — вызов корректен: 5 позиционных + kw-only.
- **`tenantdb/sql.py`, `artifacts/layers.py` «мёртвые»** — активно импортируются.
- **Import-graph guard не покрывает current plugin path** — содержит
  `CURRENT_PLUGIN_PATH`, проверяет достижимость от `main.tsx`, тест проходил.
- **`plugin_shell_origin` отсутствует в DTO** — присутствует в контракте и был
  покрыт 12 тестами; устарела только строчка документации.
- **Digest-конфликт не покрыт** — был покрыт четырьмя тестами в том же модуле.

## 5. Тестовые доказательства: история, не текущий прогон

### 5.1. Результаты исходного аудита (2026-11-10)

| Набор | Исторический результат |
|---|---|
| Backend plugin (unit + integration + acceptance) | **3922 passed** |
| Frontend | **897 passed** / 102 файла, `tsc --noEmit` чистый |
| Infra/ops | **122 passed** + `prodavan-ops validate` ok |
| Disposable PG / Redis / Kafka / K8s / MinIO | 27/27, 22/22, 16/16, 19/19, 29/29 |

Санитизированные исторические доказательства — [индекс](README.md) (38 файлов).

### 5.2. Более поздние результаты до verification freeze

| Набор | Доказательство | Ограничение |
|---|---|---|
| Полный plugin suite | **3975 passed** на `415adf14` | Предыдущий результат, не свежий запуск после текущих локальных изменений |
| Focused OIDC | **248 passed** | Предыдущее focused evidence, не release acceptance |
| Migration contracts | **3910 passed** | Предыдущий результат, не текущий freeze-прогон |

Новые тесты для текущих доработок написаны, но **намеренно не запущены** из-за
verification freeze. Никакие значения из этого раздела нельзя представлять как
свежий результат 2026-11-12 или как доказательство успешной публикации.

## 6. Как разрешаются противоречия старых редакций

Старые разделы отчёта описывали быстро менявшееся состояние CI. Ниже эти записи
сохранены как история и явно отделены от текущего статуса.

### 6.1. Runners и Docker

Первоначально предполагалось, что все CI workflow заблокированы отсутствием
self-hosted runners. Затем было установлено, что `plugin-artifacts.yml` тогда
объявлял `runs-on: ubuntu-latest` и реально выполнялся, но падал на
`failed to connect to the docker API at unix:///var/run/docker.sock`.
На `ubuntu-latest` в той конфигурации не было доступного Docker daemon, а
workflow по политике не использовал actions `setup-buildx`/`setup-docker`.
Остальные workflow (`ci-images`, `ci-e2e`, `verify-dev`, `ci-gate`)
объявляли `[self-hosted, linux, docker]`.

Оба job plugin workflow были переведены на `[self-hosted, linux, docker]`.
Запись «0 из 5 runners online» отражала ранний снимок 2026-11-10; успешный
dry-run 42898185702 от 2026-11-11 уже доказал выполнение реальной сборки на
self-hosted docker runner. Поэтому старое «runners ни при чём» и старое «все
заблокированы runners» по отдельности неполны: причины менялись по мере исправлений.

### 6.2. Старая workflow на main

До merge исходной ветки `main` содержал старую версию workflow без
`--base-image-arg`, без `PYTHON_BASE_IMAGE` и без `GITHUB_REPOSITORY_ID`.
GitHub брал dispatch-версию с default branch, а PR runners выполняли версию из
head PR. Это объясняло падения **до merge** и не должно применяться к baseline
`415adf14` после merge #339–#359 как будто старая workflow всё ещё текущая.

### 6.3. Пять исправлений, найденных реальными прогонами

Успешный dry-run 42898185702 стал результатом пяти последовательных исправлений:
`--build-arg` в обеих сборках, `GITHUB_REPOSITORY_ID`, инициализация Docker,
отказ от `unset` под `set -u` и переименование `DOCKER_CONTEXT` в
`BUILD_CONTEXT`. Последнее было корневым для ошибки `context not found`:
Docker CLI читал `DOCKER_CONTEXT` как имя собственного контекста, а не как путь
к исходникам.

### 6.4. Variables и supply-chain inputs

Историческая последовательность была такой:

1. На раннем снимке `gh variable list` был пуст, workflow не имел required
   variables и падал до сборки.
2. Затем были заданы **7 из 10** variables; оставались
   `PLUGIN_PLATFORM_PUBLICATION_CONFIG`,
   `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST` и `PLATFORM_VERSION`.
3. **Актуально на 2026-11-12: установлены 10 repository variables.** Старые
   «0» и «7/10» сохранены только как хронология, не как текущий блокер.

В ходе исходной работы `requirements.lock` был создан из реального индекса через
`uv pip compile` (`--python-version 3.13 --python-platform x86_64-manylinux_2_28
--generate-hashes`): 22 пакета, 658 хешей, покрытие допустимых wheel и sdist.
Тогда проверялось, что `--require-hashes --no-deps` разрешается полностью.
Digest базового образа был получен (`sha256:e48f59cd…`), SHA256
`liquibase-4.24.0.tar.gz` — `6ecf638a…` (два скачивания совпали). Официального
файла контрольной суммы у релиза не было: это воспроизводимость, а не подпись
вендора. Эти сведения остаются историческими supply-chain evidence, а не новой
проверкой в freeze.

## 7. Открытые блокеры и границы готовности

1. **Release не завершён.** Последний известный release run 43144913547 остановлен
   на verify с `INVALID_EVIDENCE`; успешного post-fix run нет. Promote,
   reverify и publication не подтверждены.
2. **Verification freeze.** Новые тесты и acceptance checks написаны, но не
   запущены. Текущие локальные доработки нельзя считать проверенными.
3. **Live network configuration не подтверждена.** Несмотря на #358/#359,
   `gai.conf` на VM host не применён; дополнительные build-time network fixes
   ещё не проверены и не опубликованы.
4. **Production configuration blockers сохраняются:** `PLUGIN_JOB_DECLARATIONS`,
   реальный SealedSecret, подписанный bundle, live STS и человеческое решение о
   включении.
5. **STOP-SHIP сохраняется.** Отсутствие свежего полного прогона и production/live
   acceptance не компенсируется прежними 3975/248/3910 passed или merge PR.
6. **B1 — static_origin null против fail-closed CSP.** Старые docs описывали
   `static_origin: null`, что противоречит fail-closed CSP. Требуется exact
   digest predeploy; текущий workflow его не доставляет.
7. **B2 — approval exact digest без stable rerun/handoff.** Статический
   same-commit resume helper и rerun flow локально implemented / UNVERIFIED under
   freeze; успешный live rerun/publication не доказан.
8. **B3 — consumed approval exact PUBLISHED replay.** Локальный backend fix
   implemented / UNVERIFIED under freeze; live replay не доказан.
9. **B4 — release GHCR write до scan/CSP.** Для direct registry consumers
   образ доступен до scan/CSP checks; platform `PUBLISHED` остаётся fail-closed.
   Статический script path локально implemented и workflow integration локально
   выполнена (evidence ДО `id: promote`, publish переиспользует snapshot);
   **UNVERIFIED under freeze**: успешный CI run не доказан, open-blocker фаза
   снята, это теперь чистая UNVERIFIED позиция.
10. **OIDC event/workflow mode binding и migration regressions** усиливаются
    локально, но не проверены и не опубликованы.

По продуктовой функциональности также остаются исходные ограничения:

- Меню плагинов реализовано через `pluginMenuItems` и `GET /admin/plugins`, без
  схемы v2. Автор по-прежнему не задаёт собственный заголовок/порядок пункта;
  если это потребуется, понадобится отдельное расширение контракта.
- Продукт создаёт приватный репозиторий модуля из верифицированного template
  через GitHub App, но содержимое шаблона и связку с агентом заранее готовит
  администратор. Продукт создаёт из шаблона, а не пишет сам шаблон.

## 8. Чего этот отчёт НЕ утверждает

- Не утверждается успешный release после исправления `buildInvocationID`.
- Не утверждаются promote, reverify или publication без фактического run evidence.
- Не утверждается, что сеть полностью стабильна или live VM уже настроена.
- Не утверждается, что локальные непроверенные доработки опубликованы.
- Не утверждается закрытие B1 (open), B2/B3 (UNVERIFIED), успешный CI run
  или готовность B4 к production использованию; локальная workflow integration
  evidence-first (evidence до promote, reuse snapshot) НЕ является live
  acceptance, а только unverified static implementation.
- Исторические disposable-прогоны и тесты **не являются** production/live acceptance.
- Ничто в документе не разрешает включение платформы, промоцию в prod или снятие
  STOP-SHIP.

`infra/k3s/overlays/prod` и prod-ресурсы не изменялись.
