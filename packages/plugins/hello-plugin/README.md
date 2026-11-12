# hello_plugin — демонстрационный каркас, не эталон работающего runtime

> **Статус: DEMO / STOP-SHIP.** Этот пакет показывает возможную структуру исходника
> plugin v4, но **не является подтверждённым эталоном end-to-end**. Plugin
> platform сейчас emergency disabled, а независимый аудит HEAD `6ee720dd` не
> подтвердил ни один сквозной plugin-сценарий.
>
> Не использовать README как quickstart для install/deploy. Текущее состояние —
> [STOP-SHIP](../../../docs/migration/plugin-migration-status.md), live acceptance —
> [все пункты открыты](../../../docs/migration/plugin-acceptance-map.md), authoring
> matrix — [PLUGIN-AUTHORING](../../../docs/PLUGIN-AUTHORING.md).

## Что действительно можно изучать

| Файл | Назначение | Статус |
|---|---|---|
| `manifest.json` | Пример manifest с 13 top-level полями, включая typed `rpc_operations` и `mcp_servers` | Statically declared; не proof publication или live integration |
| `backend/app.py` | FastAPI-пример health, scoped greetings и webhook | Демонстрационный код, не подтверждённая интеграция |
| `backend/liquibase_url.py`, `migrations/changelog.xml` | Иллюстрация plugin-owned Liquibase migrations | Единственный практически описанный migration engine; live apply не подтверждён |
| `backend/Dockerfile`, `requirements.txt` | Пример упаковки backend | Не evidence OCI signature/provenance/deploy |
| `frontend/index.html`, `app.js` | Пример static frontend/RPC-клиента | Не подтверждённые hosted static-origin и browser flow |
| `backend/entrypoint.sh` | Контейнерный пример `liquibase update` → application start | Не хостовый infra script и не live readiness proof |

Пакет **не** переносит `mod_equipment`, не заменяет текущий S1 declarative-app и
не показывает, что все девять capability adapters работают.

| Статус | Что входит |
|---|---|
| **Supported (только локальный authoring/static inspection)** | Чтение исходников, подготовка 13-field hello manifest по canonical schema, изучение Liquibase/static/backend-примеров и написание local contract tests |
| **Statically implemented / live unverified** | Две typed `rpc_operations` + MCP declarations, durable MCP registration/broker/JSON-RPC, scoped jobs, runtime identity/fresh-wake contracts, strict eight-field health/introspection и webhook forwarding |
| **Target** | Trusted digest-pair publishing, tenant DB/migrations до Ready, static sandbox, deploy/update/rollback saga и сквозная acceptance-проверка всех statically implemented контрактов |
| **Not implemented / unverified** | Live install, рабочий hello vertical slice, end-to-end typed RPC, Gateway→hello webhook, все 9 adapters, GitHub App и воспроизводимый image build до reviewed inputs |

## Известные интеграционные разрывы

### 1. Нет подтверждённого vertical slice

Наличие manifest, backend, frontend, Dockerfile, миграций и tests не доказывает
цепочку publish → grant → tenant DB → migration/Ready → identity → static/RPC →
webhook → update/rollback. Все такие проверки остаются в acceptance map `[ ]`.

### 2. Воспроизводимая сборка намеренно fail-closed

Текущий Dockerfile нельзя считать готовым воспроизводимым build path. Для
разблокировки требуются три независимо проверенных входа:

1. полный multi-arch digest базового `python:3.13-slim@sha256:<64 hex>` или
   content-identical mirror `ghcr.io/ne-tort/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90`;
2. подтверждённый SHA256 официального `liquibase-4.24.0.tar.gz`;
3. полный reviewed `backend/requirements.lock` со всеми direct/transitive
   версиями, markers и hashes для pip `--require-hashes --no-deps`.

Lock намеренно отсутствует; частичный lock соседнего проекта не является его
заменой. Нельзя выдумывать digests/hashes или объявлять image собранным до
отдельного supply-chain review и build verification.

### 3. Typed RPC и broker: statically implemented, live unverified

Текущий hello manifest содержит `rpc_operations` с ровно двумя declarations
canonical capability `hello.greetings@1.0`:

| Operation | Method | Backend path | Public |
|---|---|---|---|
| `list` | `GET` | `/api/greetings/list` | `false` |
| `greet` | `POST` | `/api/greetings` | `false` |

Ключ `(capability_id, capability_version, operation)` и каждый `backend_path`
уникальны. `/api/stats` существует в backend, но не объявлен в `rpc_operations`
и поэтому не разрешён broker'ом. Manifest-scoped broker statically implemented:
он разрешает только operations из подписанного manifest. Прежнее описание
несогласованного поля `rpc_operations` устарело для текущего worktree.

Это статическое состояние кода и контрактов, **не evidence** готового
browser → Gateway → backend end-to-end сценария или пройденного live acceptance.

Дополнительно demo не доказывает все bounded payload/resource limits: для новых
endpoints нужны собственные byte/depth/page/concurrency limits и negative tests,
а не доверие только внешнему proxy.

### 3a. Jobs: exact selectors и scoped worker, live unverified

`PLUGIN_JOB_DECLARATIONS` не является module/client setting для произвольного
запуска tasks. Server принимает только exact
`{plugin_id, version, artifact_digest}` selectors и строит allowlist исключительно
из exact verified publications и их signed private `rpc_operations`. Concrete
Celery task name, backend digest/path и handler назначает platform, не caller.

Scoped `ManifestJobTasks` повторно проверяет active grant, subject/company/
plugin/instance/artifact/backend digest и manifest declaration до side effect и
перед result commit. Execution использует canonical backend resolver, fresh
activator wake, job lease и scoped proxy. API связывает install-created registry
через typed `job_worker_registration` hook; standalone Celery создаёт ресурсы
только через `DeferredStandalonePluginJobComposition` и закрывает их отдельным
owner'ом. Наличие selectors/API hook/factory в коде не является evidence
выполненной job или успешного worker startup.

### 3b. MCP: durable discovery → `mcp.invoke` → canonical backend RPC

Hello дополнительно объявляет `mcp_servers` с `server_id: hello.greetings` и
tools `list`/`greet` версии `1.0.0`. Их object `input_schema` ограничены и
соответствуют canonical `hello.greetings@1.0` operations, но manifest declaration
сам по себе не регистрирует tool и не открывает grant.

Static path состоит из:

1. durable Redis registration в exact company/plugin/instance/artifact-digest
   scope (`SET NX` + re-read; replay только с идентичными definitions);
2. discovery только пересечения signed `mcp_servers` и durable registrations с
   совпадающими tool version/schema digest;
3. `PluginCapabilityService` operation `mcp.invoke` с обычными grant/auth/quota/
   audit;
4. `DurableMcpBroker`, который выбирает ровно одну canonical `rpc_operations`
   declaration по server/tool и вызывает её backend path через scoped proxy.
   Это **не infra remap** MCP tool в cache/objects/docs/другую capability.

Authenticated JSON-RPC mount `POST /capabilities/v1/mcp/servers/{server_id}`
поддерживает `initialize`, `notifications/initialized`, `tools/list`,
`tools/call`. Real registration/discovery/invoke/client flow, revoke/rollout,
quota/audit и redaction остаются **live unverified**.

### 4. Per-request identity и ровно четыре managed keys

Статического capability token в pod env нет. Backend читает ровно четыре runtime
значения из platform-managed per-instance Secret через `secretKeyRef`:

- `GATEWAY_URL`;
- `PLUGIN_ID`;
- `TENANT_DB_DSN`;
- `WEBHOOK_SECRET`.

Имя webhook-переменной — именно `WEBHOOK_SECRET`, не `PLUGIN_WEBHOOK_SECRET`.
Доверенный identity input — короткоживущий `X-Plugin-Capability-Token` текущего
запроса. Backend statically вызывает protected
`GET {GATEWAY_URL}/capabilities/v1/health` на каждый API request и принимает
только JSON object ровно с восемью полями: `plugin_id`, `company_id`,
`cabinet_id`, `project_id`, `grant_id`, `artifact_digest`, `scopes`, `exp`.
`status`, `surface` и любые другие дополнительные поля отклоняются. Protected
`plugin_health` статически возвращает тот же набор, но сквозная совместимость
capability surface остаётся **live unverified**.

