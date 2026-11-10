# hello_plugin backend: security integration contract

> **Statically implemented / live unverified / STOP-SHIP.** Документ описывает
> код и signed-manifest контракт текущего worktree. Он не включает plugin
> platform, не подтверждает live integration и не разрешает deploy до реальных
> интеграционных проверок и live acceptance.

## Runtime env и managed Secret

Backend читает ровно четыре runtime значения, проецируемые platform-managed
per-instance Secret через `secretKeyRef`:

- `GATEWAY_URL`;
- `PLUGIN_ID`;
- `TENANT_DB_DSN`;
- `WEBHOOK_SECRET`.

Имя webhook-переменной — **`WEBHOOK_SECRET`**, а не
`PLUGIN_WEBHOOK_SECRET`. Отдельного static capability token в env нет.
Значения Secret не должны попадать в argv, DTO, исключения или логи.

## Typed RPC: canonical capability и уникальные operations

Canonical capability reference hello — `hello.greetings@1.0`. В manifest она
записывается объектом `{"id":"hello.greetings","version":"1.0"}`; строка с
`@1.0` используется в bootstrap/frontend capability vocabulary. Это typed
module RPC capability, а не одно из девяти infra adapter имён в верхнеуровневом
`capabilities`.

`rpc_operations` содержит ровно две distinct declarations:

| Capability | Operation | Method | Backend path | Public |
|---|---|---|---|---|
| `hello.greetings@1.0` | `list` | `GET` | `/api/greetings/list` | `false` |
| `hello.greetings@1.0` | `greet` | `POST` | `/api/greetings` | `false` |

Ключ `(capability_id, capability_version, operation)` и каждый
`backend_path` уникальны. `/api/stats` существует в backend, но не объявлен
в `rpc_operations`, поэтому не является broker-разрешённой operation. Обе hello
operations приватны; anonymous public GET-only surface их не открывает.

## Capability introspection на каждый API request

1. Plugin Gateway выдаёт backend короткоживущий токен в
   **X-Plugin-Capability-Token**. Клиентские Authorization и identity-заголовки
   не являются источником доверия. Статического токена в env нет.
2. Backend вызывает **GET {GATEWAY_URL}/capabilities/v1/health** с
   **Authorization: Bearer <token>**, Accept: application/json.
   AsyncClient создаётся с `trust_env=False`, `follow_redirects=False` и
   коротким timeout. Claims не кэшируются между запросами.
3. HTTP 200 обязан содержать JSON-объект РОВНО с восемью полями:
   `plugin_id`, `company_id`, `cabinet_id`, `project_id`, `grant_id`,
   `artifact_digest`, `scopes`, `exp`. Поля `status`/`surface` из
   старого health-response и любые другие дополнительные поля не принимаются.
   Текущий protected `plugin_health` статически возвращает этот же набор;
   сквозная проверка всё равно остаётся **live unverified**.
4. `plugin_id`, `company_id`, `grant_id` — непустые строки;
   `artifact_digest` — `sha256:` плюс 64 lowercase hex;
   `cabinet_id`/`project_id` — непустая строка или `null`; `scopes` —
   список уникальных непустых строк; `exp` — конечный числовой Unix timestamp.
   Чужой `plugin_id` даёт 403, истёкший `exp` или отказ Gateway 401 даёт
   401, revoke/другой отказ 403 даёт 403. Сетевая ошибка, redirect,
   неожиданный статус, невалидный JSON или нарушение схемы дают 503.
5. Backend не декодирует JWT и не получает `PLUGIN_CLAIMS_SECRET` или иной
   signing secret. Криптографическая проверка и revocation выполняются на
   protected capability surface.

## Tenant scope и существующие query/body поля

`company_id` остаётся обязательным в текущих API запросах только как assertion
маршрута: после удаления Gateway префикса `/pluginpage/{plugin}/{company}` это
позволяет независимо сопоставить запрошенную компанию с introspection и
company-bound `TENANT_DB_DSN`. Переданные `company_id`/`cabinet_id`/`project_id`
никогда не используются как доверенные SQL selectors. Несовпадение даёт 403;
SQL параметры берутся из introspection. `X-Company-ID` не читается.

