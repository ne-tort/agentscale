# AGENTS.md — безопасная заготовка agent-authored модуля

Этот файл обязателен для агента, который развивает `hello-plugin` или копирует
его как заготовку. Hello — НЕ production-ready пример и НЕ доказательство того,
что plugin platform, все capabilities или интеграция с GitHub уже работают.
Сначала читай `backend/CONTRACT.md`, canonical schemas и фактический код обеих
сторон стыка. Target design не выдавай за as-built.

## 1. Границы владения и trusted workflow

- Код модуля ограничен его каталогом `packages/plugins/<module>/` и его
  собственными unit/template tests. Не переиспользуй чужие tenant-данные и не
  меняй соседние модули.
- Shared JSON Schema/TS/Python contracts, Plugin Gateway и capability adapters,
  admin/API, registry/signing, k8s/GitOps manifests, CI workflows и security
  policy — trusted platform workflow. Их изменение требует отдельной задачи и
  **platform-owner review**; нельзя «подстроить» доверенную сторону под модуль.
- Не делай direct push в default branch. Не включай platform/prod/live и не
  выполняй prod-промоцию как побочный эффект работы над модулем.
- Не добавляй секреты, Git tokens, `.env`, credentials в remote URL, pod/repo
  logs, fixtures, exceptions, access logs или OCI labels/layers. Не печатай DSN,
  Authorization, capability token, webhook secret, подписанные header sets или
  тела запросов. Логи должны содержать только allowlisted non-secret metadata.
- Не обещай GitHub App, token minting, webhook delivery или другую интеграцию,
  пока она не реализована и не проверена на обоих концах. Наличие имени в
  manifest, env или target-документе не является рабочей capability.

## 2. Capability introspection — на каждый request

- Backend читает ровно четыре runtime значения из platform-managed per-instance
  Secret через `secretKeyRef`: `GATEWAY_URL`, `PLUGIN_ID`, `TENANT_DB_DSN`,
  `WEBHOOK_SECRET`. Не вводи `PLUGIN_WEBHOOK_SECRET` или static capability token.
- Единственный backend trust input — короткоживущий
  `X-Plugin-Capability-Token` текущего запроса. Для каждого API request вызывай
  protected `GET {GATEWAY_URL}/capabilities/v1/health` по контракту из
  `backend/CONTRACT.md`: short timeout, `trust_env=False`,
  `follow_redirects=False`, строгая схема ответа, fail-closed mapping ошибок.
- Успешный health response обязан содержать ровно восемь полей: `plugin_id`,
  `company_id`, `cabinet_id`, `project_id`, `grant_id`, `artifact_digest`,
  `scopes`, `exp`. Поля `status`/`surface` и любые другие дополнительные поля
  не принимаются. Эта strict eight-field схема и соответствующий protected
  `plugin_health` statically implemented; их сквозная совместимость остаётся
  **live unverified**.
- Claims не кэшируй между запросами. Не вводи static `PLUGIN_CLAIMS`,
  `PLUGIN_CLAIMS_RAW`, shared static token или local JWT/base64 verification.
  Модуль не получает `PLUGIN_CLAIMS_SECRET`: подпись и revocation проверяет
  protected Gateway surface.
- Authorization, identity headers и поля `grant.ready` от browser — не
  самостоятельное доказательство для backend authorization.

## 3. Tenant scope и DSN

- Доверенные `company_id/cabinet_id/project_id/plugin_id/grant_id/artifact_digest/scopes/exp`
  бери только из успешной per-request introspection. Client query/body/path
  значения — лишь assertions на совпадение; несовпадение = 403. SQL selectors и
  ключи идемпотентности строятся из introspected claims, не из client input.
- `TENANT_DB_DSN` разрешает только собственную схему модуля в tenant DB.
  Запрещены platform/admin DSN, чтение platform tables, CREATE ROLE/DATABASE,
  смена `search_path` по пользовательскому значению и cross-tenant queries.
  DSN передавай дочернему процессу через Secret/env, не через argv или logs.
- Таблицы должны иметь tenant-scoped constraints/indexes; все SQL параметризуй.
  Не принимай чужой `company_id` как способ выбрать другую базу или схему.

## 4. Миграции, Ready и rollback

- Порядок старта неизменен: self-managed миграции собственной схемы -> успешный
  `liquibase update` -> `LIQUIBASE_UPDATE_OK=1` -> uvicorn -> Ready. Ошибка
  миграции/checksum обязана завершать контейнер до открытия Ready.
- Не запускай autogenerate или ad-hoc DDL при старте. Применяются только
  reviewed, versioned changelog changesets; shell execution в changelog запрещён.
- Rollback кода НЕ равен rollback БД. Старый код должен быть совместим с уже
  применённой схемой; исправление схемы — отдельная forward migration с backup,
  data-compatibility и rollout plan. Не добавляй автоматический destructive
  downgrade/drop в entrypoint или deployment.
- Сохраняй non-root `10001:10001`, `readOnlyRootFilesystem`, `/tmp` как
  emptyDir, root-owned non-writable app files и healthcheck. Runtime не должен
  получить shell/download tools сверх необходимого entrypoint/JRE набора.