Не добавляйте `PLUGIN_CLAIMS`, `PLUGIN_CLAIMS_SECRET` или signing secret в
manifest, frontend или новый backend env ради запуска demo.

### 4a. Scoped runtime identity и намеренные production 503

Deployment статически проецирует фактические `plugin_instance_id` и
`backend_digest` из non-secret pod annotations через один read-only
`downwardAPI` volume `plugin-runtime-identity` в
`/etc/agentscale/plugin-runtime`. Hello читает два файла с этими именами и
сверяет declaration, fixed headers и fresh introspection с ними; missing/invalid
projection = 503, identity/digest mismatch = 403. Init container, shell и
identity Secret для projection не используются.

Scoped service proxy для pod operations делает fresh `PluginActivator.wake` и
передаёт только `X-Plugin-Capability-Token`, `X-Plugin-Instance-ID`,
`X-Plugin-ID`, `X-Plugin-Artifact-Digest`; прочие client `X-Plugin-*` не
наследуются. В hello production composition platform-scoped egress declaration,
artifact metadata, pod action и SecretStore providers **намеренно unwired** и
отвечают **503**. Module-local provider registration не является доверенной
заменой; synthetic result/success запрещён. Реальные provider operations и
projection/wake поведение остаются live unverified.

### 4b. Pods, secret delivery, events и STS

- Optional `pods.allowlist` содержит 1..256 exact
  `{plugin_instance_id, project_id, job_id}` tuples; omitted declaration не даёт
  pod access. **Hello `pods` не объявляет**, несмотря на requested capability
  name в верхнеуровневом массиве.
- Optional `secret_delivery` имеет только `mode: k8s_secret` и 1..256 canonical
  `vault://`/`file://` refs. **Hello `secret_delivery` не объявляет.** У accepted
  modules отдельный per-instance delivery Secret имеет hashed `SECRET_*` keys,
  per-value digest versions, ownership/resourceVersion CAS, exact-set reconcile
  и read-only `0400` mount `/var/run/agentscale/capability-secrets`. Revoke
  удаляет только ref key; cleanup отзывает declared refs и удаляет только owned
  delivery Secret, не source SecretStore и не four-key runtime Secret. Live
  delivery/rotation/revoke/delete/RBAC не подтверждены.
- Platform-level `PLUGIN_EVENT_SIGNING_SECRET_REF` — typed
  `vault://`/`file://`/`k8s-secret://<name>/<key>` reference, required для
  enabled и не входящий в четыре hello runtime values. Dev sealed-example и
  read-only `0400` mounts для API/Gateway statically present; `.example` не
  содержит live ciphertext и не добавлен в resources. `optional: true` относится
  только к disabled platform; enabled+missing key fail-closed. Нет fallback на
  `PLUGIN_CLAIMS_SECRET`/`AUTH_TEST_SECRET` и нет live sign/verify/rotation proof.
- `objects.scoped_credentials` остаётся typed-unwired до STS enabled + отдельного
  post-probe availability. Prefix-wide operation не подменяется per-key presign
  или static/admin credentials; старый presign-код elsewhere не открывает её.
  Post-probe и scoped AssumeRole/policy/TTL/isolation evidence отсутствуют.

### 5. Gateway → hello webhook: static forwarding, live unverified

Hello принимает `X-Prodavan-Signature:
sha256=<hex HMAC-SHA256(WEBHOOK_SECRET, raw_body)>` и сравнивает подпись через
`hmac.compare_digest`. Подписываются неизменённые raw bytes; hello v1 не
добавляет timestamp/delivery/event/method/path в signed bytes односторонне.

В текущем statically implemented Gateway path sanitization удаляет клиентские
signature/event/delivery headers, а после успешной GatewayAuth
`build_forward_headers` восстанавливает их из `VerifiedWebhook` вместе с raw body.
Прежнее утверждение, что signature всегда теряется перед forward, устарело.

