# Plugin authoring — Statically implemented / Target / Live unverified

> **STOP-SHIP:** plugin platform остаётся emergency disabled по действующему
> fail-closed решению. Historical independent audit HEAD `6ee720dd` не
> подтвердил сквозной plugin-сценарий; после него код существенно изменился, но
> новых live evidence нет. Этот документ различает **наличие реализации** и
> **проверку**, не является рабочим quickstart или разрешением на deploy.
>
> Текущее состояние: [migration status](migration/plugin-migration-status.md).
> Live acceptance: [все проверки открыты](migration/plugin-acceptance-map.md).
> Self-service target: [ADR-009 — **Предложено**](architecture-migration/ADR-009-module-source-authoring.md).

## 1. Значения статусов

- **Statically implemented** — в текущем worktree есть код, схема, manifest,
  wiring или написанные tests. Это не значит, что код запускался, интеграция
  работает или компонент разрешён в live.
- **Target** — принятое или предложенное целевое требование; оно может уже
  частично реализовано, но всё равно требует integration/live acceptance.
- **Live unverified** — нет воспроизводимого live evidence с HEAD, окружением,
  датой и результатом. До снятия этого статуса по всей цепочке действует
  **STOP-SHIP**.

Старое деление «Supported / Not implemented» здесь намеренно заменено: уже
написанные PublicationApproval, ModuleSource, runtime lifecycle и typed RPC
не следует называть отсутствующими, но и нельзя выдавать за live-supported.

## 2. Матрица возможностей

