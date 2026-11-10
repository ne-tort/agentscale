# Выравнивание plugin-manifest и CompatibilityGate (аудит F11, блокер №5)

> **HISTORICAL REPORT / NOT CURRENT ACCEPTANCE.** Содержимое относится к ранней
> волне и не переподтверждено на audited HEAD `6ee720dd`. Любые «реализовано»,
> `[x]`, `acceptance ✓`, «эталон» и прошлые test counts — только historical
> claims. Актуальный статус: [STOP-SHIP](plugin-migration-status.md) и
> [live-only acceptance map](plugin-acceptance-map.md). Написанный код ≠ live.

**Решение: Вариант A.** CompatibilityGate НЕ читает запрещённые поля из
подписанного manifest. Закрытый whitelist `validate_manifest` (задача 01)
сохранён без ослаблений; доверие к сканеру и CSP перенесено туда, где оно и
должно быть — в CI и static-origin.

**Статус:** реализовано. Тесты написаны, но НЕ запускались; сборки / линт /
компиляция / git (status/diff/add/commit/push) НЕ выполнялись (verification
freeze миграции).

---

## Проблема

| Gate | Что требует | Есть в схеме? |
|---|---|---|
| `validate_manifest` (`domain/plugins/validation.py`, задача 01) | ровно 11 полей `MANIFEST_ALLOWED_FIELDS`, `additionalProperties: false`, запрет `artifact_digest` / `signature` / `egress` / `allowed_hosts` / `allowed_paths` | да |
| `CompatibilityGate.check` (задача 07, до правки) | `manifest["requestedCapabilities"]` — непустой массив строк | **нет** |
| `CompatibilityGate.check` (задача 07, до правки) | `manifest["frontend"]["csp"]` — непустая строка | **нет** |
| `CompatibilityGate.check` (задача 07, до правки) | отдельный аргумент `scan_verified=True` | да (внешний) |

Один и тот же dict физически не может пройти оба gate: добавление
`requestedCapabilities` / `frontend.csp` отвергается валидатором
(`UNKNOWN_FIELD` / `FRONTEND_INVALID`), а их отсутствие — compatibility gate.
Следствие: при реальной публикации **каждый** артефакт уходит в
`QUARANTINED`, весь контур OCI/cosign не работает.

---

## Таблица «поле → кто владеет → почему не в манифесте»

| Поле / сигнал | Кто владеет | Где живёт | Почему НЕ в подписанном `manifest.json` |
|---|---|---|---|
| `capabilities` | Автор плагина → `CapabilityGrant` в БД | `manifest["capabilities"]` (поле №6 из 11) | **В манифесте ЕСТЬ.** Единственный авторитетный источник запрошенных capability; gateway сверяет с `CapabilityGrant.capabilities`. |
| `requestedCapabilities` | CI (имя OCI-аннотации) | `annotations["agentscale.plugin.requestedCapabilities"]` в OCI-манифесте; `.github/scripts/plugin_manifest_builder.py::A_CAPABILITIES`; `.github/scripts/plugin_verify.py` | **Запрещено** `additionalProperties: false` и `MANIFEST_ALLOWED_FIELDS`. Это имя аннотации/проекции legacy `AppVersion`, а не поле контракта v1. Дубль `capabilities` создавал бы рассинхрон. |
| `frontend.entry`, `frontend.base_path` | Автор плагина | `manifest["frontend"]` (закрытый объект ровно из 2 полей) | **В манифесте ЕСТЬ.** Относительный путь статики и абсолютный base_path на отдельном static-origin; CSP здесь не объявляется. |
| CSP (`Content-Security-Policy`) | **Хост static-origin (задачи 13/14)** | HTTP-заголовок ответа static-origin; в CI — аннотация слоя `agentscale.plugin.frontend.csp` (ключ `csp` в аргументе `frontend_layer` функции `layers.py::build_oci_manifest`) | **Запрещено:** `frontend` в `schema.json` имеет `additionalProperties: false` со списком `entry`, `base_path`. CSP — свойство среды исполнения/хоста, а не подписываемого контракта плагина; его можно менять без переподписи артефакта. |
| `csp_verified` | **CI / static-origin (задачи 13/14)** | отдельный аргумент `CompatibilityGate.check(..., csp_verified=True)`, `ArtifactVerifier.verify`, `PluginArtifactRegistry.publish`; снимок в `VerificationEvidence.csp_verified` | Булево подтверждение «CSP-заголовок реально выставлен» не может быть полем подписываемого JSON: это результат наблюдения хоста, а не декларация автора. Fail-closed default `False` → отсутствие = отказ. |
| `scan_verified` | **CI-сканер** (`.github/workflows/plugin-artifacts.yml`, шаг сборки backend с `provenance: true` / `sbom: true`) | отдельный аргумент `CompatibilityGate.check(..., scan_verified=True)`; снимок в `VerificationEvidence.scan_verified`; аннотация слоя `agentscale.plugin.backend.scanned` | Результат сканирования образа известен только после сборки, поэтому не может быть в подписанном manifest (самореференция по смыслу). Fail-closed default `False`. |
| `artifact_digest`, `signature` | Платформа / cosign | `OciArtifactRef.digest`, annotations OCI **после** push, `VerificationEvidence.artifact_digest` | Явный запрет `SELF_REFERENCE_FIELDS` / `not.anyOf` в `schema.json`: нельзя подписать значение, появляющееся только после подписи (§13.1 v4). |
| `egress`, `allowed_hosts`, `allowed_paths`, `egress_policy` | `CapabilityGrant` / `EgressPolicy` в БД (задача 05) | `manifest["egress_ref"]` → `grant:...` | Явный запрет `EMBEDDED_EGRESS_FIELDS` / `not.anyOf`: правила egress не должны пересобираться при истечении JWT; встроенный allowlist — обход grant'а. |
| `min_platform_version`, `protocol`, `version` | Автор плагина | поля манифеста (в whitelist) | **В манифесте ЕСТЬ**; gate их проверяет как и раньше. |
| `plugin_id`, `backend`, `migrations`, `public_pages`, `webhooks` | Автор плагина | поля манифеста (в whitelist) | **В манифесте ЕСТЬ**; валидатор задачи 01 проверяет структуру. |