Direct-to-backend unit example и наличие forwarding-кода **не доказывают**
сквозной `External → Plugin Gateway → hello backend` webhook. Доставка остаётся
**pending integration/live acceptance**, включая secret rotation и отсутствие
duplicate side effects; готовый end-to-end сценарий не заявляется.

`_seen_deliveries` в процессной памяти — только demo: теряется при рестарте, не
координирует реплики и не является durable idempotency/outbox.

## Manifest — полный пример, не готовый пакет к установке

Текущий пример содержит 13 top-level полей:

```text
plugin_id, version, protocol, frontend, backend, capabilities,
migrations, egress_ref, public_pages, webhooks, min_platform_version,
mcp_servers, rpc_operations
```

`frontend` содержит `entry/base_path`, `backend` — `port/health`,
`migrations` — `engine/path`. `rpc_operations` содержит ровно две приватные
operations `hello.greetings@1.0`, перечисленные выше. `mcp_servers` содержит
data-only declarations тех же `list`/`greet` версии `1.0.0`. В manifest намеренно
нет `pods`, `secret_delivery`, `artifact_digest`/`signature` и встроенного egress
allowlist.

- Canonical machine contract —
  [`schema.json`](../../schemas/plugin-manifest/v1/schema.json), а не этот README.
- RPC envelope определяется canonical `protocol.json`; hello `app.js` не
  объявляется готовой нормативной реализацией или live proof.
- Список capability-имён не означает наличие работающих adapters. Запрашивайте
  только необходимые capabilities через отдельный grant.
- Manifest объявляет `8080` + `/health`; сами значения не доказывают настройку
  runtime или live readiness.

## Supply-chain guardrails и no-cutover

- **`:latest` запрещён.** Допускаются только полные immutable OCI references
  `image@sha256:<64 lowercase hex>`; `IfNotPresent` безопасен только с таким
  pin, а не с movable tag. Сейчас verified build evidence отсутствует.
- Перед любым deploy требуется **cosign** verify/provenance для exact digest.
  Это gate, а не заявление об already verified image. См. текущий
  [release workflow](../../../.github/workflows/plugin-artifacts.yml) и
  [CI contract](../../../docs/migration/plugin-test-plan.md).
- Контексты ревью, а не completed evidence: **задача 05** (tenant grants),
  **задача 07** (manifest gate), **задача 09** (activation),
  **задача 16** (artifact/CI provenance). Все они требуют отдельной live
  проверки владельцем workflow.
- **strangler/cutover не выполняется:** `plugin_enabled_modules[]` не меняется,
  `mod_equipment` переносится **позже**, с backup, data-compatibility review и
  явным этапом переноса данных; **переноса данных сейчас нет**.
  отдельными gates. Никакого destructive downgrade или удаления applied
  migrations. Используются только **эфемернные** fixtures, не shared tenant data.
  См. [no-cutover runbook](../../../docs/migration/plugin-cutover-safety.md).

## Target packaging и trusted delivery — не текущий quickstart

Целевой source path описан в [ADR-009](../../../docs/architecture-migration/ADR-009-module-source-authoring.md)
со статусом **«Предложено»**:

```text
source/private repo → agent snapshot/patch без Git credentials
  → protected PR + human review
  → trusted reusable workflow pinned SHA + OIDC constraints
  → backend OCI digest + frontend bundle digest
  → publish evidence → deploy saga → explicit public page (no plugin menu) → update/rollback
```

GitHub App, атомарная backend+bundle digest pair и deploy/rollback saga **не
заявляются существующими** на audited HEAD. Старые инструкции `docker build …
@sha256:<digest>` удалены как непригодный quickstart: digest вычисляется после
сборки, а не подставляется как заранее известный tag.

В **текущем** worktree release workflow/scripts statically реализуют более
строгий path: protected environment `PLUGIN_PLATFORM_PUBLICATION_CONFIG` с exact
source bindings и pinned scanner, release reverify, Trivy scan actual release
backend, protected static-origin HEAD с exact `plugin_shell_origin` в
`frame-ancestors`, required security headers и exact `X-AgentScale-Bundle-Digest`.
Только после этих проверок получается GitHub OIDC
token и выполняется `POST /internal/plugin-artifacts`; platform response должен
точно совпасть с publication. Это не успешный run: protected live inputs и
PUBLISHED evidence отсутствуют, missing config/scanner/static-origin/OIDC input
остаются blockers до POST.