| Область | Statically implemented в worktree | Target / live unverified |
|---|---|---|
| Live install/deploy | Есть publication/grant/deployment code и production composition tests | **Нет live readiness.** Любой dev/prod quickstart запрещён до integration + live acceptance и human-решения |
| Текущий S1 / legacy cleanup | Legacy dynamic host-only web files удалены; current plugin path и preserved renderer/dynamic inventory охраняются static import guards | Guards и browser regression не запускались; declarative tables/helpers и cutover не удалять |
| Язык/framework | Демонстрационные Python/FastAPI backend и static JS | Любые языки/frameworks только при OCI/digest, manifest, health, identity, DB, sandbox, securityContext и network contracts; нет live SDK/adapter certification |
| Manifest | Машинная схема содержит 11 required top-level fields и optional typed `rpc_operations`, `mcp_servers`, `pods`, `secret_delivery`; hello объявляет `hello.greetings@1.0` и MCP declarations | Publication и все RPC/MCP/runtime paths остаются live unverified |
| Migrations | Есть Liquibase-пример и plugin-owned migration contract | Fresh/repeat/checksum/readiness и historical upgrades требуют реальных проверок; Flyway/Alembic не являются verified authoring paths |
| Backend + bundle | Есть digest-pair metadata, PublicationApproval и deployment binding | Trusted publish/deploy пары, signatures/provenance/SBOM и rollback не подтверждены live |
| Static frontend | Есть HTML/JS, bootstrap DTO, sandbox/RPC code и current plugin import path | Hosted static-origin, CSP и browser E2E не подтверждены |
| Tenant DB | `AsyncpgAdminConnection` реализует async cluster/tenant pools, binds, health и lifecycle close | Real provisioning/isolation/pgaudit и pool lifecycle на PostgreSQL не подтверждены live |
| Identity | Per-request hello introspection согласован со eight-field health; lifecycle K8s clients используют отдельный scoped TokenRequest provider | Revoke/expiry/RBAC/refresh и сквозная identity требуют integration/live; не смешивать lifecycle identity с plugin request token |
| Capabilities | Enabled registry composition требует все девять real typed adapters и `quota_usage`; missing/stub — startup blocker | Не утверждать, что cache/objects/docs/events/jobs/pods/mcp/secrets/network работают на реальной инфраструктуре |
| Jobs | Exact verified `PLUGIN_JOB_DECLARATIONS`, scoped `ManifestJobTasks`, API typed hook и standalone deferred factory | Worker registration/execution, leases/retry и digest fences live unverified |
| MCP | `mcp_servers` + durable registration, `mcp.invoke` broker к canonical `rpc_operations`, authenticated JSON-RPC mount | Discovery/invoke/client/backend/revoke/quota/audit live unverified |
| Scoped runtime | Read-only downwardAPI identity, fresh-wake fixed service-proxy headers; hello production provider stubs = 503 | Projection/wake и любые реальные provider operations live unverified |
| Events | Typed `PLUGIN_EVENT_SIGNING_SECRET_REF`, dev sealed-example и read-only mounts; optional только для disabled | Real key/sign/verify/rotation/missing-key startup live unverified |
| Objects STS | `objects.scoped_credentials` typed-unwired до enabled + post-probe availability; нет presign/static fallback для operation | Real disposable MinIO: **29/29 passed, 0 skipped** ([JUnit](../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml), [summary](../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); STS TTL 900..3600 и canary-only probe policy подтверждены, presign остаётся 60..3600. Production/live configuration и API/Gateway lifespan остаются unverified |
| CI publication | Protected publication config, release Trivy evidence, static-origin CSP+digest header, OIDC POST после release verify | Protected live inputs и successful run отсутствуют; B1 evidence-only (§9): cluster-internal `plugin-static` + dev allowlist implemented offline (plugin suite 4251/1 skip, static_origin 85, infra/ops 156, scoped ruff passed), live blocked by deployment/config; НЕ public browser static hosting; остаются blockers |
| Pods/delivery | Exact `pods.allowlist`, reference-only `secret_delivery`, отдельный versioned delivery Secret lifecycle | Real pods/delivery/rotation/revoke/delete/RBAC live unverified |
| Runtime Secret | SecretProjection управляет `GATEWAY_URL`, `PLUGIN_ID`, `TENANT_DB_DSN`, `WEBHOOK_SECRET` | Rotation/CAS/drift/ownership и Gateway→hello delivery остаются pending |
| Port/health | Hello использует `8080` и `/health`; Liquibase readiness fail-closed | Произвольный port/health сам не настраивает Deployment/Service/probe |
| securityContext/network | Есть restrictive manifests/generator code и emergency fail-closed policy | Нет общего разрешения egress/DB/MinIO/events; live policy не проверялась |
| Source self-service | ModuleSource, SQL reservation, GitHub App/OIDC clients и production composition присутствуют | ADR-009 остаётся «Предложено»; GitHub App/source→PR→OIDC→publish→deploy не принят как live service |
| Menu/public/update/rollback | Bootstrap, ensure/update saga и anonymous read-only operation guards реализованы статически | Menu/health/public activation, update compensation и rollback требуют failure injection и live acceptance |

## 3. Authoring boundary

Язык и framework свободны **только внутри жёсткой упаковки и security contract**:

```text
source + manifest + migrations
  → backend OCI digest + frontend bundle digest
  → trusted signature/provenance/SBOM
  → exact PublicationApproval
  → tenant DB provisioning
  → plugin self-migration до Ready
  → per-request identity + bounded typed APIs
  → sandboxed static frontend
  → явные securityContext и network grants
```

Это target pipeline с statically implemented частями, а не доступный end-to-end
quickstart. PublicationApproval не заменяет artifact verification, а grant не
заменяет подтверждённый Deployment/health.

## 4. Manifest — текущая машинная схема, live unverified

Машинный источник: `packages/schemas/plugin-manifest/v1/schema.json`.
Обязательные **11 top-level полей**:

1. `plugin_id`
2. `version`
3. `protocol`
4. `frontend`
5. `backend`
6. `capabilities`
7. `migrations`
8. `egress_ref`
9. `public_pages`
10. `webhooks`
11. `min_platform_version`

Optional `rpc_operations`, `mcp_servers`, `pods` и `secret_delivery` описаны
canonical schema и typed domain contracts. Прежнее «schema не допускает
`rpc_operations`» устарело. Hello сейчас содержит 13 top-level fields: 11
required + `mcp_servers` + `rpc_operations`; `pods` и `secret_delivery` у hello
отсутствуют. Ни одно optional declaration не доказывает live path и не расширяет
grant само по себе.

Иллюстративный **target** manifest, а не артефакт для немедленной установки:

```json
{
  "plugin_id": "example_plugin",
  "version": "0.1.0",
  "protocol": "plugin-rpc/1",
  "frontend": {
    "entry": "index.html",
    "base_path": "/plugins/example_plugin/0.1.0/"
  },
  "backend": {
    "port": 8080,
    "health": "/health"
  },
  "capabilities": ["cache"],
  "migrations": {
    "engine": "liquibase",
    "path": "migrations/changelog.xml"
  },
  "egress_ref": "grant:example_plugin",
  "public_pages": [],
  "webhooks": [],
  "min_platform_version": "4.0.0"
}
```

Правила:

- `frontend` содержит `entry` и `base_path`; `backend` — `port` и
  `health`; `migrations` — `engine` и `path`.
- Запрашивайте только необходимые infra capabilities. Перечисление всех девяти
  имён не создаёт adapters и не расширяет grant.
- `artifact_digest`, `signature`, `egress`, `allowed_hosts`,
  `allowed_paths` и другие unknown fields не добавляются в подписанный JSON.
  Digest появляется после сборки; egress живёт в grant, а не в manifest.
- `public_pages: []` — безопасный default. Public surface требует отдельного
  явного решения и проверки.
- Используйте MVP convention `8080` и `/health`, пока параметризация
  deployment не реализована и не проверена.

### Typed operations и canonical capability

Каждая declaration содержит `capability: {id, version}`, `operation`,
`http_method` GET/POST, unique `/api/...` `backend_path` и optional
`public` (default false). Уникален tuple
`(capability_id, capability_version, operation)`; один `backend_path` нельзя
повторно использовать для разных declarations.

Canonical hello reference — **`hello.greetings@1.0`**:

```json
"rpc_operations": [
  {
    "capability": {"id": "hello.greetings", "version": "1.0"},
    "operation": "list",
    "http_method": "GET",
    "backend_path": "/api/greetings/list",
    "public": false
  },
  {
    "capability": {"id": "hello.greetings", "version": "1.0"},
    "operation": "greet",
    "http_method": "POST",
    "backend_path": "/api/greetings",
    "public": false
  }
]
```

Верхнеуровневый `capabilities` и typed module RPC reference — разные
vocabularies. `hello.greetings@1.0` не добавляется в enum девяти infra
capabilities и не доказывает server-side adapter support.

### Server-owned jobs — statically implemented, live unverified

`PLUGIN_JOB_DECLARATIONS` — platform/server configuration, не client input и не
поле manifest. JSON допускает только unique exact selectors
`{"plugin_id","version","artifact_digest"}`. Composition загружает ровно
выбранные publications и требует PUBLISHED/APPLIED, non-quarantined verified
evidence, согласованные manifest/backend identity и digest pair. Request не
выбирает Celery task name, backend route/digest или handler.

Signed private `rpc_operations` преобразуются в non-empty scoped
`ManifestJobTasks` allowlist с canonical reference
`capability@major.minor:operation`. Registry повторно связывает active grant,
subject/company/plugin/instance/artifact и publication backend digest до handler
и до commit результата; scoped executor использует canonical backend resolver,
fresh activator wake, job lease и fixed HTTP proxy. Redis execution store
различает running lease и completed result digest, не fabricating success.

В API install-created `ManifestJobTasks` передаётся через typed
`job_worker_registration` hook и остаётся platform-owned. Standalone Celery
использует `DeferredStandalonePluginJobComposition`: ресурсы создаются лениво в
worker process и закрываются standalone owner'ом, а не FastAPI state/process-
global registry. Это wiring, не evidence успешного worker run.

### MCP — declarations + durable registration + canonical broker

- `mcp_servers` — optional signed data-only declarations: unique `server_id`,
  non-empty unique tools с `name`, SemVer `version`, description и bounded object
  `input_schema`. Поле не меняет `capabilities`, grant или transport.
- Durable Redis registration ограничена
  `(company_id, plugin_id, plugin_instance_id, digest_bound_server_id)`;
  `SET NX` + re-read делает exact replay идемпотентным, а conflict fail-closed.
  Discovery показывает только пересечение signed declarations и registrations с
  совпадающими version/schema digest.
- `DurableMcpDiscovery` вызывает `PluginCapabilityService` с infra capability
  `mcp` и operation `mcp.invoke`, сохраняя grant/auth/quota/audit. Attached
  `DurableMcpBroker` затем выбирает ровно одну canonical `rpc_operations`
  declaration по `(server_id, tool_name)` и вызывает её backend path через scoped
  proxy. Tool **не преобразуется** в cache/objects/docs или другую infra capability.
- Authenticated internal mount `POST /capabilities/v1/mcp/servers/{server_id}`
  реализует bounded JSON-RPC 2.0 `initialize`, `notifications/initialized`,
  `tools/list`, `tools/call`; notification получает 202. Это не public/anonymous
  endpoint и не live client certification.

Hello объявляет MCP server `hello.greetings` с tools `list`/`greet` версии
`1.0.0`; они соответствуют двум canonical RPC declarations выше. Реальные
registration/discovery/broker/JSON-RPC flow, revoke/rollout и quota/audit остаются
pending.

### `pods` и отдельный `secret_delivery`

`pods` — optional closed object с non-empty `allowlist` из 1..256 unique exact
`{plugin_instance_id, project_id, job_id}` tuples. Wildcards, arbitrary pod ids,
commands/args отсутствуют; omitted declaration не даёт pod access.

`secret_delivery` — optional closed object `{mode: "k8s_secret", allowlist}` с
1..256 unique canonical `vault://` или `file://` refs. Manifest не содержит Secret
names/data или plaintext. Platform projection использует отдельный per-instance
delivery Secret: hashed `SECRET_<SHA256>` keys, per-value digest annotation,
ownership/resourceVersion CAS, exact-set reconcile и удаление stale keys. Pod
монтирует его read-only `0400` в `/var/run/agentscale/capability-secrets`.
Revoke удаляет только выбранный key; lifecycle cleanup отзывает declared refs и
удаляет только owned delivery Secret, не source SecretStore и не основной
four-key runtime Secret. Real lifecycle/RBAC остаётся live unverified.

## 5. Backend, tenant DB и migrations

### DSN, async admin и владение схемой

Плагин получает `TENANT_DB_DSN` только к БД своей компании. Роль владеет
`module_<plugin>` и ограничена `NOCREATEDB NOCREATEROLE NOSUPERUSER`,
`CONNECT` только к своей БД и отсутствием `USAGE` на чужие схемы.
Платформенная БД недоступна; RLS не считается границей.

**Plugin role сама выполняет DDL.** Платформа делает provisioning БД, роли,
схемы и секретов и проверяет артефакт/границы, но не исполняет plugin DDL
доверенной платформенной ролью.

`AsyncpgAdminConnection` — statically implemented administrative port:
startup open/ping cluster pool, lazy tenant pools, true asyncpg binds,
fail-closed health и idempotent close всех pools. Это не evidence реального
PostgreSQL isolation или успешного provisioning.

### Миграции до Ready

Практический описанный вариант сейчас — **Liquibase**:

```text
container start
  → прочитать changelog из проверенного артефакта
  → применить миграции своей plugin role
  → проверить схему
  → только затем /health = Ready
```

Ошибка apply/checksum = не Ready. Повторный запуск должен быть идемпотентным по
migration history. Flyway/Alembic — target alternatives по машинной схеме, но не
готовые verified authoring paths; для них нужны отдельные contract tests и guide.

### Rollback кода ≠ rollback БД

- Code rollback возвращает предыдущую backend+bundle digest pair.
- Новый и прежний код должны быть совместимы через expand/contract.
- Destructive contract выполняется только после прекращения использования старой
  схемы старым кодом.
- Автоматический destructive DB downgrade при откате кода запрещён.
- Schema-only downgrade может воссоздать пустую структуру, но не восстанавливает
  данные и не является полным rollback продукта.

## 6. Identity, lifecycle tokens, capabilities и runtime secrets

- Plugin Gateway и `PluginClaims` отделены от agent `:8001` /
  `PodBridgeClaims`; плагин не получает agent routes.
- Identity проверяется на каждый запрос или короткий lease и связывается с
  plugin/company/instance/grant/artifact digest.
- Статический `PLUGIN_CLAIMS` в pod env не требуется и не читается hello.
- Клиентские `X-Company-ID` и query/body `company_id` не являются
  самостоятельным доверенным tenant selector. SQL scope строится только из
  проверенной identity и company-bound DSN.
- Capabilities — bounded typed operations, не generic `sql`/`command`
  endpoint. Enabled composition требует real adapter, operations и quota hook;
  real external behavior остаётся live unverified.
- Objects — tenant/prefix-scoped. Для operation `objects.scoped_credentials`
  обязателен явный `scoped_credentials_wired=True`: STS enabled плюс отдельное
  post-probe availability. До него operation typed-unwired и fail-closed;
  per-key presigned URL не эмулирует prefix-wide credentials, static/admin
  credentials не подставляются. Credentials и подписи не попадают в логи/audit.
  Сам STS-провайдер подтверждён только в disposable MinIO (29/29: TTL 900..3600,
  canary-only policy, foreign 403); модульный контракт и API/Gateway lifespan
  остаются unverified.
- `:8001` не используется плагином; `/capabilities/v1/*` находится в Plugin
  Gateway.

### Runtime deployment identity и fixed service-proxy headers

Deployment записывает фактические `plugin_instance_id` и `backend_digest` в
non-secret pod annotations и проецирует их через один read-only `downwardAPI`
volume `plugin-runtime-identity` в `/etc/agentscale/plugin-runtime` как два файла
`plugin_instance_id` и `backend_digest`. Init container, shell и identity Secret
для этого не используются. Hello runtime API требует fresh per-request
introspection, fixed headers и совпадение declaration с introspected claims и
фактической projection; missing/invalid projection = 503, mismatch = 403.

Scoped service proxy для pods выполняет fresh `PluginActivator.wake` и передаёт
ровно `X-Plugin-Capability-Token`, `X-Plugin-Instance-ID`, `X-Plugin-ID`,
`X-Plugin-Artifact-Digest`, отбрасывая остальные client `X-Plugin-*`. В hello
production composition platform-scoped declaration/artifact/pod-action/secret
providers намеренно unwired и отвечают 503; module-local registration не
считается trusted replacement и synthetic success запрещён. Всё это остаётся
**live unverified**.

### Events signing secret — typed reference, optional только для disabled

`PLUGIN_EVENT_SIGNING_SECRET_REF` содержит reference, не ключ:
`vault://...`, `file://...` или `k8s-secret://<secret-name>/<key>`. Enabled
platform требует непустой valid reference и успешное resolution;
`PLUGIN_CLAIMS_SECRET` и `AUTH_TEST_SECRET` не являются fallback.

Dev содержит `plugin-event-signing.sealed.yaml.example` с placeholder (не live
ciphertext) и read-only `0400` mounts
`/var/run/secrets/agentscale-plugin-events` для API и Plugin Gateway с
`k8s-secret://agentscale-plugin-events/signing-key`. `.example` не подключён в
kustomization resources. `optional: true` у volume допускает только запуск
emergency-disabled platform; enabled+missing/unreadable key остаётся startup
failure. Real SealedSecret/key/sign/verify/rotation evidence отсутствует.

### Scoped TokenRequest — только platform lifecycle

`ServiceAccountTokenProvider` для DeploymentAdmin/SecretProjection использует
только ServiceAccount `plugin-deployment-admin`, audience
`kubernetes.default.svc`, TTL не более 3600 секунд, proactive refresh,
coalesced in-flight mint и shutdown. Mounted/static lifecycle token этим путём
не принимается. Это отдельная K8s lifecycle identity, не токен browser→plugin и
не замена per-request introspection.

### SecretProjection — ровно четыре managed keys

Per-instance Secret содержит только `GATEWAY_URL`, `PLUGIN_ID`,
`TENANT_DB_DSN`, `WEBHOOK_SECRET`. Deployment получает их через
`secretKeyRef`. Hello использует именно `WEBHOOK_SECRET`, не
`PLUGIN_WEBHOOK_SECRET`.

Static reconcile проверяет ownership labels, exact keys, non-empty values,
DSN-ref SHA-256 annotation и resourceVersion CAS. Значения сравниваются при
каждом ensure, поэтому rotation при неизменном ref предусмотрена кодом, но её
реальный DSN/webhook сценарий остаётся pending acceptance.

## 7. Frontend sandbox и anonymous read-only surface

- Отдельный static-origin, restrictive CSP и versioned immutable bundle.
- iframe без `allow-same-origin`; токены не передаются в frontend или URL.
- Любой `postMessage` проверяет source/origin по canonical `plugin-rpc/1`,
  включая nonce/instance/plugin/digest.
- Public pages отсутствуют по умолчанию. Menu/public активируются только после
  deploy/health/security checks, а не при простом наличии static files.
- Private bootstrap требует membership/active company/active grant/enabled
  installation/published digest-matched artifact.
- Anonymous bootstrap разрешён только для exact `public_pages` route и
  возвращает лишь explicit public operations без token/DSN/admin receipt.
- Anonymous broker/Gateway допускает только unique read-only public GET
  declaration; GET/HEAD с пустым body. Public POST/side effects запрещены даже
  при `public: true`, а non-public declaration не разрешена. Anonymous не
  получает capability token.

Обе hello operations имеют `public: false`; в частности, mutating `greet`
POST нельзя сделать anonymous только сменой frontend visibility.

## 8. securityContext и network grants — target минимум

Backend image/Deployment должны предусматривать:

- `runAsNonRoot: true` и непривилегированного пользователя;
- `allowPrivilegeEscalation: false`;
- `readOnlyRootFilesystem: true` с явными ограниченными writable mounts;
- `capabilities.drop: ["ALL"]`;
- `seccompProfile.type: RuntimeDefault`;
- явные CPU/memory requests и limits;
- отсутствие hostPath, hostNetwork, privileged и Kubernetes API credentials у
  plugin runtime;
- default-deny NetworkPolicy и отдельные grants только на DNS, Plugin Gateway,
  собственную tenant DB и явно разрешённый egress/objects/events.

Это target authoring requirements. Наличие generator/manifests не заменяет
static manifest verification и live network/security acceptance.

## 9. Source, PublicationApproval и deployment saga

ADR-009 остаётся **«Предложено»**, хотя части source/CI composition уже
statically implemented. Предлагаемый путь:

```text
source → private platform-org repo/template
  → SQL reservation → GitHub App provisioning
  → agent snapshot/patch без Git credentials в pod
  → protected PR + human review → default branch
  → trusted reusable workflow pinned SHA
  → OIDC repository/ref/sha/job_workflow_ref
  → exact PublicationApproval + backend+bundle digest pair
  → deploy saga → явный public (меню НЕ реализовано, см. §9) → update/rollback saga
```

### ModuleSource и reservation

`ModuleSource` — metadata source/authoring и trust config, **не второй runtime
registry**. Runtime registry хранит publication, evidence и согласованную digest
pair. До provider create отдельная SQL reservation фиксирует company+plugin и
provider+full_name без фиктивного `repo_id`; `attempt_id` ограждает retry/
release. Reservation lifecycle не равен готовому source или publication.

### PublicationApproval и exact policy

Durable approval создаётся до publication и связывает company/plugin/SemVer/
bundle digest с exact capabilities, egress и quotas. Он не является grant или
ACTIVE status. New/revoked/update grant требует exact approval; successful grant
saga отмечает `consumed_at`. Exact ACTIVE replay отдельно обеспечивает
Deployment/routes без второго grant или secret rotation.

Approval привязан к **exact bundle digest**: rerun/новая сборка с другим digest
не покрывается прежним approval и требует нового durable approval с независимым
review. Same-commit resume (**B2**) **statically implemented** в release
workflow (шаг `Resume preflight for same-commit quarantine artifacts`, id
`resume_preflight`), но **live не подтверждён**:

- Preflight (после trust/ORAS/prepare/GHCR login, до backend build) резолвит
  только exact quarantine refs `$QUAR_BACKEND:sha-$GITHUB_SHA` и
  `$QUAR_BUNDLE:sha-$GITHUB_SHA`. **Both absent** → обычный first-run
  build/sign; **both present** → resume без rebuild; **partial state** и любые
  transient/auth/network ошибки — fail-closed, НЕ rebuild (absence признаётся
  только по явному not-found/404/manifest unknown).
- Resume вызывает `plugin_quarantine_resume.py resume`: `oras manifest fetch
  --output` (raw bytes, не pretty re-serialization) и `oras blob fetch
  --output` для config blob; offline проверяются sha256(raw)==resolved bundle
  digest, config digest/size и byte-identity с локально пересозданным canonical
  manifest, а signed backend_image_ref аннотация сверяется с expected release
  pin. Exact backend+bundle digest pair сохраняется; expected/pushed manifest
  намеренно одинаковы (integrity = raw digest + полный verify, а не diff).
- Пропускаются только пять build/evidence/sign steps;
  `quarantine_verify` выполняется **всегда** (полный cosign verify +
  evidence + canonical + compatibility gate), promote/reverify/publish общие.
  Helper — НЕ evidence и НЕ decision: доверие даёт только полный verify.
- **Operator flow:** первый run печатает exact digest pair в step summary →
  оператор выдаёт durable approval на этот exact digest → rerun того же
  commit → resume с неизменной digest pair → publication. Успешный live
  rerun/publication **не заявлен** (verification freeze не снят).

Approval/grant/list DTO сохраняют один exact six-field `QuotaSet`:
`ops_per_minute`, `max_rows`, `max_bytes`, `max_keys`,
`max_object_bytes`, `max_egress_bytes`. Quotas и egress не помещаются в
plugin token; runtime читает durable grant. Реальное `429 QUOTA_EXCEEDED`
всё ещё нужно доказать на metered adapter path.

### DeploymentAdmin ensure/update saga

Static path выполняет SecretProjection → Service → Deployment, связывает backend
image digest и bundle artifact digest и использует только `secretKeyRef`.
Новый Deployment создаётся с replicas 0; ensure существующего не сбрасывает
replicas. Update требует `expected_current_artifact_digest`; admin saga
содержит health confirmation, durable CAS и compensation/recovery branches.

Это не утверждение об атомарном live rollout. First-ever publication,
grant/update failure/replay, secret rotation и rollback обязаны получить
integration и live evidence.

### CI publication — static release path, live inputs остаются blockers

Release job читает `PLUGIN_PLATFORM_PUBLICATION_CONFIG` только из protected
GitHub environment `vars` (default в workflow — пустая строка), а не caller
`source_id`, capabilities, approval или self-asserted scan/CSP booleans. Config
содержит platform endpoint, pinned Trivy image и exact provisioned ModuleSource
bindings; source/OIDC/ref/workflow checks выполняются до registry writes.

**Схема конфига** (ровно эти ключи, `additionalProperties` запрещён — лишний
ключ даёт `PROTECTED_CONFIG_INVALID`):

Release-job объявлен с `environment: plugin-release`. Это **не** декорация:
именно этот environment держит protected-переменные (включая
`PLUGIN_PLATFORM_PUBLICATION_CONFIG`) и правила ревью, поэтому без него
job либо не стартует, либо видит пустые значения. Секретов workflow не требует
вовсе — единственный используемый `GITHUB_TOKEN` выдаётся рантаймом.

Все входы release-пути приходят из repository/environment `vars`, а не из
caller-параметров. Полный список — ровно эти десять имён; незаданная переменная
останавливает соответствующий шаг, а не подставляет default:

| Переменная | Зачем | Формат |
|---|---|---|
| `PLUGIN_PYTHON_BASE_IMAGE` | база backend-образа | `python:3.13-slim@sha256:<64 hex>` или GHCR mirror с тем же digest |
| `PLUGIN_LIQUIBASE_SHA256` | контрольная сумма архива Liquibase | `<64 hex>` |
| `PLUGIN_BUILDKIT_IMAGE` | driver buildx | `moby/buildkit:v<semver>@sha256:<64 hex>` |
| `PLUGIN_COSIGN_2_4_1_LINUX_AMD64_SHA256` | контрольная сумма cosign | `<64 hex>` |
| `PLUGIN_ORAS_1_2_3_LINUX_AMD64_SHA256` | контрольная сумма oras | `<64 hex>` |
| `PLUGIN_GRANTED_CAPABILITIES` | независимо проверенные grants | `<cap>,<cap>,…` |
| `PLATFORM_VERSION` | `CompatibilityGate` против `min_platform_version` | `<semver>` |
| `PLUGIN_PLATFORM_PUBLICATION_CONFIG` | protected publication mapping | JSON ниже |
| `PLUGIN_PLATFORM_REPOSITORY_ID` | ожидаемый id репозитория для OIDC-сверки | `<int>` |
| `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST` | опциональные HTTP-хосты для внутреннего dev | список URL, пусто в prod |

```json
{
  "schema_version": 1,
  "publication_base_url": "https://<platform-host>",
  "scanner_image": "aquasec/trivy:<major>.<minor>.<patch>@sha256:<64 lowercase hex>",
  "sources": [
    {
      "source_id": "<id модуля-источника>",
      "company_id": "<company_id>",
      "plugin_id": "<plugin_id>",
      "repository": "<owner>/<repo>",
      "repository_id": 123456789,
      "ref": "refs/heads/main",
      "trusted_workflow_ref": "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main",
      "static_origin": null
    }
  ]
}
```

`static_origin`, если он указан, принимает ровно `url_template`,
`plugin_shell_origin` и `security_headers`. **`null` НЕ пропускает CSP-пробу**:
`plugin_publication.py publish` fail-closed останавливается с
`CSP_EVIDENCE_REQUIRED` ещё до platform POST, поэтому пример с `null` выше —
только иллюстрация схемы, а не публикующая конфигурация. `scanner_image` обязан
иметь и точную версию, и
привязку по digest (`aquasec/trivy:<semver>@sha256:…`) — одно без другого
отвергается. `trusted_workflow_ref` — **только** канонический workflow платформы
с суффиксом `@<40-hex-sha>` или `@refs/heads/<branch>`, причём для direct push
ветка обязана совпасть с `ref`.
Это описание **статической схемы**, а не evidence выполненной публикации.

Backend дополнительно связывает OIDC `event_name` с формой workflow identity:

- `push` и `workflow_dispatch` допускают **только direct protected-branch**
  identity `...@refs/heads/<protected-default-branch>`; для текущего canonical
  source это ровно `...@refs/heads/main`, совпадающее с expected source `ref`.
- `workflow_call` допускает **только reusable workflow, pinned на полный
  lowercase 40-hex commit SHA**: `...@<40 lowercase hex>`.

Неправильные комбинации (`push`/`workflow_dispatch` с SHA и `workflow_call`
с branch identity) backend отклоняет как `INVALID_CLAIM` с claim
`job_workflow_ref` даже при валидной GitHub-подписи. Exact bindings
`repository`, `repository_id`, `ref`, `sha`, `ref_protected="true"` и
signer identity/issuer остаются обязательными и не ослабляются этой матрицей.

В статической схеме миграция `2026111201` и ORM используют одинаковый workflow
CHECK для `plugin_module_sources` и `plugin_source_reservations`. Operational
следствие fail-closed DDL: upgrade валидирует существующие строки и на
noncanonical rows обязан прерваться без silent rewrite/backfill; downgrade при
наличии direct `refs/heads/main` rows также обязан прерваться, поскольку
восстанавливаемый старый CHECK принимает только full SHA. Это контракт миграции,
а не утверждение о выполненных live upgrade/downgrade.

Promotion выполняется строго по digest refs и состоит из двух дополняющих
копирований: `oras copy --recursive` переносит primary manifest и native OCI
referrers, а `cosign copy --only=sig,att` — tag-based `.sig`/`.att` манифесты
подписей и attestations, которые ORAS recursive не покрывает. После обоих
копирований digest resolve checks подтверждают идентичность release refs, и
отдельный `release_verify` обязательно повторно проверяет actual release
refs/referrers — promotion без reverify не считается завершённым.

Evidence-first порядок (**B4**, statically implemented в scripts и
integrated в `plugin-artifacts.yml`; **UNVERIFIED live**, успешный CI run не
заявлен):

1. `plugin_release_evidence.py` выполняется ПОСЛЕ `quarantine_verify` и ДО
   любого release GHCR write/promote: pinned Trivy сканирует exact
   **quarantine backend digest**, protected static-origin CSP-проба проверяет
   exact bundle digest (configured headers и
   `X-AgentScale-Bundle-Digest: sha256:<bundle>`). Bounded JSON snapshot
   фиксирует exact quarantine+release ref/digest pairs, scanner identity,
   scan report digest и проверенные CSP facts. Шаг не запрашивает OIDC, не
   делает platform POST и не выполняет registry write/promotion.
2. Promotion по digest (два копирования выше) и обязательный `release_verify`.
3. `plugin_publication.py publish --release-evidence` повторно валидирует
   snapshot после release verify и **не повторяет scanner/CSP** перед POST:
   GitHub OIDC token и authenticated `POST /internal/plugin-artifacts`
   выполняются только при валидных snapshot/decision/manifest, с проверкой
   exact matching platform PUBLISHED response.

**B4 integration — локально выполнена, UNVERIFIED:** в `plugin-artifacts.yml`
отдельный шаг `Verify independent release evidence before promotion` стоит
после `quarantine_verify` и до `id: promote`; publish-шаг сохраняет позицию
после `release_verify` и передаёт `--release-evidence`, повторно валидируя
snapshot без второго scanner/CSP. Статический guard
`IndependentEvidenceBeforeReleaseWriteTests`
(`apps/api/tests/unit/plugins/test_ci_plugin_publication.py`) больше не
намеренно RED: ordering фиксирует evidence ДО promote, а no-write guard
ограничен рамками самого evidence-шага (последующий promote пишет).
Fail-closed семантика не ослабляется: scan findings/CSP mismatch = quarantine
без platform POST. Успешный CI run и live publication по-прежнему не
подтверждены.

Форматы evidence различаются по происхождению: backend SBOM создаётся нативно
BuildKit/Syft, и verifier принимает его как SPDX-2.2 (допустим также SPDX-2.3);
bundle SBOM/provenance генерирует сам trusted builder, и bundle SBOM остаётся
строго SPDX-2.3.

**Operator blocker — static origin (B1, remains open).** HEAD-проба требует HTTP 200, точное
совпадение configured security headers/CSP и заголовок
`X-AgentScale-Bundle-Digest: sha256:<release-bundle>` для exact release bundle.
Поэтому exact bundle обязан быть доставлен на static origin ДО platform POST.
Текущий workflow такой доставки НЕ выполняет: шагов upload/sync на static
origin в нём нет, и конкретный реальный origin здесь не указывается. Без
предварительной доставки и provisioned origin конфигурации публикация
остановится fail-closed (`CSP_EVIDENCE_REQUIRED`, `CSP_ORIGIN_HTTP_ERROR`,
`CSP_SECURITY_HEADERS_FAILED` или `CSP_ARTIFACT_BINDING_MISMATCH`).

**B1 (evidence-only) реализован offline:** ниже — «Static-origin evidence —
cluster-internal `plugin-static`»: отдельный cluster-internal Service с
digest-verified readiness route, манифесты и dev allowlist в CI script.
**Live blocker сохраняется:** deployment/config (CI Images rebuild, Argo
sync dev, SealedSecret, ConfigMap, protected `static_origin` + dev
allowlist) не выполнены, evidence probe не запускался, успешный CI run не
заявлен. Sanitized config plan и evidence-контракт:
[STATIC-ORIGIN-EVIDENCE.md](../reports/module-audit/STATIC-ORIGIN-EVIDENCE.md).

Это порядок в текущих workflow/scripts, **не evidence выполненного release**.
Protected publication config/source mapping, scanner digest, static-origin
credentials/endpoint и остальные live inputs для аудита не предоставлены;
missing/invalid input останавливает path до POST и остаётся STOP-SHIP blocker.

### Static-origin evidence — cluster-internal `plugin-static` (B1: implemented offline, live blocked by deployment/config)

Это **Scope B1**: снять operator blocker только для CI evidence
(static-origin CSP/digest probe), а **не** предоставить public browser
static hosting. As-built в worktree: ASGI `apps/api/src/prodavan/main_plugin_static.py`
(`prodavan.main_plugin_static:app`) + `PluginStaticEvidenceService`
(`application/plugins/static_origin/service.py`), unit tests
`apps/api/tests/unit/plugins/static_origin/`, манифесты
`infra/k3s/base/plugins/plugin-static.yaml` и dev-only NetworkPolicy patch
`infra/k3s/overlays/dev/plugins/patch-plugin-static-runner-ingress.yaml`,
регрессии `infra/ops/tests/test_plugin_static_manifest.py`. Verification
(offline, 2026-11-12): full backend plugin suite **4251 passed / 1 skipped**,
static_origin **85 passed** (включая 4 builder-entry bridge регрессии),
infra/ops **156 passed**, scoped ruff passed,
stdlib CI suites зелёные. **Live unverified:** Service не задеплоен,
SealedSecret/ConfigMap/protected config не заполнены, evidence probe не
выполнялся. B1 = offline implemented + live blocked by deployment/config;
STOP-SHIP сохраняется.

**Контур и поверхность (as-built):**

- Отдельный Deployment/Service `plugin-static` в namespace `agentscale-dev`,
  ClusterIP `:8081` (NodePort/LoadBalancer запрещены regression-тестом).
  **Без Ingress**, без публичного DNS: адрес доступен только внутри кластера
  и с dev CI runner VM (NetworkPolicy ниже).
- **Read-only API:** только `GET`/`HEAD`; любой другой метод — `405`.
  Write API (`POST /internal/plugin-artifacts`, upload/sync/delete) на этом
  Service отсутствует по дизайну и не проксируется.
- **Нет tenant-доступа:** `automountServiceAccountToken: false`, RBAC не
  выдаётся; tenant MinIO/DB credentials, `TENANT_DB_DSN`, SecretProjection и
  capability tokens отсутствуют. Единственный credential — GHCR read-only
  `registry_read` (GitHub App installation token, `packages:read` +
  `metadata:read`) через `GhcrRegistryAuthorizer`; приватный ключ — только
  `k8s-secret://` REF + read-only mount SealedSecret'а, токены не
  логируются.
- **NetworkPolicy `plugin-static-egress`** (podSelector `app: plugin-static`):
  ingress только от подов `plugin-gateway`/`agentscale-api` на TCP 8081;
  dev-only JSON6902 patch добавляет ровно один peer — CI runner VM
  `172.31.156.203/32` на TCP 8081, base-правила не заменяются. Egress:
  kube-dns (UDP/TCP 53) и внешний TCP 443 с except pod/service CIDR
  (`10.42.0.0/16`, `10.43.0.0/16`) для api.github.com + ghcr.io; порт 80,
  БД/MinIO/Kubernetes API и внутренние Service'ы fail-closed.

**Readiness route (as-built contract v1):**

```
http://plugin-static.agentscale-dev.svc.cluster.local:8081/internal/plugin-static/v1/bundles/{plugin_id}/{version}/{bundle_digest}/ready
```

- `bundle_digest` — canonical `sha256:<64 lowercase hex>`; exact digest
  обязан быть в path (требование `StaticOriginConfig.url_for` и route
  `STATIC_EVIDENCE_READY_PATH`).
- **Реальная digest-верификация, не «ready по headers»:**
  `PluginStaticEvidenceService` перепроверяет полный OCI manifest contract
  (schema/mediaType/artifactType, аннотации `plugin_id`/`manifest_version`,
  config descriptor, ровно один `frontend-dist` layer), canonical config JSON
  bytes и frontend `tar.gz` (только iterate members, `extractall` запрещён).
  Fetch — `HttpxOciArtifactLoader` (`allowed_registries=("ghcr.io",)`,
  `trust_env=False`, redirects запрещены, bounded caps) с
  `sha256(raw bytes) == {bundle_digest}` и digest+size проверками blobs.
- **Builder↔service entry contract:** новые CI manifests эмитят
  `agentscale.plugin.frontend.entry` (`artifacts/layers.py`); service
  совместим с legacy manifests без annotation — entry подтверждается
  digest-verified canonical config JSON, mismatch отклоняется. Bridge
  regressions `static_origin/test_builder_entry_binding.py` (реальный
  `build_bundle_manifest` → ready, legacy accepted, mismatch rejected).
- **Quarantine→release fallback:** exact
  `ghcr.io/<org>/plugins-quarantine/<plugin_id>/bundle@{bundle_digest}`;
  release `ghcr.io/<org>/plugins/<plugin_id>/bundle@{bundle_digest}` —
  **только после доказанного registry 404** в quarantine. Любой другой сбой
  (transport/auth/unexpected) = typed sanitized `503 EVIDENCE_UNAVAILABLE`
  без fallback/retries; digest mismatch/contract failure/proven double-404 =
  typed `404 BUNDLE_NOT_READY`; caps exceeded = typed `413 BUNDLE_TOO_LARGE`.
- Ответ `200` только после верификации: `HEAD` — пустое тело +
  `StaticResponsePolicy` headers; `GET` — minimal sanitized JSON metadata
  (без blobs/secrets); прочие методы `405`. Клиентские `X-AgentScale-*` не
  принимаются как evidence; CI `validate_csp_response` fail-closed по
  `status != 200`.
- Verified results — bounded in-memory LRU с per-digest singleflight и
  коротким negative cache (503 не кэшируется); disk cache и blob storage
  отсутствуют. `/health/ready` — configured-gate (`200` только при composed
  loader+policy, иначе typed `503`), `/health/live` — дешёвая liveness;
  пробы в GHCR не ходят. Под без trusted GitHub/GHCR конфигурации стартует
  явно unconfigured: liveness up, readiness/evidence fail-closed `503`.

**Response headers — ровно `StaticResponsePolicy` (server-owned):**

Headers строятся через
`StaticResponsePolicy(shell_origin=<PLUGIN_SHELL_ORIGIN>).response_headers(artifact_digest=<bundle_digest>)`,
а не из request или manifest:

| Header | Значение |
|---|---|
| `X-AgentScale-Bundle-Digest` | `sha256:<bundle_digest>` из path после verify |
| `Content-Security-Policy` | `default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors <plugin_shell_origin>` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |

`plugin_shell_origin` — exact canonical HTTPS origin (`exact_https_origin`),
совпадающий с API `PLUGIN_SHELL_ORIGIN` (server-derived, не из request Host
или `window.location`; dev-значение в tests — `https://agentscale.dev`).
Static и shell origins обязаны различаться (`exact_plugin_origins`);
wildcard и implicit `'self'` в `frame-ancestors` запрещены.
`security_headers` в protected config — lowercase-ключи, байт-в-байт как у
`_static_security_headers(shell_origin)` в `plugin_publication.py`: ровно
четыре ключа `content-security-policy`, `x-content-type-options`,
`referrer-policy`, `permissions-policy`;
`X-AgentScale-Bundle-Digest` проверяется отдельно.

**CI evidence usage (target):** `plugin_release_evidence.py` продолжает делать
HEAD по `static_origin.url_for(...)`; readiness route заменяет «голый» file
hosting metadata-ответом, поэтому CSP/digest evidence дополнительно
подтверждает фактическую проверку OCI manifest + frontend `tar.gz`. Scope
строго evidence: browser iframe static hosting, SPA/entry serving и
`GatewayProxy.forward_static` в этот план не входят и остаются live
unverified.

**Deployment/config prerequisites (live blocked, НЕ выполнены):**

1. **CI Images rebuild** `ghcr.io/ne-tort/agentscale-api:latest` с entrypoint
   `prodavan.main_plugin_static` (`imagePullPolicy: Always`): без свежего
   образа под не найдёт модуль. Доставка только merge в main → CI Images.
2. **Argo CD sync dev** (Application `plugin-platform` →
   `infra/k3s/overlays/dev/plugins`): подключает base `plugin-static.yaml`
   и dev NetworkPolicy patch. Ручной `kubectl apply` не используется.
3. **SealedSecret `plugin-module-source-github-app`** (dev): Opaque Secret с
   единственным key `private-key` = PEM приватного ключа GitHub App.
   Оператор запечатывает его kubeseal'ом (dev controller cert,
   namespace/name scope), коммитит только
   `plugin-module-source-github-app.sealed.yaml` и подключает его в
   kustomization; `.example` в resources не входит. Plaintext/base64 PEM,
   PAT и installation token в Git запрещены; значения в этот документ не
   включались и не читались.