`greetings` фильтруются по `company_id` и, если они заданы claims, по
`cabinet_id`/`project_id`. `greeting_stats` имеет только UUID key в
существующей миграции, поэтому ключ вычисляется детерминированно из полного
introspected `[company_id, cabinet_id, project_id]`; client input в ключ не
попадает.

## Webhook: совместимая hello v1 подпись

Hello принимает **`sha256=<hex HMAC-SHA256(WEBHOOK_SECRET, raw_body)>`** в
`X-Prodavan-Signature` и сравнивает через `hmac.compare_digest`. Подписываются
именно неизменённые raw bytes. Timestamp, delivery, event, method и path НЕ
добавляются в hello v1 signed bytes односторонне.

Gateway статически сохраняет v1 compatibility и также содержит отдельный v2
canonical-signing path. Hello не объявляет поддержку v2: любой переход требует
совместного версионирования, timestamp tolerance и replay policy, а не
несериализованной конкатенации полей.

Клиентские signature/event/delivery заголовки удаляются sanitization, но после
успешной GatewayAuth текущий `build_forward_headers` статически восстанавливает
их из `VerifiedWebhook` вместе с raw body. Таким образом прежнее утверждение
«signature всегда теряется перед forward» устарело. Наличие этого кода и unit
tests не является доказательством сквозной доставки: **Gateway → hello backend
webhook остаётся pending integration/live acceptance**, включая secret rotation
и отсутствие duplicate side effects.

`_seen_deliveries` — только демонстрационная in-memory идемпотентность одного
процесса. Она теряется при рестарте, не координирует реплики и НЕ является
durable production-решением. Production требует атомарного durable delivery
record с tenant/plugin key, body digest, TTL/retention и повторной обработкой
ошибок; сроки хранения и миграция хранилища — отдельная интеграционная задача.

## MCP registration, discovery и backend broker

Hello manifest объявляет optional `mcp_servers`: server `hello.greetings`,
tools `list`/`greet` версии `1.0.0` с bounded object `input_schema`.
Declaration не меняет grant и не регистрирует executable handler. Durable Redis
registration привязана к company/plugin/instance и artifact-digest-scoped server;
`SET NX` + re-read допускает только exact idempotent replay и отклоняет conflict.
Discovery показывает лишь пересечение signed declarations и registrations с
совпадающими tool version/input-schema digest.

Invocation проходит `PluginCapabilityService` как infra `mcp` + operation
`mcp.invoke`. `DurableMcpBroker` повторно загружает exact signed manifest и
active grant, проверяет schema/tenant assertions и выбирает **ровно одну**
canonical `rpc_operations` declaration по `(server_id, tool_name)`. Backend
вызов идёт на её `backend_path` через scoped proxy. Это не remap MCP tool в
другую infra capability и не generic dispatch.

Authenticated internal JSON-RPC mount —
`POST /capabilities/v1/mcp/servers/{server_id}`; допускаются только bounded
`initialize`, `notifications/initialized`, `tools/list`, `tools/call`.
Static mount/discovery/broker не подтверждают live client → service → hello
backend flow, registration persistence, revoke/rollout, quota/audit или redaction.

## Server-owned jobs — не client-selected Celery tasks

`PLUGIN_JOB_DECLARATIONS` содержит только exact verified
`{plugin_id, version, artifact_digest}` selectors. Он не разрешает caller
задавать task name, handler, backend route/digest. Из signed private
`rpc_operations` выбранных publications platform строит scoped
`ManifestJobTasks`: active grant и canonical subject/company/plugin/instance/
artifact/backend digest перепроверяются до handler и перед result commit;
execution использует canonical resolver, fresh wake, job lease и scoped proxy.