## 5. Canonical plugin-rpc и typed operations

- Browser transport — только canonical `plugin-rpc/1` и его schemas:
  `host.ready` -> `plugin.ready`, `grant.ready`,
  `plugin.rpc.request/response`, `plugin.error`. Проверяй exact protocol,
  `event.origin`, `event.source`, nonce, instance_id, plugin_id и artifact
  digest на каждом сообщении. Не изобретай параллельный postMessage protocol,
  token-in-URL или wildcard origin.
- Backend operations описываются typed `rpc_operations`
  (`RpcOperationDeclaration`): capability id + `major.minor`, operation,
  GET/POST, unique `/api/...` backend_path и explicit `public`; private UI
  не означает public page.
- Текущий hello manifest объявляет ровно две operations canonical capability
  `hello.greetings@1.0`: `list` — `GET /api/greetings/list`, `public=false`;
  `greet` — `POST /api/greetings`, `public=false`. Ключ
  `(capability_id, capability_version, operation)` и каждый `backend_path`
  уникальны. `/api/stats` не объявлен и не является broker-разрешённой operation.
- **Statically implemented / live unverified:** manifest-scoped broker разрешает
  только operations, объявленные в подписанном manifest. Это статический контракт
  текущего worktree, а не доказательство готового browser → Gateway → backend
  end-to-end сценария или пройденного live acceptance.
- Массив `capabilities` — requested/target declaration, а не список реально
  поддерживаемых адаптеров. В документации модуля разделяй supported (есть код,
  контракт обеих сторон и evidence), unavailable и target. Не используй
  `grant.ready.capabilities` как подтверждение server-side adapter support.

## 6. Webhook HMAC и идемпотентность

- Текущая совместимая форма — `X-Prodavan-Signature:
  sha256=<hex HMAC-SHA256(WEBHOOK_SECRET, raw_body)>`, сравнение через
  `hmac.compare_digest`. Подписываются именно неизменённые raw bytes.
- Не добавляй timestamp/delivery/event/method/path в signed bytes односторонне.
  Расширение требует совместного версионирования Gateway+plugin, canonical
  serialization, timestamp tolerance и replay policy.
- В текущем statically implemented Gateway path после успешной GatewayAuth
  `build_forward_headers` восстанавливает signature/event/delivery headers из
  `VerifiedWebhook` вместе с raw body; прежнее утверждение, что sanitize всегда
  теряет signature перед forward, устарело. Gateway → hello delivery остаётся
  **live unverified** и требует отдельной integration/live acceptance-проверки,
  включая secret rotation и отсутствие duplicate side effects.
- In-memory `_seen_deliveries` — только demo одного процесса, не durable и не
  cross-replica. Production требует атомарной durable delivery record, tenant/
  plugin key, body digest, TTL/retention и повторной обработки ошибок.

## 7. Bounded payloads и ресурсы

- Каждый HTTP/RPC/webhook input и output должен иметь явный byte limit. Проверка
  Content-Length до чтения обязательна, но недостаточна: читай stream не более
  `limit + 1`, отклоняй chunked/ложный length и oversized body до JSON parse
  или HMAC buffers. Согласуй module limit с меньшим Gateway limit.
- Ограничивай JSON depth/node count/array size/string size, page size, response
  bytes, concurrency, DB pool/query time и outbound timeout. Не возвращай
  неограниченные SELECT и не пересылай большие payloads через postMessage.
- Текущий hello не доказывает все эти ограничения: до включения новых endpoints
  добавь собственные bounded readers/validators и tests, не полагаясь только на
  внешний proxy. Отсутствие лимита — blocker, а не implicit unlimited mode.

## 8. Воспроизводимая сборка: текущий fail-closed blocker

- `PYTHON_BASE_IMAGE` обязан быть digest-pinned reference: либо
  `python:3.13-slim@sha256:<64 lowercase hex>`, либо content-identical mirror
  `ghcr.io/ne-tort/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90`.
  Mirror создан через `oras copy --recursive` из Docker Hub; digest совпадает
  (проверено `imagetools inspect`). Пустое значение, tag-only или другая база
  запрещены. Debian sources переводятся на HTTPS перед `apt-get update`,
  потому что VM может достать CDN по HTTPS, но не по HTTP.
  `LIQUIBASE_VERSION` закреплена на `4.24.0`, а `LIQUIBASE_SHA256` обязан
  совпасть с скачанным tar.gz до распаковки.
  Наблюдённое значение для `v4.24.0` (два независимых скачивания дали одинаковый
  hash): `6ecf638a75b501b798189da824e167f29db93477186109f2549508c6266d0b2a`.
  Официального `*.sha256` в релизе **нет**, поэтому это проверенная
  воспроизводимостью величина, а не подпись вендора; перед включением CI её
  должен подтвердить владелец supply-chain.
