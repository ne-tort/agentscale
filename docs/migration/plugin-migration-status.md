# Состояние plugin-миграции — STOP-SHIP

**Дата документной сводки:** 2026-11-10.
**Scope:** статическое чтение текущего worktree после последних durable scope/
artifact/update changes, Redis MCP NX/replay registration, real-backend acceptance
harnesses, dormant cutover metadata, MinIO STS canary, event ownership и shell-
origin/CSP handoff changes; historical independent audit относился к HEAD `6ee720dd`.
**Implementation:** перечисленные ниже компоненты **statically implemented**.
**Verification:** **live production unverified**; 2026-11-10 получено real disposable acceptance evidence: PostgreSQL 27/27 и Redis 22/22 (reports/module-audit/postgres-redis/), disposable K8s 19/19, Kafka/Redpanda 16/16 и MinIO 29/29 (см. ниже). Live/dev/prod не проверялся; production configuration blockers сохраняются.
**Вердикт:** **STOP-SHIP до реальных integration checks и live acceptance.**

> Не включать plugin platform, не публиковать/устанавливать плагины как рабочие,
> не переносить pending `2026110802`, не выполнять `strangler/removal.py`.
> Наличие production factories, integration tests или real adapter classes не
> является live readiness, live evidence или человеческим разрешением на deploy.

## Release addendum 2026-11-12 — не acceptance и не публикация

- Историческая база поставки: PR **#339–#359** влиты в `main`; известный
  baseline — `415adf14`. Датированные disposable/offline доказательства ниже
  (включая 2026-11-10) остаются историей и не выдаются за свежий прогон.
- Release run **43144913547** дошёл до **release verify** и остановился на
  `INVALID_EVIDENCE`: фактический cosign predicate содержал
  `metadata.buildInvocationID`, а verifier принимал только
  `metadata.buildInvocationId`. Локальный fix разрешает оба написания и требует
  непустое run identity, но **успешного main run после него ещё нет**.
- Поэтому **verify/promote/reverify/publication успешными не заявляются**:
  фактического successful run evidence нет, platform POST/`PUBLISHED` не
  объявляется.
- Локально выявлены и исправляются, но **не проверены и не опубликованы**:
  native BuildKit SPDX-2.2 compatibility; cosign tag-based promotion
  signature/attestation после ORAS recursive copy; migrations symlink fail-closed
  staging; image-level IPv4 preference; bounded downloads/git; Ubuntu apt HTTPS.
- **B2 same-commit resume statically implemented:** release preflight резолвит
  exact quarantine refs `sha-$GITHUB_SHA` для backend/bundle; both absent →
  обычный first-run build; both present → `plugin_quarantine_resume.py`
  восстанавливает raw manifest/config blob с сохранением exact digest pair
  (offline digest/config/canonical/annotation checks); partial state и
  transient/auth/network ошибки — fail-closed без rebuild; пять
  build/evidence/sign steps пропускаются только при resume,
  `quarantine_verify` не пропускается никогда; promote/reverify/publish
  общие. Operator flow: exact digest pair в step summary → durable approval на
  этот exact digest → rerun того же commit → resume → publication.
  **Успешный live rerun/publication НЕ подтверждён.**
- **B4 pre-promotion evidence statically implemented и integrated в workflow,
  UNVERIFIED live:** `plugin_release_evidence.py` (pinned Trivy по exact
  quarantine backend digest + static-origin CSP по exact bundle digest ДО
  release GHCR write/promote, bounded JSON snapshot, без OIDC/POST/registry
  write) вызывается отдельным шагом `Verify independent release evidence before
  promotion` после `quarantine_verify` и до `id: promote`;
  `publish --release-evidence` повторно валидирует snapshot после release
  verify без повторного scanner/CSP до POST. Guard
  `IndependentEvidenceBeforeReleaseWriteTests` больше не намеренно RED:
  ordering требует evidence ДО promote, no-write guard ограничен рамками
  evidence-шага. Успешный CI run и live publication не заявлены.
- **B3:** exact `PUBLISHED` replay идемпотентен ТОЛЬКО для exact immutable
  binding (source/plugin/version/digest pair/evidence); любой другой digest
  требует нового durable approval и replay'ем не покрывается.
- **B1 remains open:** protected static-origin provisioning и доставка exact
  bundle digest на origin не выполнены workflow; без них CSP-проба fail-closed
  останавливает publication (`CSP_EVIDENCE_REQUIRED`/`CSP_ORIGIN_HTTP_ERROR`/
  `CSP_SECURITY_HEADERS_FAILED`/`CSP_ARTIFACT_BINDING_MISMATCH`).
- Live VM host `gai.conf` **не применён** из-за отсутствия доступа; полную
  сетевую стабильность объявлять нельзя.
- Действует verification freeze: новые тесты написаны, но намеренно не запущены.
  **STOP-SHIP, live production unverified и prod untouched сохраняются.**

## Текущая static implementation — не acceptance