API получает install-created registry через typed `job_worker_registration`
hook без FastAPI-state registry. Standalone worker использует deferred factory,
создающую ресурсы только в worker process и закрывающую их своим owner'ом.
Фактические selectors для hello и любой successful API/standalone job run в
текущих evidence отсутствуют; contract **live unverified**.

## Scoped runtime identity, fixed headers и production 503

Помимо per-request introspection, runtime API требует platform-managed projection
фактических `plugin_instance_id` и `backend_digest`. Deployment записывает их
в non-secret pod annotations и проецирует через один read-only `downwardAPI`
volume `plugin-runtime-identity` в `/etc/agentscale/plugin-runtime`. Hello
читает только два bounded ASCII файла с этими именами; missing/invalid projection
= 503, declaration/header/claims mismatch = 403. Init container, shell и identity
Secret для projection не применяются.

Scoped service proxy для pods делает fresh `PluginActivator.wake` и формирует
ровно четыре plugin headers: `X-Plugin-Capability-Token`,
`X-Plugin-Instance-ID`, `X-Plugin-ID`, `X-Plugin-Artifact-Digest`. Остальные
client `X-Plugin-*` не наследуются; capability token не попадает в URL/body/
result/logs.

В production hello app platform-scoped egress declaration, artifact metadata,
pod action и SecretStore providers намеренно unavailable: их typed endpoints
отвечают **503**, а не synthetic success. Явные test doubles вне production
composition не являются trusted providers. Remote declaration с любым
`secret_refs` отклоняется с typed **503 REMOTE_PROVIDER_SECRET_REFS_FORBIDDEN**
до SecretStore/DNS, чтобы compromised remote data не включал local plaintext
fallback. Реальная projection/wake и любой будущий platform-scoped provider
остаются live unverified.

## `pods` и отдельный `secret_delivery`

Canonical `pods` declaration — closed object с non-empty `allowlist` из
1..256 unique exact `{plugin_instance_id, project_id, job_id}`. Нет wildcards,
arbitrary pod ids, commands или args; omitted declaration не даёт pod access.
Hello не содержит `pods`, поэтому верхнеуровневый requested `pods` capability
не является pod grant.

Canonical `secret_delivery` — closed object
`{mode: "k8s_secret", allowlist: [vault://... | file://...]}` с 1..256 unique
refs, без Secret names/data/plaintext. Hello это поле не объявляет. Для accepted
manifest platform управляет **отдельным** per-instance delivery Secret: hashed
`SECRET_<SHA256>` keys, per-value digest versions, ownership/resourceVersion
CAS и exact-set reconcile со удалением stale keys. Deployment монтирует Secret
read-only `0400` в `/var/run/agentscale/capability-secrets` только после
сверки exact refs/keys/value versions. Revoke удаляет только выбранный key;
cleanup отзывает declared refs и удаляет только owned delivery Secret. Source
SecretStore и основной four-key runtime Secret этим lifecycle не удаляются.
Real delivery/rotation/revoke/delete/RBAC остаются pending.

## Platform events key и Objects STS — fail-closed boundaries

`PLUGIN_EVENT_SIGNING_SECRET_REF` — platform-level typed
`vault://`/`file://`/`k8s-secret://<name>/<key>` reference, а не пятое hello
managed Secret value. Enabled platform требует resolution; claims/auth secrets не
fallback. Dev sealed-example и read-only `0400` mounts для API/Gateway являются
static wiring; `optional: true` допускает только disabled platform, а
enabled+missing key остаётся startup failure. Live ciphertext/key/sign/verify/
rotation evidence отсутствует.

`objects.scoped_credentials` требует `scoped_credentials_wired=True`: STS
enabled плюс отдельное post-probe availability. До этого operation typed-unwired;
per-key presign не заменяет prefix-wide credentials, static/admin keys не
подставляются. Существование presign implementation elsewhere не открывает эту
operation. Модульный контракт остаётся unverified; сам STS-провайдер
подтверждён только в disposable MinIO (29/29: TTL 900..3600, canary-only policy,
foreign 403), а API/Gateway lifespan и live AssumeRole — нет.