- Установка из `requirements.txt` запрещена. Dockerfile требует полный
  `backend/requirements.lock` и вызывает pip с `--require-hashes --no-deps`.
  Lock **есть** и сгенерирован из реального индекса, а не собран из
  `infra/ops/poetry.lock` (тот содержит лишь часть transitive packages):
  `uv pip compile requirements.txt --python-version 3.13
  --python-platform x86_64-manylinux_2_28 --generate-hashes`. Полный граф —
  22 пакета, 658 хешей; проверено, что `--require-hashes --no-deps`
  разрешается полностью. Это статическое разрешение, а не доказательство
  сборки: CI всё ещё требует базовый образ, liquibase и семь repo-переменных.
- Для разблокировки CI нужны verified inputs: (1) проверенный multi-arch digest
  `python:3.13-slim`; (2) независимо подтверждённый SHA256 официального
  `liquibase-4.24.0.tar.gz`; (3) reviewed lock полного direct+transitive graph
  для CPython 3.13/Linux direct dependencies `fastapi==0.115.6`,
  `uvicorn[standard]==0.34.0`, `asyncpg==0.30.0`, `httpx==0.28.1`, включая
  extras, environment markers, exact versions и SHA256 всех допустимых wheel/
  sdist artifacts. Нельзя выдумывать digest/hashes или сшивать несовместимые
  версии из частичных lockfiles. После получения inputs нужен отдельный reviewed
  build/test/supply-chain verification; само наличие файлов не доказывает build.

## 9. Jobs и MCP — текущие static contracts, live unverified

- `PLUGIN_JOB_DECLARATIONS` — server-owned JSON только с exact verified selectors
  `plugin_id/version/artifact_digest`. Не принимай от plugin request task name,
  backend path/digest или handler. Signed operations исполняются через scoped
  `ManifestJobTasks` с grant/instance/artifact/backend revalidation; API получает
  install-created registry через typed hook, standalone worker — через deferred
  factory с отдельным resource owner. Worker execution не подтверждён live.
- `mcp_servers` — signed data-only declarations, не grant и не transport. Только
  пересечение manifest и digest-scoped durable registration доступно discovery.
  `mcp.invoke` идёт через `PluginCapabilityService`; broker вызывает unique
  canonical `rpc_operations`, а не remap в другую infra capability. JSON-RPC
  mount `POST /capabilities/v1/mcp/servers/{server_id}` существует статически,
  но live client/backend flow, revoke, quota/audit не приняты.
- Hello объявляет MCP tools `list`/`greet` версии `1.0.0`; не добавляй tool,
  который не имеет ровно одной соответствующей canonical RPC declaration.

## 10. Scoped runtime, pods и secret delivery

- Фактические `plugin_instance_id` и `backend_digest` читаются только из
  platform-managed read-only projection `/etc/agentscale/plugin-runtime`
  (`downwardAPI` pod annotations). Header/body assertions без этой projection
  не доказывают deployment identity. Service proxy использует fresh wake и ровно
  fixed capability/instance/plugin/artifact headers.
- Не заменяй production declaration/artifact/pod-action/secret providers
  module-local doubles: текущие hello providers намеренно unavailable и дают 503.
  Это fail-closed static contract, а не runtime readiness.
- `pods.allowlist` допускает только exact
  `{plugin_instance_id, project_id, job_id}`; omitted `pods` не даёт access.
  Hello такое поле не объявляет.
- `secret_delivery` содержит только `mode: k8s_secret` и canonical
  `vault://`/`file://` refs, без Secret names/data/plaintext. Delivery Secret —
  отдельный owned per-instance ресурс с hashed keys, value versions, CAS/exact-set
  reconcile, read-only `0400` mount и revoke/delete lifecycle. Не смешивай его с
  four-key runtime Secret. Hello `secret_delivery` не объявляет.
- Для `objects.scoped_credentials` STS остаётся typed-unwired до enabled +
  отдельного post-probe availability. Не подменяй prefix-wide operation presign
  или static/admin credentials; наличие presign-кода elsewhere не открывает её.

## 11. Events signing secret

- `PLUGIN_EVENT_SIGNING_SECRET_REF` — typed `vault://`, `file://` или
  `k8s-secret://<name>/<key>` reference, не plaintext. Enabled platform требует
  его resolution; `PLUGIN_CLAIMS_SECRET`/`AUTH_TEST_SECRET` не fallback.
- Dev sealed-example и read-only `0400` mounts для API/Gateway — static wiring,
  не live key evidence. `optional: true` разрешён только для disabled platform;
  enabled+missing key обязан останавливать startup. Не добавляй example в
  kustomization resources и не создавай собственный event key в module env.

## 12. Trusted CI publication — не quickstart

- Release использует protected `PLUGIN_PLATFORM_PUBLICATION_CONFIG`, pinned
  Trivy evidence для actual release backend и static-origin CSP/security headers
  с exact `X-AgentScale-Bundle-Digest`. Только после release reverify и этих
  независимых evidence выполняется OIDC `POST /internal/plugin-artifacts`.
- Не принимай source/capabilities/approval/scan/CSP assertions от модуля или
  caller input. Protected live inputs и successful run отсутствуют в текущих
  evidence и остаются blockers; наличие workflow/scripts не означает PUBLISHED,
  install или live readiness.