4. **ConfigMap `plugin-static-config` (dev patch)**: managed installation
   config для `managed_config_from_settings`/`github_app_client_from_settings`
   — `PLUGIN_MODULE_SOURCE_GITHUB_APP_ID`,
   `PLUGIN_MODULE_SOURCE_GITHUB_APP_INSTALLATION_ID` и trusted
   template/workflow/branch-policy values. `PLUGIN_MODULE_SOURCE_ENABLED`
   остаётся `false`, `PLUGIN_PLATFORM_ENABLED` — `false` (emergency
   disabled). Без полной установки readiness/evidence = typed `503`, не
   anonymous GHCR.
5. **`PLUGIN_PLATFORM_PUBLICATION_CONFIG`**: только
   `sources[0].static_origin` = `url_template` v1 ready route +
   `plugin_shell_origin` (exact HTTPS, совпадает с ConfigMap
   `PLUGIN_SHELL_ORIGIN: https://agentscale.dev`) + `security_headers`
   (ровно 4 ключа `_static_security_headers(shell_origin)`). Остальные ключи
   и `publication_base_url` не меняются. План применения без печати значений
   — [STATIC-ORIGIN-EVIDENCE.md](../reports/module-audit/STATIC-ORIGIN-EVIDENCE.md)
   §3; `gh variable set` здесь НЕ выполнялся.