## Target DB и migrations

При будущем accepted runtime:

- `TENANT_DB_DSN` ведёт только в `tenant_<company>`;
- plugin role владеет `module_hello_plugin` и сама выполняет DDL;
- платформа только provisioning БД/схемы/роли и проверка артефакта;
- Liquibase apply и проверка схемы завершаются **до** `/health = Ready`;
- checksum/apply/DB failure = не Ready;
- повторный apply идемпотентен по `DATABASECHANGELOG`.

Rollback кода и rollback БД различаются. Возврат предыдущего artifact не вызывает
автоматический destructive DB downgrade; schema-only downgrade не восстанавливает
данные. См. [PLUGIN-AUTHORING](../../../docs/PLUGIN-AUTHORING.md).

## Target frontend и network/security

- Отдельный static-origin, CSP, iframe без `allow-same-origin`.
- `data-host-origin` остаётся canonical; пустое значение допускает только closed
  `agentscale-plugin-shell/1` DTO из `iframe.name`, установленный host из
  server-derived bootstrap `plugin_shell_origin` до `src`. Missing/invalid DTO =
  fail-closed; `event.origin`/parent/nonce/instance/digest проверяются на каждом
  сообщении. Host/window.location/query/token не являются trust inputs, signed
  HTML после сборки не мутируется.
- **Любое изменение hello frontend source требует нового signed OCI bundle digest
  и новой publication; старый digest переиспользовать нельзя. После publication
  обязательны live HEAD evidence для direct static origin: exact shell
  `frame-ancestors`, security headers и новый `X-AgentScale-Bundle-Digest`.**
- Токены не попадают во frontend или URL.
- Точный RPC envelope/origin behavior определяется canonical машинной схемой,
  а не рекламируется по demo-коду.
- Restrictive securityContext и default-deny network grants обязательны как
  target; фактическая emergency NetworkPolicy сейчас намеренно уже steady-state.
- `:8001` остаётся агентам; plugin capabilities находятся в Plugin Gateway.
- Objects — tenant/prefix-scoped. `objects.scoped_credentials` typed-unwired до
  post-probe STS availability и не имеет presign/static fallback; это не
  утверждение о работающем objects path.

## Локальные contract tests

Для этого каркаса полезны проверки:

1. 13-field hello schema validation; ровно две typed operations, уникальные
   ключи/`backend_path`, `public=false`, согласованные `mcp_servers`; отказ
   unknown/self-reference/embedded egress и неподтверждённые `pods`/`secret_delivery`.
2. Fresh/repeat Liquibase apply; checksum failure и отсутствие таблиц → не Ready.
3. Отсутствие статического `PLUGIN_CLAIMS`; strict eight-field health;
   revoked/expired/wrong scope → deny.
4. Client `company_id` не меняет SQL scope, вычисленный из проверенной identity.
5. Неверный webhook HMAC/replay → deny; **обязательный отдельный Gateway → hello
   integration test**, а не только direct backend test.
6. CSP/sandbox отсутствуют `allow-same-origin`, tokens отсутствуют в URL.
7. Backend+bundle digest pairing, non-root/read-only securityContext и network
   default deny — после стабилизации соответствующих target workflows.
8. Jobs exact selectors/API+standalone registration и digest/lease fences; MCP
   durable conflict/replay/discovery/JSON-RPC/canonical broker; runtime missing
   projection/fixed headers/503 providers; events missing key/rotation; STS
   pre/post-probe без fallback; delivery exact-set/revoke/delete; CI отсутствие
   POST до release+Trivy+CSP evidence. Все эти gates остаются pending и не
   отмечаются пройденными по наличию тестов.

**Никакие tests/build/lint/compile/git checks при этой документной правке не
запускались.** Тесты и код в дереве не превращают пакет в рабочий reference до
live acceptance.