| Область | Что видно в текущем коде | Граница утверждения |
|---|---|---|
| **PublicationApproval** | Durable administrative pre-approval связывает company/plugin/SemVer/bundle digest с exact capabilities, egress и quota policy. New/revoked/update grant требует approval; exact ACTIVE replay — отдельный путь. `consumed_at` отмечает успешную grant saga, а не сам publication или ACTIVE grant. | Offline integration `tests/integration/plugins`: **27 collected / 27 passed / 0 skipped**, включая approval/failure/replay; live checks остаются pending. |
| **Deterministic initial identity + durable legacy scopes** | `plugin_instance_id_for(company_id, plugin_id)` вычисляет canonical `inst_<32 hex>` из fixed namespace и canonical JSON до source/approval/grant. `DurablePluginInstanceScopeResolver` возвращает deterministic id только при отсутствии обоих persisted grant+instance; если пара существует, authoritative остаётся persisted `instance.plugin_instance_id`, включая legacy random id и revoked records для будущего re-grant. Partial pair, invalid types, tenant/plugin/instance mismatch дают `SCOPE_MISMATCH`; read failure — `SCOPE_RESOLUTION_UNAVAILABLE`. Подписанные `pods`/`secret_delivery` scopes привязываются к resolved source instance. | Resolver и unit/saga/fixture tests написаны, но не запускались. Real initial/replay/legacy behavior, concurrent durable pair changes и фактическая scope projection не приняты. |
| **Neutral shared scope validation** | Domain-only `validate_plugin_instance_scopes` использует переданный canonical secret-ref predicate и resolved instance id. ModuleSource вызывает его до SQL reservation/provider mutation и возвращает `MODULE_SOURCE_INSTANCE_SCOPE_MISMATCH`; CI publication сначала read-only разрешает durable grant+instance pair, затем валидирует typed canonical manifest до approval consumption, verifier и любых registry writes. Partial/mismatch/read failure не выполняет publication writes и даёт typed quarantine. Omitted declarations доступа не дают. | Offline integration подтвердил обе границы и pre-mutation denial в составе **27/27 / 0 skipped**; live source/publication evidence остаётся pending. |
| **Fresh grant identity + stable instance** | Revoke→re-grant заранее отклоняет reused revoked `grant_id` как `REGRANT_ID_CONFLICT`; SQL `regrant` фиксирует exact REVOKED previous row, сохраняет company/plugin/instance, проверяет `UPDATE RETURNING` и откатывает failed session. После completed uninstall→reinstall новая approval/publication создаёт fresh `grant_id`, а deterministic function восстанавливает тот же `plugin_instance_id`; старый grant не становится ACTIVE. | 2026-11-10 regrant portable UPDATE RETURNING fix: returned rows через scalars().all() с exactly-one-row вместо rowcount (SQLite ChunkedIteratorResult), zero/multiple => typed unavailable; REVOKED/instance/tenant binding и same-id pre-gate без изменений. New real-SQLite regression 2/2 + unit regrant 4/4, full tests/integration/plugins 27/27, real disposable PG SUCCESS + ZERO_ROW typed failure + rollback ([regrant-report](../../reports/module-audit/postgres-redis/regrant-report.md)). Real SQL concurrency/crash behavior, freshness нового `grant_id` и отказ старой identity остаются pending. |
| **ModuleSource reservation** | До вызова provider создаётся отдельная SQL reservation без выдуманного `repo_id`; `attempt_id` ограждает retry/release, uniqueness связывает company+plugin и provider+full_name. `ModuleSource` остаётся source/trust metadata, не вторым runtime registry. | Static lifecycle и guards есть; GitHub App/source→PR→OIDC live не принят, ADR-009 остаётся «Предложено». |
| **Durable plugin routes** | Revision `2026110905` после `2026110904` создаёт additive secret-free `plugin_routes` (company/plugin/grant binding, exact paths, egress count, canonical deployment). `SqlRouteRegistry` каждый раз перечитывает active grant+instance, разрешает exact replay, не чинит tampered row и не переписывает чужой grant. | Real disposable PostgreSQL plugin_postgres: **27 collected / 27 passed / 0 skipped** ([PG JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); fresh/historical/repeat upgrades и constraints подтверждены. Revoke/unregister и production caller/live behavior остаются pending. |
| **Durable artifact lifecycle + rollback** | Additive revisions `2026110906`/`2026110907` создают lifecycle и rollback vocabulary; additive `2026110908` после `2026110907` добавляет nullable `command_fingerprint` без backfill. ORM model имеет ту же nullable String(64) колонку. SQL и InMemory вычисляют SHA-256 полного normalized `RollbackCommand`, сохраняют fingerprint при rollback и требуют exact fingerprint при replay; historical NULL не принимается как exact replay evidence, reason/`command.actor` mismatch даёт typed conflict. Source-generation token и current-target registry replay возвращают stored record с authoritative timestamps, process-local lifecycle log не является production rollback store. | Real disposable PostgreSQL: **27 collected / 27 passed / 0 skipped** ([PG JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)). Fresh/historical/repeat до `2026110909`, no-backfill/legacy conflicts, lifecycle constraints, rollback cycle и same-generation lost-response replay подтверждены; fd6 _recover_publication_replay hardening: artifact_atomicity 5/5 ([JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-atomicity-fd6-junit.xml)); regrant SUCCESS/ZERO_ROW + rollback ([regrant-report](../../reports/module-audit/postgres-redis/regrant-report.md)). Extended A→B→A→B→A production-caller cycles и live behavior остаются pending. |
| **Concurrent first INSERT + exact replay** | После unique-race `IntegrityError` failed transaction откатывается, а `_recover_publication_replay` в fresh transaction повторно читает digest/version/current/lifecycle под locks. Success возможен только при exact semantic совпадении artifact, verification, current и lifecycle; mismatch остаётся typed `ConcurrentPublishError`/semantic conflict, без повторной записи. Persisted `created_at`/`published_at` и lifecycle `occurred_at` authoritative, retry не создаёт новый token/timestamp. `PluginArtifactRegistry.publish_async` и promote path проецируют returned stored record, а не локальный candidate. | Real disposable PostgreSQL: **27 collected / 27 passed / 0 skipped** ([PG JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); concurrent first INSERT, fresh re-read, authoritative stored timestamps, exact replay и typed semantic mismatch подтверждены. Production publish/promote/live behavior остаётся pending. |
| **Async artifact reads для gateway consumers** | `PluginArtifactRegistry.get_async` делегирует async store; `GrantAdapter` и SQL grant→manifest composition ожидают `get_async` для grant-bound artifact вместо sync SQL bridge на event loop. Missing/unavailable/mismatched artifact не даёт доверенной projection. | Async consumer paths statically implemented; реальные оба entrypoints, grant/digest binding, failure denial и отсутствие event-loop sync bridging остаются integration/live unverified. |
| **Async TenantDB admin** | `AsyncpgAdminConnection` открывает и пингует cluster pool на startup, лениво создаёт tenant pools, использует настоящие asyncpg binds и закрывает все pools через lifecycle; health fail-closed. | Реальные PostgreSQL provisioning, isolation, repeat/close и pgaudit не проверялись live. |
| **TokenRequest lifecycle identity** | Отдельный provider для lifecycle K8s clients фиксирует ServiceAccount `plugin-deployment-admin`, audience `https://kubernetes.default.svc.cluster.local`, TTL ≤ 3600 секунд, proactive refresh, coalesced in-flight mint и shutdown. `build_runtime_ports` использует один TokenRequest-backed client для scale, readiness, state store и DeploymentAdmin; mounted/static token, default fallback и override без `scoped_identity`/с mismatched provider отвергаются. | Это не per-request plugin identity и не production/live acceptance. Fresh disposable kind v1.37.0: **19 collected / 19 passed / 0 skipped**, реальные SA TokenRequest/RBAC deny/refresh/failure/shutdown ([fix-report](../../reports/module-audit/k8s/fix-report.md), [JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)). |
| **Unified gateway composition** | `install_api_plugin_platform` — единый включённый composition для `prodavan.main` и `prodavan.main_plugin_gateway`: одни declarations/stores/tasks, real WorkerManager hook, один install-created `ManifestJobTasks`, platform handlers и job handlers без app.state divergence. Disabled entrypoints сохраняют fail-closed surfaces; standalone factory остаётся deferred single-owner. | Cold offline entrypoint tests написаны, но не запускались; реальные два процесса и live jobs/MCP composition не приняты. |
| **SecretProjection** | Per-instance reconcile управляет ровно четырьмя keys: `GATEWAY_URL`, `PLUGIN_ID`, `TENANT_DB_DSN`, `WEBHOOK_SECRET`. Есть ownership labels, DSN-ref SHA-256 binding, exact-key/drift checks и resourceVersion CAS; значения сравниваются при каждом ensure, включая rotation при неизменном ref. | Disposable K8s v1.37.0: **19 collected / 19 passed / 0 skipped** для exact four keys, rotation/CAS/ownership/drift и no-plaintext logs ([fix-report](../../reports/module-audit/k8s/fix-report.md), [JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)); production/live остаётся unverified. |
| **DeploymentAdmin + distributed update ownership** | `ensure`/`update` проецируют Secret, Service и backend Deployment по immutable digest. `SqlPluginUpdateLock` использует transaction-scoped PostgreSQL advisory lock для company/plugin/instance; rollback/close завершает owned cleanup, а rollback/close failure вызывает `connection.invalidate()` с `detach()` fallback, чтобы старая physical connection и её locks не вернулись в pool. Update owner token проецируется в Deployment metadata и pod template; digest+owner fences проверяются до PATCH/compensation. Rollback восстанавливает digest и удаляет transient annotation. Foreign SQL/runtime owner запрещает compensation/quarantine. | Targeted runtime units: **101 passed**. Disposable K8s v1.37.0: **19 passed / 0 skipped** для CAS/annotation/update-owner/rollback, sibling volumes/mounts и scoped uninstall ([fix-report](../../reports/module-audit/k8s/fix-report.md), [JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)). 2026-11-10 real PostgreSQL update_lock tests green (11/11 внутри plugin_postgres 27/27, [JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml)): xact contention/cancellation, rollback/close-failure invalidate, real physical discard (sync invalidate + fresh-checkout invalidate fallback), retry на новой physical connection, production-pool reuse, no cross-tenant collision; offline unit 6/6. Cleanup fix реально invalidates physical connection без session.bind mutation. Два production API processes и atomic live rollout/recovery остаются pending. |
| **Exact grant quotas / all 6** | Approval, durable grant и admin list DTO сохраняют один exact `QuotaSet`: `ops_per_minute`, `max_rows`, `max_bytes`, `max_keys`, `max_object_bytes`, `max_egress_bytes`. В plugin token quotas/egress не встраиваются. | Effective quotas и `429 QUOTA_EXCEEDED` должны быть доказаны на реальном adapter request path. |
| **Anonymous public API/static** | Bootstrap выбирает только explicit public declarations на exact public page. Gateway допускает anonymous только unique read-only GET declaration; GET/HEAD требуют пустое body, POST/side effects и private operations отклоняются. `GrantAdapter` повторно сверяет active grant company/plugin/instance/version/artifact и digest-verified published bundle; static требует тот же bundle digest в claims, grant и stored artifact без backend-digest/client fallback. Public static/API GET/HEAD scenarios возвращают 200, private остаётся deny, capability token и Authorization не передаются anonymous/static. | Production-adapter scenarios написаны, но **не запускались** и не являются live evidence. Public bootstrap DTO сам по себе не authorization proof; реальные оба entrypoints, public 200/private deny, exact identity и no-token behavior остаются live unverified. |
| **Shell-origin/CSP handoff** | API bootstrap выдаёт exact server-derived `plugin_shell_origin`; web canonicalizes его независимо от `window.location` и сериализует bounded `agentscale-plugin-shell/1` DTO с plugin/version/digest в `iframe.name`. `StaticResponsePolicy` формирует server-owned CSP с `frame-ancestors` ровно на этот shell origin, без wildcard или shared static/shell origin. Hello frontend читает и валидирует DTO. | Handoff DTO, API/web policy и lifecycle regressions **statically implemented; tests not run**. Для live/hello требуется **новый signed bundle** с этим consumer и digest-matching protected static-origin/CI evidence; старый bundle не доказывает handoff. Browser/API/Gateway и оба entrypoints остаются pending. |
| **Installation-parent scope + `2026110909`** | Current private bootstrap fail-closed: missing/invalid binding даёт `PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE`/`PLUGIN_BOOTSTRAP_SCOPE_MISMATCH`, public возвращает только company scope без cabinet/project. Additive `2026110909` после `2026110908` создаёт `plugin_installation_scopes` и nullable source/reservation parent pairs с composite tenant/cabinet/project FK, без backfill. `SqlInstallationScopeRepository.ensure_from_source` требует READY source, deterministic instance и exact ProjectRow anchors; admin grant вызывает binding, bootstrap читает его после exact grant+instance validation. Legacy/random/ambiguous instances не backfill'ятся. | Migration/model/source/admin/bootstrap path validated on real disposable PostgreSQL 2026-11-10 (27/27). Real fresh/historical/repeat migration, no-backfill, paired scope CHECK/FK rejection, deterministic source binding и private exact scope/public company-only подтверждены real PostgreSQL 2026-11-10 (27/27); HTTP two-entrypoint/live и удаление binding row с повторным fail-closed остаются unverified. |
| **hello typed RPC** | Manifest использует canonical `hello.greetings@1.0` и две unique operations: `list` GET `/api/greetings/list`, `greet` POST `/api/greetings`; обе private. Backend читает `WEBHOOK_SECRET`. | Hello остаётся демонстрационным модулем, не рабочим vertical slice. |
| **Production composition и explicit test doubles** | Молчаливые in-memory defaults для audit, quota, activation state, grant и artifact store удалены: enabled builders требуют явные durable ports, а production admin/repository composition использует SQL/Redis и distributed update lock. Missing required ports являются composition blockers. `install_plugin_platform_for_tests` и явные constructor overrides отделяют test/dev doubles; cold `create_app`, approval→publication→grant и stateful IO-double сценарии остаются только тестовыми контурами. | Offline `tests/integration/plugins`: **27/27 / 0 skipped**; explicit fakes не являются production wiring или live evidence. Реальные durable ports, отсутствие process-local fallback и cold обоих entrypoints остаются pending. |
| **Real adapter registry composition** | Enabled composition требует все девять real typed adapters, operations и `quota_usage`; missing/`UnavailableAdapter`/in-memory required ports являются composition blockers в написанных tests. | Реальный registry и каждый внешний adapter остаются pending integration/live acceptance, несмотря на удаление старого «not implemented». |
| **Server-owned jobs + Redis execution fencing** | `PLUGIN_JOB_DECLARATIONS` принимает только exact verified selectors `{plugin_id, version, artifact_digest}` и образует scoped `ManifestJobTasks` из signed `rpc_operations`. Redis `SET NX` трактует `None` как обычный claim miss, затем перечитывает exact binding/completed record; неподтверждённый response не считается success. Owner+binding проверяются Lua CAS при renew/complete, renewal продолжается через async post-validation и final CAS. Uncertain outcome/cancellation/post-validation mismatch не освобождает lease для side-effecting retry; автоматический retry ограничен pre-side-effect conflict. | Real disposable Redis: **22 collected / 22 passed / 0 skipped** ([Redis JUnit](../../reports/module-audit/postgres-redis/redis-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); NX, owner/binding CAS, post-validation renewal, uncertainty и cancellation проверены. API/standalone worker и оба entrypoints остаются live pending. Non-empty verified `PLUGIN_JOB_DECLARATIONS` остаётся **production configuration blocker**: test declarations не production config и не evidence. |
| **MCP signed-RPC + Redis registration** | Optional signed `mcp_servers` сочетаются с digest-scoped durable Redis registration, которая не является privilege selector. `RedisMcpRegistrationStore` возвращает `True` только при создании, `None`/`False` как NX miss; другой response не подтверждён. `DurableMcpAdapter` после `put_if_absent` обязательно перечитывает canonical definitions: exact replay успешен, mismatch — `mcp_registration_conflict`, overwrite запрещён. Discovery/broker используют один signed resolver и `mcp`/`mcp.invoke`. Active/error fixes **statically implemented после independent static review**: explicit ACTIVE/is_active/revoked_at/timestamps без fallback, exact grant/artifact binding и sanitized typed GatewayError/JSON-RPC codes. | Real disposable Redis: **22 collected / 22 passed / 0 skipped** ([Redis JUnit](../../reports/module-audit/postgres-redis/redis-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); NX True create, None/False miss+re-read, concurrent exact replay/conflict, signed resolver, stale/foreign/revoked denial подтверждены. Live SQL/JWT/backend/quota/audit и оба entrypoints остаются pending. |
| **Scoped runtime identity и service proxy** | Deployment проецирует фактические `plugin_instance_id` и `backend_digest` через read-only `downwardAPI`. `GrantAdapter`, `ActivationAdapter` и proxy повторно сверяют exact grant/company/plugin/instance/artifact fields: claims==durable grant, resolver не перенаправляет authoritative grant instance, activator result и `WakeResult.claims` обязаны совпасть с requested tenant/plugin/instance и валидным grant/artifact binding, lease затем сравнивается с expected grant/artifact. Missing/mismatched evidence отклоняется без resolver/backend-digest fallback. Anonymous/static capability token не создаётся. | Identity/adapter/proxy scenarios написаны, но не запускались. Projection, exact grant/company/plugin/instance/artifact binding, fresh wake/fixed headers, no-fallback denial и отсутствие header-only spoofing остаются live unverified. |
| **Trusted runtime provider API** | Fixed typed POST routes под `/capabilities/v1/runtime/*` один раз монтируются на shared `plugin_gateway.router`, который включают оба entrypoints. Strict bounded contracts принимают tenant identity только из verified `PluginClaims`, без query identity или generic exec/proxy. Zero-arg `get_scoped_runtime_provider` fail-closed 503 без trusted composition; production строит `ScopedRuntimeProvider` один раз из installed real platform ports и устанавливает per-app dependency override, без request/global `app.state` lookup. На каждый вызов перепроверяются active grant, canonical backend, artifact и backend digest. Egress declaration читается из durable grant, artifact metadata — из digest-verified OCI manifest, secret metadata — digest-only через control-plane SecretStore. `max_result_bytes` ограничивает и OCI fetch, и результат; превышение даёт `413 ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED`. Pod status/logs/messages остаются typed-unwired 503. | Route/provider/composition tests написаны, но не запускались. Два реальных процесса и per-app isolation, zero-arg fail-closed dependency, auth/claims binding, cross-tenant denial, OCI/SecretStore composition, byte-limit 413, limits/redaction и unwired 503 остаются live unverified. |
| **Events signing secret and fixtures** | `PLUGIN_EVENT_SIGNING_SECRET_REF` — typed reference `vault://`, `file://` или `k8s-secret://<name>/<key>`, не plaintext и не fallback на claims/auth secrets. Enabled platform требует resolvable reference; API/Gateway mounts read-only `0400`. В текущем dev overlay есть только `plugin-event-signing.sealed.yaml.example` с placeholder и он не входит в resources: реальный SealedSecret/ciphertext — **production configuration blocker**, не заменяемый fixture. `optional: true` не смягчает enabled startup. Integration fixtures используют `HmacPluginEventSigner` только как test doubles. | Нет real SealedSecret/ciphertext/key evidence. Disposable Kafka/Redpanda harness подтвердил independent test signing refs/keys, sign/verify и fail-closed fixtures в составе **16 collected / 16 passed / 0 skipped** ([JUnit](../../reports/module-audit/kafka/junit-final.xml), [summary](../../reports/module-audit/kafka/acceptance-summary.json)). Real resource resolution, rotation и missing-key fail-closed на двух entrypoints не приняты live. |
| **Event delivery ownership + Kafka assignment fence** | `_event_delivery_claims` подключает `RedisDeliveryClaimStore` через shared `event_redis`/`redis_client` без in-memory default. Typed `ConsumerAssignment` проверяется при record ownership, после wake и непосредственно перед handler, во время lease heartbeat/renew и перед completion/commit; missing/mismatched assignment отклоняется без backend/ACK/DLQ, uncertain side effects не подтверждаются. Mandatory after-wake rebalance scenario без xfail проверяет stale denial и recovery через нового owner. | Real disposable Redpanda + PostgreSQL + Redis + localhost backend: **16 collected / 16 passed / 0 skipped**, complete gate `KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE=1` ([JUnit](../../reports/module-audit/kafka/junit-final.xml), [summary](../../reports/module-audit/kafka/acceptance-summary.json)); targeted Kafka adapter units — **23 passed** ([JUnit](../../reports/module-audit/kafka/unit-kafka-adapters.xml)). Mandatory after-wake scenario без xfail подтвердил typed stale denial с zero backend/commit/DLQ и owner-only recovery с ровно одним real 2xx/commit ([rebalance evidence](../../reports/module-audit/kafka/rebalance-evidence.log)). Live SealedSecret/K8s wake и production configuration остаются blockers. |
| **Objects STS async post-probe + canary** | `objects.scoped_credentials` остаётся typed-unwired по умолчанию: `sts_probe_enabled=false`, config flags не дают availability. `MinioSts.probe` делает bounded async AssumeRole с canary-only session policy: unconditioned `s3:ListBucket` только на dedicated configured probe bucket и `s3:GetObject` только на fixed canary key, без tenant data/Put/Delete/foreign buckets; credentials отбрасываются и сохраняется только success timestamp; `_probe_lock` даёт singleflight, freshness/retry/revalidation и close/error инвалидируют доступ. Fixed canary после fix требует dedicated provisioned bucket (`HeadBucket=200`) и fixed zero-byte object (`HEAD=200`, `ContentLength=0`). `NoSuchBucket`/`NoSuchKey`/ambiguous `404` или response остаются unwired. Foreign-key `HEAD` и foreign `ListBucket` обязаны быть `403`. Standalone worker не является отдельным consumer gate и это не pass. | Real disposable MinIO: **29 collected / 29 passed / 0 failed / 0 errors / 0 skipped** ([JUnit](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); targeted MinIO units — **230 passed**. STS TTL 900..3600, canary-only HeadBucket=200/fixed HEAD=200+zero, foreign HEAD/List=403 и tenant unscoped List denial подтверждены; missing 403/404 остаются unwired. API/Gateway lifespan и live production остаются unverified; presign/static fallback запрещён. |
| **Objects exact-key presign + strict SigV4** | Typed `objects.presign` принимает полный exact key в server-derived `tenant-<company>` + `module-<plugin>/` scope, только GET/PUT/HEAD и TTL 60..3600. Adapter отклоняет wildcard/traversal/encoded scope escape и проверяет bucket/key в URL. Query authentication обязана содержать ровно шесть однозначных SigV4 fields: `X-Amz-Algorithm`, `X-Amz-Credential`, `X-Amz-Date`, `X-Amz-Expires`, `X-Amz-SignedHeaders`, `X-Amz-Signature`; unknown/case-variant/repeated/session-token fields, невалидные algorithm/date/credential scope/signature и неточный TTL отклоняются. `X-Amz-Secret-Key` и `AWSAccessKeyId` явно отклоняются как credential leak. `MinioPresigner` передаёт exact `HttpMethod` и подписывает отдельным principal, не admin. URL возвращается только авторизованному caller и не копируется в audit; `objects.scoped_credentials` остаётся STS-only. | Real disposable MinIO IAM/method/key/expiry и strict six-field validation подтверждены в составе **29/29 passed, 0 skipped** ([JUnit](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml)); presigner isolation и no-signature/no-secret logs проверены. Revocation и production/live остаются pending. |
| **CI publication** | Release использует protected environment `vars.PLUGIN_PLATFORM_PUBLICATION_CONFIG`, а не caller `source_id`/capabilities/approval booleans. Evidence-first контракт: `plugin_release_evidence.py` (pre-promotion pinned Trivy по exact quarantine backend digest + HEAD protected static-origin с configured security headers/CSP и exact `X-AgentScale-Bundle-Digest`, bounded JSON snapshot, без OIDC/POST/registry write) → promote → `release_verify` → `publish --release-evidence` (повторная валидация snapshot, затем GitHub OIDC token и authenticated `POST /internal/plugin-artifacts` без повторного scanner/CSP). B2 same-commit resume preflight восстанавливает exact digest pair из уже подписанных quarantine artifacts без rebuild; quarantine_verify обязателен всегда. | Workflow/scripts integrated локально: evidence собирается отдельным шагом ДО `id: promote`, publish после release_verify переиспользует snapshot через `--release-evidence`; guard `IndependentEvidenceBeforeReleaseWriteTests` больше не намеренно RED, но это UNVERIFIED static integration, а не успешный run. Release run **43144913547** (актуализация 2026-11-12) дошёл до release verify и остановился на `INVALID_EVIDENCE` из-за `buildInvocationID`; локальный fix ещё не дал successful main run. Protected live inputs, успешный post-fix run, live resume rerun и publication не подтверждены; никакой platform POST или PUBLISHED результат не заявляется. |
| **Real acceptance harnesses — PostgreSQL/Redis/K8s/Kafka/MinIO done; entrypoint not run** | `tests/migration/plugin_postgres` содержит upgrades до `2026110909`, xact lock и rollback cycles; `redis_plugin_acceptance` — MCP NX/jobs/quota; `kafka_plugin_acceptance` — signed events/rebalance; `minio_plugin_acceptance` — STS/presign/expiry; `tests/e2e/k8s/plugin_acceptance` — real TokenRequest/Deployment/SecretProjection; `entrypoint_acceptance` — два uvicorn + standalone Celery worker и real PostgreSQL/Redis/Mongo/Kafka/MinIO/K8s. Точные package-specific `--confcutdir`, early `-p` и required env собраны в [invocation contracts](#real-harness-invocation-contracts--не-acceptance). | K8s harness выполнен 2026-11-10 на fresh disposable kind v1.37.0: **19 collected / 19 passed / 0 skipped** ([fix-report](../../reports/module-audit/k8s/fix-report.md), [JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)); targeted runtime units — **101 passed**. Kafka/Redpanda harness выполнен на disposable resources: **16 collected / 16 passed / 0 skipped** ([JUnit](../../reports/module-audit/kafka/junit-final.xml), [summary](../../reports/module-audit/kafka/acceptance-summary.json)); targeted units — **23 passed** ([JUnit](../../reports/module-audit/kafka/unit-kafka-adapters.xml)). PostgreSQL plugin_postgres 2026-11-10: **27 collected / 27 passed / 0 skipped**; Redis redis_plugin_acceptance: **22 collected / 22 passed / 0 skipped** ([JUnit PG](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml), [JUnit Redis](../../reports/module-audit/postgres-redis/redis-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md), [SHA256](../../reports/module-audit/postgres-redis/sha256-manifest.json)). Entrypoint harness code/scenarios **не запускались**, их collected/JUnit/live evidence отсутствует. MinIO выполнен: **29 collected / 29 passed / 0 skipped** ([JUnit](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); targeted MinIO units — **230 passed**. `PLUGIN_JOB_DECLARATIONS`, real SealedSecret, CI/hello build/new signed bundle и live STS остаются blockers. |
| **Pods + secret delivery** | Optional `pods.allowlist` содержит 1..256 exact `{plugin_instance_id, project_id, job_id}` tuples; omitted declaration не даёт pod access. Optional `secret_delivery` имеет только `mode: k8s_secret` и 1..256 canonical `vault://`/`file://` refs. Отдельный per-instance delivery Secret имеет hashed `SECRET_*` keys, value-version digests, ownership/CAS/exact-set reconcile; Deployment монтирует его read-only `0400` в `/var/run/agentscale/capability-secrets`, а cleanup отзывает refs и удаляет только owned delivery Secret. | Это не четырёхключевой runtime Secret и не plaintext в manifest. Disposable K8s acceptance подтвердил delivery/rotation/revoke/delete и RBAC ([JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)); pods execution остаётся pending. Hello не объявляет `pods` или `secret_delivery`. |
| **Legacy dynamic host cleanup** | Host-only web files удалены как **завершённый static cleanup**. Сохранены renderer/schema/protocol/policy/registry-client и dynamic-app inventory; `PRESERVED_DYNAMIC_GUARDS` и `legacy-host-import-graph.test.ts` запрещают возврат host modules/imports и охраняют current plugin path. | Guards не запускались; это не browser/live acceptance и не разрешение declarative DB cutover/removal. |
| **Dormant 0802 + metadata-only cutover** | `2026110802` остаётся historical/dormant и не копируется, не регистрируется и не активируется. `CODE_FIRST_REMOVAL_INVENTORY` — только S4 review candidates. `cutover.py` сохраняет metadata-only compatibility sequence/cursor: `advance()`/`complete_current_step()` меняют только metadata flags, без DB/DML/drop/Alembic/deploy/feature-flag side effects. `declarative_app_removed=True` и REMOVAL step — inert compatibility values, не evidence удаления. S4 code-first cleanup с отдельными evidence предшествует S5 ADR/review/acceptance; новая revision создаётся только от тогдашней current head. | Metadata compatibility statically present, guards не запускались. S4/S5 остаются незавершёнными отдельными barriers; metadata progression не снимает STOP-SHIP и не разрешает schema cleanup. |
| **UI-путь создания и установки** | `/admin/plugin-modules` (таб «Plugin-модули» в админке) монтирует `ModuleAdminPage` с секциями: `PluginModuleCreate` создаёт источник из template-репозитория и привязывает точный cabinet/project; `PluginPublicationApprove` фиксирует разрешение на конкретный digest; `PluginLifecycle` выдаёт/отзывает/удаляет grant и будит модуль. Версия и digest берутся из `GET /admin/plugins`, ручной ввод digest невозможен; текст исключения на экран не попадает. | Frontend: **896 passed / 102 файла**, `tsc --noEmit` чистый; покрыто 4 модулями тестов (создание, approve, жизненный цикл, монтирование). Меню плагинов и привязка agent/instructions из UI остаются не реализованы. |

Дополнительно текущий protected `/capabilities/v1/health` статически совпадает
с eight-field hello introspection contract, а verified webhook forwarding
восстанавливает signature/event/delivery после sanitization. Прежние формулировки
«health ещё не согласован» и «signature всегда теряется» удалены как устаревшие;
сквозные identity и Gateway→hello webhook остаются **live unverified**.

## Emergency fail-closed и verification freeze

Документированный emergency barrier (`PLUGIN_PLATFORM_ENABLED=false` и суженная
plugin NetworkPolicy) не заменяет проверку безопасности. Текущие live значения в
кластере в этой задаче не читались. Offline verification started; formal runs
limited to listed evidence: код и письменные acceptance criteria по-прежнему
рецензируются статически, а выполненные проверки ограничены dated baseline ниже;
cluster operations не выполняются.

## Offline verification baseline 2026-11-10

Только подтверждённые offline команды/результаты на текущем worktree. Это отдельные
targeted package runs и отдельный full unit run, НЕ integration/live acceptance и НЕ
снятие STOP-SHIP. `tests/unit/plugins` без ignore/deselect завершён: **3753 passed, 0 failed,
0 errors, 0 skipped**, exit 0, 20.97s; warnings 466 (test keys/deprecations).
Entrypoint harness и browser остаются pending. PostgreSQL/Redis и MinIO имеют disposable-only evidence
([MinIO summary](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); production/live unverified.

| Контур | Подтверждённый результат |
|---|---|
| Backend compile | `compileall` → exit 0. |
| Backend acceptance | `tests/acceptance/plugins` → **57 passed**. |
| Infra/ops tests | `infra/ops/tests` → **122 passed**. |
| Ops validation | `prodavan-ops validate` через `PYTHONUTF8=1` → OK. |
| Frontend | **97 files / 866 tests passed** + `tsc`/`vite build` success; React `act(...)` warnings и большой chunk — non-blocking. |
| Targeted unit pilot + CI publication | **43 passed**. |
| Lifecycle targeted | **39 passed**; финальный независимый read-only review lifecycle-области — **no findings**. |
| Full backend unit suite | `tests/unit/plugins` без ignore/deselect → **3753 passed, 0 failed, 0 errors, 0 skipped**, exit 0, 20.97s; warnings 466 (test keys/deprecations). |
| Integration plugins (read-only) | `tests/integration/plugins` → **27 collected / 27 passed / 0 skipped**. |
| Targeted backend packages — core (отдельные runs) | admin **141/141**; repositories **159/159**; transport **135/135**; wiring **203/203**; events **82/82**; runtime **355/355**; pilot **197/197**. |
| Targeted backend packages — schema/gateway/persistence (отдельные runs) | CI/domain **311/311**; gateway+MCP **207/207**; persistence **90/90**; controllers/domain/identity **451/451**. |
| Targeted backend packages — adapters/runtime (отдельные runs) | adapters/tenantdb **307/307**; platform_admin+infra_adapters **147/147**; egress **149/149**; strangler **116/116**; objects+minio **351/351**. |
| Kafka adapter targeted | **23 passed** для bounded poll/timeout/record/cancellation semantics ([JUnit](../../reports/module-audit/kafka/unit-kafka-adapters.xml)). |

K8s disposable acceptance уже задокументирован выше (19/19 passed,
kind v1.37.0). Kafka/Redpanda disposable acceptance — **16/16 passed / 0 skipped**
([summary](../../reports/module-audit/kafka/acceptance-summary.json), [rebalance evidence](../../reports/module-audit/kafka/rebalance-evidence.log)); production/live не заявляется.

Entrypoint acceptance harness на текущем этапе **не пройден** и не объявляется
пройденным. При попытке реального запуска найдены и исправлены три дефекта,
из-за которых он не мог пройти ни в каком окружении:

1. early guard читал `option.confcutdir` в `pytest_load_initial_conftests`, где
   pytest ещё не применил аргументы, и отвергал документированную команду как
   bare invocation. Теперь аргументы разбираются через `parser`; regressions
   покрывают и разрешённый, и запрещённый вызов.
2. `database.py` использовал `async with create_async_engine(...)`, хотя
   `AsyncEngine` не реализует `__aenter__`/`aclose`. Введён `_engine_scope` с
   обязательным `dispose()`.
3. `seed_bootstrap_identity` вставлял `cabinet_company_grants` раньше
   `cabinet_instances`; ORM не выводит порядок для этой FK, поэтому PostgreSQL
   отдавал ForeignKeyViolationError. Теперь родитель сбрасывается явным `flush()`.

После исправлений offline-часть харнесса даёт **17 passed / 14 skipped** (skip —
только явная причина «disposable environment отсутствует»), а `DisposablePostgres`
реально создаёт БД `plugin_pg_<20 hex>`, прогоняет миграции до `2026110909` и
закрывается. Для 14 world-тестов по-прежнему требуются подписанный attested
OCI bundle, SASL_SSL Kafka, TLS PostgreSQL, смонтированный ServiceAccount и
развёрнутые plugin Deployment/Service — это остаётся pending.

STOP-SHIP не снимают ни отдельные completed static tasks, ни production
composition tests, ни исторические зелёные отчёты. Нужны явно разрешённые
integration runs на реальном составе, затем воспроизводимый live acceptance и
отдельное human-решение.

## Обязательные pending evidence

[Acceptance map](plugin-acceptance-map.md) сохраняет STOP-SHIP; отдельные disposable pass-отметки не являются production enablement. В частности,
нужны новые доказательства:

1. fresh и historical upgrade `2026110901 → 2026110909`, включая repeat,
   фактические constraints/defaults, durable `plugin_routes` register/replay/
   revoke behavior и durable `plugin_artifact_lifecycle` constraints/writes для
   `publish`/`promote`/`rollback` без перезаписи existing lifecycle/artifact state.
   `2026110908` nullable `command_fingerprint` и `2026110909` installation-parent
   scope требуют model/migration parity, composite FK/unique checks и no-backfill:
   historical NULL не exact replay evidence, missing/ambiguous installation scope
   сохраняет private bootstrap fail-closed и не получает fabricated cabinet/project;
2. first-ever publication из пустого artifact/grant state, approval denial,
   failure/retry/replay без частичной активации;
3. initial grant/replay и deployment-first update с exact all-six quotas,
   health/CAS/compensation; distributed xact `SqlPluginUpdateLock` на реальном
   PostgreSQL между двумя API processes, rollback/close invalidation, реальный
   `detach()` fallback и retry на новой physical connection без leaked advisory
   lock; owner token в обоих K8s annotation projections, digest/owner CAS,
   rollback с удалением transient annotation и foreign-owner no-quarantine;
4. DSN/webhook secret rotation, Secret ownership/drift и resourceVersion CAS;
5. anonymous public API/static + shell handoff: реальные public GET/HEAD 200
   против private/POST/non-public deny без backend forward при denial, exact
   grant/company/plugin/instance/version/artifact и published bundle binding,
   no resolver/backend-digest/client fallback и отсутствие anonymous token.
   Server-derived `plugin_shell_origin`, exact `frame-ancestors`, bounded
   `agentscale-plugin-shell/1` iframe.name DTO, новый signed bundle и browser
   handshake проверены на API/Gateway/static-origin и обоих entrypoints. Public
   bootstrap остаётся company-only; private требует durable `2026110909` binding
   с exact cabinet/project/ProjectRow, а missing/mismatched row повторно fail-closed;
6. `429 QUOTA_EXCEEDED` на реальном metered path и реальный nine-adapter
   registry без stubs/unwired; production audit/quota/activation-state/grant/
   artifact/admin ports подтверждены на PostgreSQL/Redis/K8s, in-memory defaults
   не возвращаются, а explicit test doubles не участвуют в enabled composition;
7. выполнение static import guards и browser regression после legacy host
   cleanup — отдельно от declarative data cutover;
8. полный vertical slice artifacts → approval/publication → grant/update →
   tenant DB/migrations → identity/RPC/webhook → public/private UI → isolation/
   revoke/uninstall с HEAD, окружением, датой и evidence;
9. jobs: production non-empty exact `PLUGIN_JOB_DECLARATIONS` и verified
   publications как configuration gate; API hook, deferred standalone factory и
   оба entrypoints. На реальном Redis доказаны `NX=None` miss+re-read, exact
   binding/completed replay, owner/binding renewal через post-validation/final CAS,
   pre-side-effect-only retry и uncertain/cancel/mismatch no-release/no-retry;
10. MCP: active/error fixes и NX/replay semantics подтверждены на disposable
    Redis: **22 collected / 22 passed / 0 skipped**; `SET NX=True` create, реальный
    `None`→`False` miss+re-read, exact replay и foreign-race conflict без overwrite
    проверены. Live SQL/JWT подтверждает ACTIVE/is_active/revoked_at/timestamps и
    exact digest binding без fallback; sanitized typed GatewayError codes,
    missing/raw/mismatch rejection, signed resolver, backend handshake/list/call,
    revoke, quota/audit и cross-tenant denial проверяются на обоих entrypoints;
11. runtime/infrastructure identity: read-only projection, exact grant/company/
    plugin/instance/artifact fields в claims/grant/activator result/WakeResult
    claims/lease, отсутствие resolver/backend-digest fallback, fresh wake/fixed
    headers, header-only spoofing denial и явные 503 для typed-unwired pod
    status/logs/messages на реальном составе;
12. events: реальный `plugin-event-signing.sealed.yaml` (не `.example`) и его
    кластерный SealedSecret→Secret resolution/mount — production configuration
    gate; независимый HMAC key, rotation и fail-closed startup при enabled+missing
    key на двух entrypoints остаются pending. Disposable complete run с
    `KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE=1` выполнен: **16 collected / 16 passed /
    0 skipped**, after-wake stale denial, zero backend/commit/DLQ,
    heartbeat/renew/commit fences и owner-only recovery с ровно одним real 2xx
    ([JUnit](../../reports/module-audit/kafka/junit-final.xml), [rebalance evidence](../../reports/module-audit/kafka/rebalance-evidence.log)); targeted units — **23 passed**. Это не live SealedSecret/K8s wake evidence.
13. STS: disposable MinIO fixtures выполнены: **29 collected / 29 passed / 0 skipped**
    ([JUnit](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); targeted units — **230 passed**. API/Gateway/live production остаётся unverified. Доказаны
    default-disabled/unwired, bounded async
    AssumeRole с discarded credentials, dedicated bucket `HeadBucket=200`, fixed
    zero-byte object `HEAD=200` + `ContentLength=0`; `NoSuchBucket`, `NoSuchKey`,
    ambiguous `404`/response остаются unwired. Foreign-key `HEAD` и foreign
    `ListBucket` обязаны вернуть `403`. Singleflight/freshness/retry/revalidation/
    error-close invalidation на API/Gateway, scoped policy/TTL/isolation и
    отсутствие presign/static fallback обязательны; standalone worker как отдельный
    consumer gate неприменим и не evidence;
14. CI publication: protected config/source mapping, pinned Trivy release report,
    static-origin CSP+digest evidence и фактический OIDC POST только после release
    verify с точным platform response. На 2026-11-12 release run **43144913547**
    остановился на release verify (`INVALID_EVIDENCE`, `buildInvocationID`);
    successful post-fix main run, promote/reverify и фактический platform POST/
    `PUBLISHED` не подтверждены. Для shell handoff требуется новый signed
    hello bundle с handoff consumer и matching bundle/backend digests; старый
    artifact, hello build без нового bundle или отсутствующие live/CI inputs
    остаются blockers;
15. pods/secret_delivery: exact allowlists, delivery/reconcile/rotation/revoke/
    delete lifecycle, ownership/CAS/RBAC и отсутствие stale keys/plaintext logs;
16. durable legacy scope resolution: initial empty grant+instance pair даёт
    deterministic `plugin_instance_id_for`; persisted pair, включая revoked legacy,
    сохраняет authoritative instance id. Partial/invalid/tenant/instance mismatch и
    read failure отклоняются до approval/verifier/registry writes; exact signed
    `pods`/`secret_delivery` binding, initial/replay/revoke-re-grant/uninstall-
    reinstall и concurrent pair changes проверены на реальной publication/PostgreSQL path;
17. scoped activation identity: через [K8s harness](#real-harness-invocation-contracts--не-acceptance)
    реальный TokenRequest + RBAC для одного lifecycle client, использованного
    scale/readiness/state store/admin; refresh/failure/shutdown и отсутствие
    mounted/static/mismatched identity. Дополнительно digest/id/status-only logs,
    contour guards, lifecycle+delivery CAS/stale-409/drift, sibling volumes/mounts,
    readOnly 0400 и реальные scoped admin/control-plane tokens подтверждены;
18. unified entrypoints: включённые `prodavan.main` и
    `prodavan.main_plugin_gateway` на реальном составе используют одни durable
    declarations/stores/tasks/jobs/MCP/event/provider composition и не расходятся
    по handlers, state, signed-RPC resolution или worker registration; production
    job declarations/SealedSecret inputs и отсутствие in-memory defaults проверены
    отдельно на обоих процессах;
19. signed event fixtures: sign/verify fixtures на enabled composition, включая
    unsigned/tampered/unknown-topic rejection, duplicate ACK, rotation, missing-key
    fail-closed и отсутствие plaintext logs; fixtures не закрывают real SealedSecret
    production configuration gate и не становятся live evidence;
20. отдельное independent identity review deterministic initial и persisted
    legacy `plugin_instance_id`, fresh grant-id rotation, scoped lifecycle
    TokenRequest и runtime identity projection, отдельно от functional acceptance
    и до любого human enablement;
21. **shared scope validation:** один neutral validator на реальных source и CI
    publication paths; source mismatch отклоняется до reservation/provider
    mutation, publication mismatch — до approval consumption/verifier/registry
    writes; omitted scopes не дают доступа, foreign pods/secret refs дают exact
    typed errors без частичных writes;
22. **fresh grant ids:** реальные revoke→re-grant и uninstall→reinstall выдают
    разные `grant_id` при stable authoritative persisted instance (deterministic
    для новых, preserved random для legacy); same-id candidate отклоняется до saga
    mutation, старые token/lease/claims не воскресают, REVOKED/scope/RETURNING
    fences, concurrent revoke/re-grant и crash/rollback проверены на PostgreSQL;
23. **durable lifecycle rollback/concurrency:** `2026110908`, model, SQL и
    InMemory full-command fingerprint semantics реализованы static, tests не
    запускались. На PostgreSQL доказать fresh/historical/repeat upgrade, no-backfill
    и historical NULL rejection, затем A→B→A→B→A cycles. Каждый replay сравнивает
    полный stored `PluginArtifactRecord` (identity/digest/manifest/evidence/
    publication/status/timestamps) и durable lifecycle fields без второго fact;
    reason/`command.actor` mismatch отклоняется. Lost response after commit
    возвращает тот же full stored record; concurrent same rollback создаёт один
    durable fact; stale generation/wrong source/semantic mismatch дают typed conflict.
    Rollback сохраняет artifact/current CAS + lifecycle row в одной UoW, failures
    не оставляют partial current/чужую потерю; unique token и production caller обязательны;
24. **presign/SigV4 validation:** реальный MinIO подтверждает exact full
    key/bucket, только GET/PUT/HEAD, TTL 60..3600 и фактическое expiry. Adapter
    принимает ровно шесть однозначных SigV4 fields и валидирует algorithm, date,
    credential scope, signed headers, signature и exact `X-Amz-Expires`; unknown/
    case-variant/repeated/session-token fields, `X-Amz-Secret-Key`,
    `AWSAccessKeyId`, cross-tenant/plugin, wildcard/list/traversal, неверный
    method/expiry и admin signing отклонены. URL/signature/secret отсутствуют в
    audit. Authorization fixtures используют реально provisioned exact object;
    generic `404` не является authorization success — `NoSuchBucket`/`NoSuchKey`/
    ambiguous response остаются unwired. Отдельно `scoped_credentials` остаётся
    STS-only без presign fallback;
25. **provider dependency/route:** zero-arg dependency без composition
    возвращает 503 и не создаёт tenant-controlled query parameter; production
    per-app override строится один раз из scoped platform ports, изолирован между
    apps и не использует request/global `app.state` lookup. Оба enabled entrypoints
    реально монтируют fixed trusted runtime routes с verified claims, strict
    bounded contracts и per-call grant/backend/artifact/digest revalidation.
    Egress declaration, OCI artifact metadata и digest-only secret metadata
    проверены на production composition; `max_result_bytes` реально ограничивает
    fetch/result и возвращает `413 ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED`,
    cross-tenant/spoofing/generic exec запрещены, pod status/logs/messages
    остаются 503 до independent data source;
26. **stored-record registry replay + async gateway reads:** publish и promote
    возвращают persisted record с authoritative timestamps, exact replay не
    подменяет его локальным candidate; `GrantAdapter` и SQL grant→manifest
    composition используют `get_async` без sync SQL bridge на event loop.
    Grant/plugin/version/digest binding, unavailable/mismatch fail-closed и оба
    real entrypoints подтверждены integration/live evidence;
27. **real acceptance harnesses + probe isolation:** отдельно разрешённые
    `tests/migration/plugin_postgres`, `redis_plugin_acceptance`,
    `kafka_plugin_acceptance`, `minio_plugin_acceptance`,
    `tests/e2e/k8s/plugin_acceptance` и `entrypoint_acceptance` запускаются
    только по [точным invocation contracts](#real-harness-invocation-contracts--не-acceptance)
    против disposable resources с collected>0/skipped=0 и JUnit/summary evidence.
    Exact package `--confcutdir`, package path и early `-p` там, где требуется,
    исключают parent probes и ambient proxy без потери local fixtures/no-skip
    gates. Harnesses не заменяют `PLUGIN_JOB_DECLARATIONS`, real SealedSecret,
    CI/hello build/new signed bundle, live STS, K8s или оба entrypoints.

## Real harness invocation contracts — не acceptance

Команды ниже — **invocation contracts**, а не production/live evidence. PostgreSQL,
Redis, K8s и Kafka/Redpanda контракты выполнены на disposable resources; MinIO и
entrypoint остаются written runbooks для будущего отдельно разрешённого запуска. Общие обязательные условия:
запуск из `apps/api` против disposable resources, exact package `--confcutdir`,
явный package path, сохранённые local fixtures/no-skip gates и отсутствие parent
`tests/conftest.py` probes (PostgreSQL/live API/kubectl). `--noconftest` не является
заменой cutoff. Acceptance требует `collected > 0` и `skipped == 0`; missing required
inputs — failure, не skip. Нужны сохранённые JUnit/summary, а не отдельные assertions.
Ambient application/shared/proxy env не наследуется; sentinel proxies не являются
разрешением сетевого доступа. Production configuration blockers не снимаются.

| Harness | Точная изолированная команда и required env | Обязательный контур evidence |
|---|---|---|
| `tests/migration/plugin_postgres` | `python -m pytest tests/migration/plugin_postgres -q --no-header --strict-markers -rs --confcutdir=tests/migration/plugin_postgres --junitxml=plugin-postgres-junit.xml`. Env: `PRODAVAN_PLUGIN_TEST_ADMIN_DSN`, `PRODAVAN_PLUGIN_TEST_DSN` на одном disposable server. | Fresh/historical/repeat до `2026110909`, no-backfill, first-INSERT replay, rollback cycle/lost-response replay; xact contention/cancellation, rollback/close invalidate, real detach и retry на новой physical connection. **Выполнено:** **27 collected / 27 passed / 0 skipped** ([PG JUnit](../../reports/module-audit/postgres-redis/plugin-postgres-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); production/live evidence не заявляется. |
| `tests/integration/redis_plugin_acceptance` | `python -m pytest tests/integration/redis_plugin_acceptance -q -p no:cacheprovider --confcutdir=tests/integration/redis_plugin_acceptance`. Env: `REDIS_PLUGIN_ACCEPTANCE_REDIS_URL`, `REDIS_PLUGIN_ACCEPTANCE_REDIS_DB`, `REDIS_PLUGIN_ACCEPTANCE_REQUIRED=1`, `REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_ISOLATED_TEST_RESOURCES`. | Два реальных `redis.asyncio` клиента; NX/Lua/GET/SET/SCAN без подмены, MCP replay/conflict, job lease renewal/uncertainty и exact quota. Namespace-only cleanup, никакого ambient DB 0/proxy. **Выполнено:** **22 collected / 22 passed / 0 skipped** ([Redis JUnit](../../reports/module-audit/postgres-redis/redis-plugin-acceptance-junit.xml), [summary](../../reports/module-audit/postgres-redis/summary.md)); production/live evidence не заявляется. |
| `tests/integration/kafka_plugin_acceptance` | `python -m pytest -p tests.integration.kafka_plugin_acceptance.acceptance_plugin --confcutdir=tests/integration/kafka_plugin_acceptance tests/integration/kafka_plugin_acceptance -q`. Env: `KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS`, `KAFKA_PLUGIN_ACCEPTANCE_DATABASE_DSN`, `KAFKA_PLUGIN_ACCEPTANCE_REDIS_DSN`, `KAFKA_PLUGIN_ACCEPTANCE_SECRET_ROOT`, `KAFKA_PLUGIN_ACCEPTANCE_EVENT_SIGNING_SECRET_REF`, `KAFKA_PLUGIN_ACCEPTANCE_CLAIMS_SIGNING_SECRET_REF`, `KAFKA_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_ISOLATED_TEST_RESOURCES`, `KAFKA_PLUGIN_ACCEPTANCE_REQUIRED=1`; для SASL также `KAFKA_PLUGIN_ACCEPTANCE_SASL_USERNAME_REF`, `KAFKA_PLUGIN_ACCEPTANCE_SASL_PASSWORD_REF`, `KAFKA_PLUGIN_ACCEPTANCE_TLS_CA_FILE`, `KAFKA_PLUGIN_ACCEPTANCE_SASL_MECHANISM`; для complete run `KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE=1`. | **Выполнено на disposable Kafka/Redpanda + PostgreSQL + Redis + localhost backend с independent signing keys:** **16 collected / 16 passed / 0 skipped** ([JUnit](../../reports/module-audit/kafka/junit-final.xml), [summary](../../reports/module-audit/kafka/acceptance-summary.json)); targeted units — **23 passed** ([JUnit](../../reports/module-audit/kafka/unit-kafka-adapters.xml)). Mandatory after-wake typed stale denial и owner-only recovery без xfail подтверждены ([rebalance evidence](../../reports/module-audit/kafka/rebalance-evidence.log)). Это не production/live evidence; real SealedSecret и K8s wake остаются pending. |
| `tests/integration/minio_plugin_acceptance` | `python -m pytest --confcutdir=tests/integration/minio_plugin_acceptance tests/integration/minio_plugin_acceptance -q`. Env: `MINIO_PLUGIN_ACCEPTANCE_ENDPOINT`, `MINIO_PLUGIN_ACCEPTANCE_ROOT_ACCESS_KEY`, `MINIO_PLUGIN_ACCEPTANCE_ROOT_SECRET_KEY`, `MINIO_PLUGIN_ACCEPTANCE_ADMIN_SECRET_KEY`, `MINIO_PLUGIN_ACCEPTANCE_PRESIGN_SECRET_KEY`, `MINIO_PLUGIN_ACCEPTANCE_MC_BINARY`, `MINIO_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_DISPOSABLE_MINIO_RESOURCES`, `MINIO_PLUGIN_ACCEPTANCE_REQUIRED=1`; optional `MINIO_PLUGIN_ACCEPTANCE_REGION`. | Disposable buckets/users/policies/fixed zero-byte objects, STS canary/presign/TTL expiry, NoSuchBucket/NoSuchKey unwired. `mc` получает allowlist env; HTTPX `trust_env=False`, boto3 без ambient proxy; полные URL/secrets не логируются. |
| `tests/e2e/k8s/plugin_acceptance` | `python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini --confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance -v`. Env: `PLUGIN_ACCEPTANCE_KUBECONFIG` вне репо и `PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX`; опционально bounded `PLUGIN_ACCEPTANCE_KUBE_CONTEXT`, `PLUGIN_ACCEPTANCE_RUN_ID`, `PLUGIN_ACCEPTANCE_TTL_SECONDS`, `PLUGIN_ACCEPTANCE_TOKEN_TTL_SECONDS`, `PLUGIN_ACCEPTANCE_API_TIMEOUT_SECONDS`. | Real scoped admin/Control-plane TokenRequest, RBAC deny/refresh/shutdown; Deployment owner/digest fences и sibling volumes/mounts; lifecycle+delivery Secret CAS/stale-409, rotation/drift/stale keys/readOnly 0400. Logs содержат только ids/digests/status, не DSN/secrets/tokens. Context/namespace/shared-contour guards и TTL owner cleanup обязательны. **Выполнено:** fresh disposable kind v1.37.0, **19 collected / 19 passed / 0 skipped** ([fix-report](../../reports/module-audit/k8s/fix-report.md), [JUnit](../../reports/module-audit/k8s/plugin-k8s-junit-fixed-rerun.xml)); targeted runtime units — **101 passed**. |
| `tests/integration/entrypoint_acceptance` | `python -m pytest -p tests.integration.entrypoint_acceptance.acceptance_plugin --confcutdir=tests/integration/entrypoint_acceptance tests/integration/entrypoint_acceptance -q --no-header --strict-markers -rs --junitxml=entrypoint-acceptance-junit.xml`. Env: `ENTRYPOINT_ACCEPTANCE_REQUIRED=1`, `ENTRYPOINT_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_DISPOSABLE_ENTRYPOINT_RESOURCES`, `ENTRYPOINT_ACCEPTANCE_POSTGRES_ADMIN_DSN`, `ENTRYPOINT_ACCEPTANCE_POSTGRES_DSN`, `ENTRYPOINT_ACCEPTANCE_REDIS_URL`, `ENTRYPOINT_ACCEPTANCE_REDIS_DB`, `ENTRYPOINT_ACCEPTANCE_MONGODB_URL`, `ENTRYPOINT_ACCEPTANCE_MONGODB_DB`, `ENTRYPOINT_ACCEPTANCE_KAFKA_BOOTSTRAP_SERVERS`, `ENTRYPOINT_ACCEPTANCE_KAFKA_SASL_USERNAME`, `ENTRYPOINT_ACCEPTANCE_KAFKA_SASL_PASSWORD`, `ENTRYPOINT_ACCEPTANCE_KAFKA_TLS_CA_FILE`, `ENTRYPOINT_ACCEPTANCE_KAFKA_SASL_MECHANISM`, `ENTRYPOINT_ACCEPTANCE_MINIO_ENDPOINT`, `ENTRYPOINT_ACCEPTANCE_MINIO_ROOT_ACCESS_KEY`, `ENTRYPOINT_ACCEPTANCE_MINIO_ROOT_SECRET_KEY`, `ENTRYPOINT_ACCEPTANCE_MINIO_MC_BINARY`, `ENTRYPOINT_ACCEPTANCE_MINIO_REGION`, `ENTRYPOINT_ACCEPTANCE_TEST_BUNDLE_REF`, `ENTRYPOINT_ACCEPTANCE_TEST_BACKEND_REF`, `ENTRYPOINT_ACCEPTANCE_TEST_PLUGIN_ID`, `ENTRYPOINT_ACCEPTANCE_TEST_VERSION`, `ENTRYPOINT_ACCEPTANCE_TEST_SOURCE_ID`, `ENTRYPOINT_ACCEPTANCE_TEST_SOURCE_REPOSITORY`, `ENTRYPOINT_ACCEPTANCE_TEST_COMMIT_SHA`, `ENTRYPOINT_ACCEPTANCE_TEST_RUN_URL`, `ENTRYPOINT_ACCEPTANCE_TEST_SCAN_EVIDENCE`, `ENTRYPOINT_ACCEPTANCE_TEST_CSP_EVIDENCE`, `ENTRYPOINT_ACCEPTANCE_COSIGN_IDENTITY`, `ENTRYPOINT_ACCEPTANCE_COSIGN_ISSUER`, `ENTRYPOINT_ACCEPTANCE_COSIGN_BINARY`, `ENTRYPOINT_ACCEPTANCE_COMPANY_ID`, `ENTRYPOINT_ACCEPTANCE_FOREIGN_COMPANY_ID`, `ENTRYPOINT_ACCEPTANCE_PLUGIN_INSTANCE_ID`, `ENTRYPOINT_ACCEPTANCE_FOREIGN_PLUGIN_INSTANCE_ID`, `ENTRYPOINT_ACCEPTANCE_GRANT_ID`, `ENTRYPOINT_ACCEPTANCE_FOREIGN_GRANT_ID`, `ENTRYPOINT_ACCEPTANCE_PROJECT_ID`, `ENTRYPOINT_ACCEPTANCE_PROVIDER_JOB_ID`, `ENTRYPOINT_ACCEPTANCE_CABINET_ID`, `ENTRYPOINT_ACCEPTANCE_K8S_NAMESPACE`, `ENTRYPOINT_ACCEPTANCE_K8S_STATE_CONFIGMAP`, `ENTRYPOINT_ACCEPTANCE_K8S_SERVICE_HOST`, `ENTRYPOINT_ACCEPTANCE_K8S_SERVICE_PORT`, `ENTRYPOINT_ACCEPTANCE_INTERNAL_GATEWAY_URL_TEMPLATE`, `ENTRYPOINT_ACCEPTANCE_STARTUP_TIMEOUT_SECONDS`, `ENTRYPOINT_ACCEPTANCE_OPERATION_TIMEOUT_SECONDS`, `ENTRYPOINT_ACCEPTANCE_SHUTDOWN_TIMEOUT_SECONDS`. Полная матрица — [README](../../apps/api/tests/integration/entrypoint_acceptance/README.md). | Два реальных uvicorn entrypoints + standalone Celery prefork, production SQL/Redis/Mongo/Kafka/MinIO/K8s, одинаковый verified `PLUGIN_JOB_DECLARATIONS`, scoped tokens, update lock, MCP/jobs/events и private/public bootstrap. Subprocess env allowlist удаляет application/shared/proxy variables. |

Ни один harness не закрывает production `PLUGIN_JOB_DECLARATIONS`, real SealedSecret,
CI/hello build/new signed bundle, live STS inputs или human enablement. Для K8s-only
пакета `SqlPluginUpdateLock` намеренно не подменяется: он проверяется только в
PostgreSQL/entrypoint контурах.
## Исторические claims — НЕ текущие зелёные результаты

Прежние результаты от 2026-11-08 сохранены только как **unverified historical
claims**, без подтверждения на текущем worktree:

| Исторический контур | Что было записано ранее | Как читать сейчас |
|---|---|---|
| API tests | `pytest tests/acceptance tests/unit -q` → **4225 passed**, 0 failed | Не текущий прогон и не live plugin proof |
| Web tests | `pnpm exec vitest run` → **785 passed**, 0 errors | Unverified historical claim |
| infra/ops tests | `pytest infra/ops/tests -q` → **106 passed**, 0 failed | Unverified historical claim |
| Web compile/build | `tsc --noEmit` и `pnpm run build` → success | Не запускались в этой правке |
| GitOps/YAML/Alembic/import smoke | kustomize, YAML parse, clean Postgres upgrade/check, composition и TestClient smoke | Исторические отчёты, не новые integration/live evidence |
| Dev cluster | Ранее записанные `Running`, `Synced/Healthy`, Completed jobs и topics | Снимок на прошлую дату, не текущее live состояние |

## Что всё ещё не засчитывается как vertical slice

- ревизии и plugin-таблицы сами по себе;
- classes, routes, manifests, feature flags и wiring сами по себе;
- unit tests, TestClient, stateful/fake IO boundaries и незапущенные production
  composition tests;
- наличие hello frontend/backend/Liquibase;
- прошлые Running/Healthy или локальные зелёные команды;
- completed legacy host static cleanup без запущенных guards и browser checks.

## Cutover guardrails

- Pending `2026110802` остаётся dormant/not active: не переносить, не копировать,
  не регистрировать и не применять; никакой env flag не разрешает активацию.
- `strangler/removal.py` и `CODE_FIRST_REMOVAL_INVENTORY` не выполняются: это только
  S4 review inventory. `cutover.py` и его `advance()`/`complete_current_step()`
  изменяют лишь metadata cursor/flags; REMOVAL и `declarative_app_removed=True`
  inert и не являются evidence фактического removal или разрешением на него.
- `product_module_upsert`, product seeds и `management_module_merge`
  сохраняются для fresh/historical upgrades. Applied migrations не редактируются.
- Порядок — code-first expand/contract A→B, а не drop-first. Сначала отдельный S4
  удаляет legacy readers/writers/routes/imports и согласует ORM metadata с отдельными
  evidence; затем отдельный S5 ADR/review/acceptance проектирует новую revision от
  тогдашней current head со schema-only downgrade и без env guard. Подробности в
  [safe-cutover документе](plugin-cutover-safety.md).
- Completed static host cleanup и metadata progression не снимают S4/S5 barriers и
  не удаляют сохранённый dynamic/declarative renderer inventory.

**Проверки при этой документной правке намеренно не запускались:** tests,
build, lint, compile, git status/diff и live cluster verification. Commit/push и
обращения к live/prod также не выполнялись.