6. **`PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`**: exact origin
   `http://plugin-static.agentscale-dev.svc.cluster.local:8081` (dev only;
   prod пусто). Без allowlist static-origin `http://` отклоняется
   `CSP_CONFIG_INVALID`.
7. **Rollback:** удалить sealed-файл и ConfigMap/NP patches из
   kustomization → Argo sync возвращает прежнее состояние; откат protected
   config на прежнее значение = fail-closed `CSP_EVIDENCE_REQUIRED` до
   platform POST, частичной активации нет; удаление runner patch вновь
   закрывает доступ VM. Деструктивных DB-операций нет — контур без БД.

**Dev HTTP allowlist для static-origin (implemented offline, UNVERIFIED
live):** `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST` — JSON-список exact `http://`
origins (≤32, без path/query/credentials/wildcards; wildcard entries
отклоняются `DEV_ALLOWLIST_INVALID`). Сейчас в `plugin_publication.py`
static-origin использует тот же allowlist, что и `publication_base_url`:
`StaticOriginConfig.parse` принимает optional `dev_allowlist`,
`SourceBinding.parse` пробрасывает его, а `PublicationConfig.from_environment`
передаёт существующий `_parse_dev_allowlist`. HTTPS поведение не изменено.
`http://` в template probe и в formatted `url_for` разрешён **только** при
exact origin membership `http://host[:port]` в allowlist — проверяются оба
URL (`_static_origin_url_allowed`), поэтому подстановка fields не может
увести probe на другой origin. Userinfo/query/fragment/другие схемы
отклоняются `CSP_CONFIG_INVALID`/`CSP_ORIGIN_INVALID`.
`plugin_shell_origin` всегда остаётся HTTPS. Prod: allowlist пуст, только
HTTPS. Regressions в `test_ci_plugin_publication.py`
(`StaticOriginDevHttpAllowlistTests`): focused offline runs 71/71
(publication) и 25/25 (release evidence) passed на Python 3.13.3; live
Service/probe не проверены. Sanitized config plan — в
[STATIC-ORIGIN-EVIDENCE.md](../reports/module-audit/STATIC-ORIGIN-EVIDENCE.md).