---

## Что изменено

| Файл | Изменение |
|---|---|
| `apps/api/src/prodavan/application/plugins/artifacts/verification.py` | `CompatibilityGate.check`: capability берутся из `manifest["capabilities"]`; требование `frontend.csp` заменено на проверку структуры `frontend` (ровно `entry` + `base_path`) + отдельный `csp_verified: bool = False` с fail-closed. Сохранены semver, protocol, `min_platform_version`, `capabilities ⊆ grant`, digest. В `ArtifactVerifier.verify` / `recheck_compatibility` / `VerificationEvidence` добавлен и протянут `csp_verified`. |
| `apps/api/src/prodavan/application/plugins/artifacts/registry.py` | `publish(..., csp_verified: bool \| None = None)` берёт флаг из evidence-снимка по умолчанию, явный `False` = отказ; повторный gate получает оба флага. Проекция `AppVersion` читает `capabilities`, а не `requestedCapabilities` (legacy-ключ в обёртке сохранён — того требует доменный тип). |
| `apps/api/src/prodavan/application/plugins/artifacts/layers.py` | `build_oci_manifest` читает capability из `manifest["capabilities"]`; имя OCI-аннотации `agentscale.plugin.requestedCapabilities` **не менялось** (контракт CI). CSP остаётся атрибутом frontend-слоя/аннотации, а не полем `plugin_manifest`. |
| `apps/api/src/prodavan/application/plugins/artifacts/__init__.py` | Не правлен: набор экспортируемых имён не изменился (сигнатуры — только внутри классов). |
| `apps/api/tests/unit/plugins/artifacts/conftest.py` | `valid_manifest()` теперь ровно 11 полей whitelist (реальная форма `hello-plugin/manifest.json`); добавлены `pilot_manifest()` и `real_validator()`. |
| `apps/api/tests/unit/plugins/artifacts/test_verification.py` | Валидный манифест пилота + оба флага → `APPLIED`/`ok=True`; отсутствие/не-bool `csp_verified` → `INCOMPATIBLE`; `requestedCapabilities`/`frontend.csp` не требуются; сохранены QUARANTINED при падении cosign, неизвестной capability, semver/protocol mismatch, digest conflict, SBOM/provenance. |
| `apps/api/tests/unit/plugins/artifacts/test_registry.py` | `_verified` передаёт оба флага; capability-override переименован; добавлены тесты снимка флагов и принудительного отказа при `csp_verified=False`. |
| `apps/api/tests/unit/plugins/artifacts/test_layers.py` | Проверено, что аннотация следует за `capabilities`, а запрещённый ключ не читается; отсутствие `capabilities` → отказ. |
| `apps/api/tests/unit/plugins/artifacts/README.md` | Обновлено описание покрытия и публичных аргументов. |

**НЕ тронуто (по условию задачи):** `domain/plugins/**`, `packages/schemas/**`,
`packages/plugins/**`, `apps/web/**`, `infra/**`, `.github/**`.

---

## Откуда приходят `scan_verified` / `csp_verified`