### Production composition tests — написаны, не выполнены

`test_production_composition.py` и `test_publication_grant_saga.py` описывают
cold `create_app` с production factories и stateful doubles только на IO
boundaries: shared verifier/GitHub lifecycle owner, durable approval/grant,
AsyncpgAdmin, SecretProjection, exact quotas, first-ever publication, failed
Deployment create, retry/replay и private/public DTO. Они также содержат guards
против unwired/stub/in-memory required ports.

Наличие этих tests — важный static implementation artifact, но **не test result,
не real adapter registry evidence и не live acceptance**.

## 10. Локальные contract tests автора

Пишите локальные проверки **до передачи source в trusted CI**. Во время
verification freeze этот репозиторий их не запускает; наличие тестов не отмечает
acceptance.

Trusted CI дополнительно содержит семь offline stdlib-модулей в
`apps/api/tests/unit/plugins/`: `test_ci_plugin_supply_chain.py`,
`test_ci_plugin_publication.py`, `test_ci_plugin_network.py`,
`test_ci_plugin_archive_safety.py`, `test_ci_plugin_promotion.py`,
`test_ci_plugin_resume.py` и `test_ci_plugin_release_evidence.py`; dry-run
workflow перечисляет их с безопасным `[[ -f ]]` guard для historical trees.
Они проверяют статические контракты workflow/scripts (supply chain, publication
gate, bounded network operations, archive safety, quarantine→release promotion),
но **freeze не снят**: модули написаны и не выполнялись, их наличие не является
test result или acceptance.

| Контракт | Минимальная проверка после явного снятия freeze |
|---|---|
| Manifest | JSON Schema validation 11 required fields и optional `rpc_operations`; unknown/self-reference/embedded egress, duplicate operation/path отклоняются |
| RPC | Consumer-driven fixtures canonical `plugin-rpc/1`; origin/source/nonce/digest negative cases; hello list/greet exact binding |
| Public | Anonymous exact page + unique GET/HEAD empty-body only; POST/non-public denied без backend forward/token |
| OCI pair | Backend и bundle имеют независимые immutable digests, одну запись совместимости и проверяемые signature/provenance/SBOM; base image digest и полный dependency lock/hashes проверены |
| Publication | First-ever empty artifact/grant state, exact approval, denial/quarantine, failure/retry/replay без частичной активации |
| Grant/update | Exact all-six quotas, initial/replay/update, health/CAS/compensation и digest binding |
| Secrets | Exact four keys, ownership/drift/CAS, DSN/webhook rotation без plaintext logs |
| Migrations | Fresh apply, historical `2026110901 → 2026110909`, repeat, checksum failure → не Ready, schema-only downgrade без данных |
| Tenant isolation | Чужая БД/схема/bucket/prefix недоступны; client-supplied tenant ids не меняют SQL scope |
| Identity | Отсутствие/просроченный/чужой/revoked token отклоняется; static env token не читается; scoped TokenRequest refresh/shutdown проверен отдельно |
| Quotas/adapters | Real nine-adapter registry без stubs/unwired ports; metered request выдаёт `429 QUOTA_EXCEEDED` |
| Health | До успешной миграции и проверки БД readiness не наступает |
| Sandbox | Нет `allow-same-origin`, token в URL или wildcard trust; CSP присутствует |
| Webhook | Неверная подпись/replay отклоняются; сквозной Gateway→backend verified-header forwarding проверяется отдельно |
| Legacy cleanup | Import-graph guards и browser regression подтверждают removed hosts и current plugin path; это не DB cutover acceptance |
| Rollback | Предыдущая digest pair совместима со схемой; code rollback не вызывает destructive DB downgrade |
| Security | Non-root/read-only/no privilege escalation, resource limits и default-deny network проверяются статически и live |