Оба флага — **внешние по отношению к подписанному JSON**. Источник истины —
CI-воркфлоу [`.github/workflows/plugin-artifacts.yml`](../../.github/workflows/plugin-artifacts.yml)
и хост static-origin:

1. **`scan_verified`** — шаг `Build backend image (provenance + SBOM, push)`
   (`docker/build-push-action@v6` с `provenance: true`, `sbom: true`) плюс
   `cosign sign` / `cosign verify` по digest. Результат сканирования образа
   известен только после сборки; в API он передаётся как отдельный аргумент
   `verify(..., scan_verified=True)` и фиксируется в
   `VerificationEvidence.scan_verified`. В `layers.py` тому же сигналу
   соответствует проверка `backend_layer["scanned"] is True` и аннотация
   `agentscale.plugin.backend.scanned`.

2. **`csp_verified`** — подтверждение, что static-origin (задачи 13/14) реально
   отдаёт `Content-Security-Policy` для `frontend.entry` на `base_path`.
   В CI это аннотация frontend-слоя `agentscale.plugin.frontend.csp`
   (`layers.py::build_oci_manifest`, `frontend_layer()["csp"]`), проверяемая
   хостом/политикой, а не поле `plugin_manifest`. В API — отдельный аргумент
   `verify(..., csp_verified=True)` и снимок `VerificationEvidence.csp_verified`.

3. `PluginArtifactRegistry.publish` НЕ доверяет «голому» флагу успеха:
   повторно прогоняет `recheck_compatibility` с обоими флагами из evidence и
   дополнительно требует `verified.scan_verified is True` /
   `verified.csp_verified is True`. Явный `False` в аргументах `publish` —
   принудительный карантин.

> Примечание: `.github/scripts/plugin_verify.py` содержит **свою автономную**
> реализацию `CompatibilityGate` для CI (не импортирует код приложения) и
> оперирует нормализованным `summary` из OCI-аннотаций, где ключ называется
> `capabilities`. Она не требует `frontend.csp` и потому уже консистентна с
> вариантом A; правка CI-скрипта в эту задачу не входила и не выполнялась.

---

## Почему не Вариант B

Вариант B (добавить `requestedCapabilities` и `frontend.csp` в схему) потребовал
бы синхронной правки `schema.json`, `types.ts`, `validation.py`, `types.py`,
CI-билдера, манифеста пилота и **всех тестов задачи 01** — 6+ файлов в чужих
зонах с высоким риском рассинхрона. Главное же: он раздувает подписанный
контракт данными, которые ему не принадлежат (результат сканирования и
CSP хоста), и ослабляет защиту от самореференции/встроенного egress, ради
которой закрытый whitelist и вводился. Вариант A устраняет конфликт в одной
зоне (`application/plugins/artifacts/**`) и сохраняет инвариант «подписанный
manifest без лишних полей».

---

## Риски и что нужно родителю

1. **Вызывающие стороны обязаны передавать `csp_verified=True`.** Fail-closed
   default `False` означает, что любой существующий вызов `verify` без нового
   флага честно уйдёт в `QUARANTINED`. Это ожидаемое поведение, но интегратору
   (задача 16 / адаптер `CosignPort` + вызовы из `wiring/platform.py`) нужно
   явно прокинуть флаг из CI/static-origin. В unit-тестах зоны это сделано.
2. **`build_oci_manifest` — тот же конфликт в смежной функции.** Она читала
   `requestedCapabilities` из подписанного dict; выровнена на `capabilities`.
   Имя OCI-аннотации намеренно НЕ менялось, чтобы не ломать
   `.github/scripts/plugin_verify.py` и `plugin-artifacts.yml`.
3. **Проекция `AppVersion`** по-прежнему кладёт capability в legacy-ключ
   `requestedCapabilities` обёртки — этого требует `AppVersion.validate_manifest`
   (`domain/dynamic_apps/types.py`). Источник значения теперь `capabilities`.
   Доменный тип не правился (вне зоны).
4. **CI-скрипты не правились.** Они работают с OCI-аннотациями, а не с
   подписанным plugin-manifest, и потому не требуют правки; при желании можно
   добавить явную передачу `csp_verified` в `plugin_verify.py` отдельной задачей.
5. `CompatibilityGate` по-прежнему не проверяет `plugin_id`, `egress_ref`,
   `public_pages`, `webhooks`, `migrations` — это зона `validate_manifest`
   (задача 01), дублировать нецелесообразно.
6. **Верификация не запускалась** (тесты pytest, сборки, линт, компиляция,
   `git status/diff/add/commit/push`) — действует verification freeze миграции.
   Тесты написаны и должны быть запущены родителем на финальном этапе.