## 10a. Runtime bootstrap — statically implemented, live unverified

Для employee/public runtime есть отдельные endpoints:

| Метод и путь | Назначение |
|---|---|
| `GET /api/v1/companies/{company_id}/plugins/{plugin_id}/bootstrap?route_path=/` | Приватный bootstrap: membership, активная компания, активный grant, enabled-флаг и опубликованный артефакт |
| `GET /api/v1/public/plugins/{company_id}/{plugin_id}/bootstrap?route_path=/` | Публичный bootstrap: только явно публичные страницы/операции манифеста |

Backend возвращает только public-safe DTO: `plugin_id`, `manifest_version`,
`artifact_digest`, `static_origin`, `plugin_shell_origin`, `frontend_entry/base_path`,
`route_path`, `capabilities`, `rpc_operations`, `public_pages`, `scope`,
`grant_expires_at`. Токены, DSN и admin receipts не отдаются. `static_origin` и
`plugin_shell_origin` — два разных server-derived origin'а: iframe грузится с
static, а handoff/`frame-ancestors` привязаны к shell; они обязаны различаться
(`exact_plugin_origins`). Дополнительно
проверяются соответствие manifest идентичности grant, активность компании и
совпадение digest артефакта.

Web-клиент (`api/client.ts`) сначала использует эти endpoints; при их отсутствии
остаётся legacy fallback `GET /api/v1/admin/plugins/?company_id=...` с прежним
platform-admin gate (обратная совместимость, а не новый способ обхода
авторизации). DTO не заменяет Gateway enforcement public GET-only.

## 11. Что пока делать нельзя

- Не использовать этот документ или hello-plugin как работающий quickstart.
- Не включать plugin platform и не запускать install/deploy в shared dev/prod.
- Не рекламировать все девять adapters, GitHub App, self-service или RPC как
  live-ready только потому, что их код и composition tests существуют.
- Не перемещать pending `2026110802`, не выполнять `removal.py`, не удалять
  migration-only helpers; см. [безопасный cutover](migration/plugin-cutover-safety.md).
- Не удалять preserved dynamic renderer/inventory после completed host cleanup;
  сначала выполнить guards и отдельный reviewed cutover.
- Не объявлять локальные contract/integration tests заменой live acceptance.
