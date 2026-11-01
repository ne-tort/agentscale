# T08 dynamic plugin gateway: runtime gaps and integration gates

Статус: migration-stage gap report, documentation-only. Baseline: 2026-11-01.

Документ фиксирует разрыв между текущим `dynamic_app_plugin_gateway` и контрактами M04/M05/M06 для dynamic plugin gateway. Он сверяет T08 application code, M05 plugin-instance/runtime fencing, M06 canonical grant and method-mapping contracts и shared durable identity envelope. Документ не меняет code/core docs, не является разрешением на route exposure, canonical positive writes, runtime publication, backfill или cutover.

## 1. Inspected boundary and current behavior

Проверены:

- `apps/api/src/prodavan/application/dynamic_app_plugin_gateway/contracts.py`, `ports.py`, `service.py`;
- `apps/api/tests/unit/test_dynamic_app_plugin_gateway.py` как source-level contract T08;
- `apps/api/src/prodavan/application/plugin_instances/contracts.py` и `service.py` как M05 identity/fencing boundary;
- `apps/api/src/prodavan/application/capability_method_mapping/contracts.py` и `service.py`;
- `apps/api/src/prodavan/application/canonical_grants_method_adapter/contracts.py`, `ports.py`, `service.py` как M06 method-to-grant composition;
- `apps/api/src/prodavan/application/shared_identity/contracts.py` и `docs/08-migration/shared-durable-identity-envelope.md`;
- `docs/08-migration/m04-m06-adapter-implementation.md`, `m06-trusted-persistence-adapter.md`, `m06-integration-readiness.md`, `implementation-wave-cross-review.md`, `dynamic-grants-binding-gaps.md` и `dynamic-grants-ownership.md`.

Текущий T08 делает полезные локальные проверки: на `open` он разрешает definition/version/instance/grants, exact scope, lifecycle и manifest methods; на `invoke` повторно читает запись, проверяет drift, exact method, requested capability, grant constraints, scope и quota до executor. Ошибка repository/scope/quota/executor может уйти в quarantine при наличии callback, а negative `open` не возвращает handle.

Эта локальная защита не образует M04/M05/M06 trusted handoff. T08 должен оставаться deny/quarantine или shadow-only до закрытия следующих разрывов.

## 2. Gap inventory

| Gap | Что есть в T08 | Почему этого недостаточно | Required closure |
|---|---|---|---|
| In-memory handle eviction | `PluginHandle` — opaque UUID-like object; `_contexts: dict[PluginHandle, PluginContext]` живёт только в процессе. Нет TTL, revoke/close, bounded size, generation-aware eviction или durable lookup. | Restart, другой worker, failover и горизонтальное масштабирование теряют handle. Handle не является replayable durable identity и не даёт доказательства, что invoke продолжает тот же runtime admission. Бесконечный словарь также не имеет эксплуатационного eviction boundary. | Handle должен ссылаться на durable/восстанавливаемый session-scoped context record или evidence ID с expiry и bounded retention. Lookup обязан проверить envelope digest, exact instance/session, current lifecycle, runtime generation, writer generation и lease; stale/expired/revoked handle — deny с audit/quarantine classification. Eviction должна быть явной policy (TTL/revoke/route change/logout/restart recovery), наблюдаемой и не превращать потерянный context в fallback allow. |
| Subject/session/fencing envelope gap | `PluginOpenRequest` содержит только `tenant_id`, app/version, `scope`, `subject_id`; `PluginContext` содержит subject и artifact/instance, но не `session_id`, cabinet/project anchors, runtime/writer generations, lease id/owner/until, correlation/trace, promotion partition или envelope digest. T08 repository `resolve()` получает request, а `invoke` повторно строит такой же узкий request. | M05 `PluginInstanceRecord` и shared `IdentityEnvelope` требуют отдельные subject и session, exact instance identity, artifact, runtime/writer fencing и lease. Session нельзя выводить из subject или instance; `plugin_instance_id` нельзя заменить app/version/scope/session. Повторная независимая resolution допускает evidence drift и не связывает T08 quarantine/audit с M04/M05/M06 snapshot. | Передавать один immutable server-resolved `IdentityEnvelope`/`IdentityContext` через open и invoke. T08 должен сохранять или ссылаться на `envelope_digest`, exact M04 definition/version/scope evidence, M05 instance evidence, subject/session, artifact, runtime/writer generations, lease и correlation/trace. Caller/plugin claims остаются assertions. При mismatch, stale generation, invalid lease, missing binding или digest conflict — deny/quarantine; запрещён fallback к app-wide grant, другой instance или новому envelope после принятия решения. |
| `grant_id` observability | `GatewayResult` имеет optional `grant_id`, но `open` его не устанавливает; method/grant lookup выбирает `CapabilityGrant`, а `invoke` возвращает `grant_id=None` даже при allow. Большинство deny paths также теряют выбранный grant. `PluginContext` хранит grants, но не canonical grant identity/generation/source mapping. | M06 audit/promotion и replay требуют canonical `capability_grant_id`, source/mapping provenance, exact decision identity и linked pre-invocation/final outcome. Без grant ID невозможно связать allow/deny, executor outcome, quarantine и promotion evidence с конкретной grant row; приложение может ошибочно воспринимать in-memory `GrantSet` как canonical authorization. | Возвращать и аудитировать resolved canonical `grant_id` только из trusted M06 decision, вместе с grant generation/schema/mapping digest/source provenance где применимо. Preserve selected ID through method, scope, quota, executor and final outcome paths; absence of a trusted ID is deny/quarantine for a path requiring canonical grant. `requestedCapabilities` и legacy grant row IDs остаются corroboration/provenance, не заменяют canonical ID. |
| Method mapping service integration | T08 берёт `method`, `capability`, `operation`, `resource`, `domain` напрямую из manifest и проверяет локальные exact constraints. M06 `CapabilityMethodMappingService` существует отдельно и выполняет versioned `MethodIdentity`, mapping digest, protocol/tool compatibility, duplicate/broadening/unknown checks; `CanonicalGrantsMethodAdapter` вызывает его перед canonical grant decision. T08 ports не содержат mapping decision port, mapping version/digest или MCP audit correlation. | Один и тот же MCP method может получить разные semantics в T08 и M06. Manifest method declaration сама по себе не является approved mapping, а absence/duplicate/unknown mapping и unsupported protocol должны quarantine. T08 может вызвать executor без M06 mapping evidence и без связанного audit envelope. | Перед capability execution T08 должен вызвать approved mapping decision service/port с `MethodIdentity(server, tool, version)`, protocol/tool versions, exact requested capability/operation/resource/domain и redacted audit correlation. Только `ALLOW` с mapping version/digest и bounded declaration может перейти в M06 canonical grant decision. `DENY`/`QUARANTINE`, unknown/duplicate/deprecated mapping, incompatible protocol/tool version и broadening constraints не вызывают executor и блокируют positive path. T08 и M06 должны использовать один mapping result/digest, а не повторно разбирать manifest. |
| Repository and runtime wiring | `DynamicPluginRepository` exposes `resolve`, plus unused/narrow `get_grants` and `get_instance`; no method requires full resolved envelope, current fence or atomic snapshot. Executor receives `context: object`; quarantine receives only T08-local `QuarantineEvidence` with tenant/app/source and string metadata. No audit or final-outcome port exists. | Port shape permits implementation to return rows that are individually plausible but not atomically bound to tenant/definition/version/artifact/scope/subject/session/fence. Optional quarantine is in-memory durability by default. There is no trusted transaction or append-only audit link, so M06 cannot use T08 result as promotion evidence. | Replace/extend the integration seam with server-resolved context, exact M04/M05/M06 snapshot/evidence IDs, runtime admission/fence decision, mapping result, canonical grant decision and append-only audit/final outcome. Repository success must be one immutable snapshot; CAS/stale results are explicit typed outcomes. Quarantine and audit failure must fail closed and cannot turn deny/system failure into allow. |
| Route and wiring gates | No T08 route/router registration was found in the inspected `apps/api/src/prodavan/api/v1/router.py` or API route surface. The application zone is not connected to authenticated request context, session resolution, identity propagation, M06 mapping/grant adapter, executor registry, audit writer or quarantine persistence. | A correct application service that is not wired cannot enforce a public gateway contract. Adding a route before the upstream gates are closed would expose an in-memory handle and incomplete identity boundary, with no stable ownership or audit semantics. | Keep the gateway unexposed until route and dependency wiring pass the gates in section 4. Route registration is a later implementation change and is outside this documentation task. |

## 3. Canonical target handoff

The target call path is one server-resolved, digest-linked chain:

```text
authenticated request/session
  -> M04 tenant + typed scope + definition/version/artifact evidence
  -> M05 exact plugin instance + subject/session + runtime/writer fence + lease
  -> shared IdentityEnvelope (digest + promotion partition)
  -> versioned capability method mapping (mapping version + digest)
  -> M06 canonical grant decision (grant_id + generation + reason)
  -> T08 runtime admission/quota/scope checks
  -> executor
  -> linked final audit outcome
```

`open` may create a handle only after the complete context is resolved and the required M06 decision/mapping evidence is available. The returned handle is an opaque reference to that evidence-bound context, not a credential and not a substitute for the envelope. `invoke` must resolve the current runtime admission against the same identity and reject stale handle/evidence, changed artifact, changed mapping, changed grant, revoked/expired session, expired lease or generation mismatch.

The shared envelope requires `tenant_id`, typed scope and anchors, definition/version, artifact digest, exact `plugin_instance_id`, `subject_id`, `session_id`, runtime/writer generations, lease identity/expiry, correlation/trace and derived promotion partition/digest. The T08 local `PluginContext` is therefore an incomplete transport object until it is replaced or explicitly linked to that envelope.

## 4. Dynamic gateway route/wiring gates

These gates are required before any route or public dependency wiring is enabled. They are partition-scoped and must produce named evidence; a live feature flag or successful service construction is not evidence.

### 4.1 Request and route boundary

- [ ] Route is mounted only under the authenticated API router and has an explicit request/session dependency; no anonymous or plugin-supplied tenant/subject/session is trusted.
- [ ] Open and invoke request DTOs distinguish authenticated server context from caller assertions. Route cannot accept a raw `PluginHandle` as a bearer credential or expose internal context fields.
- [ ] Route scope resolves through typed M04 owner/scope boundary with cabinet/project parent checks where applicable; no copied `company_id` fallback.
- [ ] Handle lifecycle is explicit: expiry, close/revoke, logout/session termination, route/scope change, runtime restart and bounded eviction all deny subsequent invoke and produce an observable reason.
- [ ] Multi-worker/restart behavior is specified and tested at the contract level before exposure; an in-memory-only lookup is not route-ready.

### 4.2 Dependency and identity wiring

- [ ] Dependency graph provides one M04/M05 resolved snapshot and shared `IdentityEnvelope`, not independent T08 reads assembled from request strings.
- [ ] M05 runtime admission verifies exact instance, artifact, subject/session, lifecycle, current runtime generation, writer generation and lease. Stale CAS/fence affects zero rows and is surfaced as deny/quarantine.
- [ ] T08 uses the same envelope digest and promotion partition for repository, mapping, grant, executor and audit calls; downstream boundaries cannot broaden or substitute identity.
- [ ] Quarantine and append-only audit writers are required dependencies for positive route paths. Optional `quarantine=None` semantics may remain only for explicit read/contract fixtures, never for production allow.
- [ ] Executor registry is capability-keyed and receives the trusted resolved context plus redacted correlation, never a caller-owned identity object or raw token.

### 4.3 Mapping and grant wiring

- [ ] Each method request resolves through the versioned method-mapping service with full `MethodIdentity`, protocol/tool compatibility and mapping audit metadata.
- [ ] Only mapping `ALLOW` with a verified mapping digest can reach the M06 canonical grant adapter; mapping `DENY`/`QUARANTINE` is terminal for the invocation.
- [ ] M06 grant decision is the authorization owner for canonical paths. Legacy `AppGrant`/`GrantSet` and manifest `requestedCapabilities` are corroboration/provenance only.
- [ ] `grant_id` and grant generation are preserved from the selected canonical decision into `GatewayResult`, pre-invocation audit, executor outcome and final audit; an allowed result without trusted grant identity is impossible.
- [ ] Pre-invocation allow and final executor result are separate linked events with request/trace/span, envelope digest, mapping digest/version and grant ID. Successful authorization is not reported as execution success.

### 4.4 Persistence, promotion and cutover

- [ ] T08 evidence is append-only, tenant-scoped and linked to source/canonical/mapping digests, checkpoint and promotion partition. Quarantine is visible/countable/retryable and never an authorization input.
- [ ] No route wiring enables canonical positive writes, published runtime transition or cutover while M04/M05/M06 evidence is incomplete, mapping unknown, binding unresolved, parity mismatched or writer unfenced.
- [ ] Promotion record includes T08 route/wiring evidence, handle lifecycle policy, runtime admission/fencing proof, mapping/grant/audit linkage and explicit `canonical_positive_writes=false` until the reviewed final enable decision.
- [ ] `dynamic_app_grants` remains read-only compatibility projection during the migration window; there is exactly one authorization owner for a promoted partition.

## 5. Acceptance evidence to add later

The following checks belong to implementation/review after this documentation-only gap report:

1. A handle survives worker restart only when its durable evidence is valid; expired, evicted, revoked and foreign handles deny without executor invocation.
2. Subject/session, artifact, instance, runtime generation, writer generation, lease and envelope digest mismatches classify as deny/quarantine and cannot select a fallback instance or grant.
3. Method mapping unknown, duplicate, broadening, unsupported protocol and incompatible tool version never reach M06 grant or executor; mapping digest is identical in T08 and M06 evidence.
4. Every allow/deny/final executor outcome contains the canonical `grant_id` when a grant was evaluated, with no fabricated ID for a legacy or in-memory-only grant.
5. A route call cannot cross tenant/cabinet/project/session or route scope, and route registration is absent from production wiring until all upstream gates are evidenced.
6. Audit/quarantine persistence failure fails closed; no positive result is returned after an evidence append failure.

These are acceptance requirements, not executed checks during the current migration verification freeze.

## 6. Unresolved blockers and verification freeze

- T08 handle/context is process-local and has no durable eviction/recovery contract.
- T08 `PluginOpenRequest`/`PluginContext` and repository port do not carry the complete shared subject/session/fencing/lease/envelope identity required by M05 and M06.
- `GatewayResult.grant_id` is not populated for the successful T08 invoke path, and T08 does not preserve canonical grant generation/provenance.
- T08 does not integrate the versioned M06 method-mapping service or the `CanonicalGrantsMethodAdapter`; manifest method declarations remain a separate local decision path.
- T08 has no required append-only audit/final-outcome port and its quarantine evidence is narrower than the shared durable quarantine envelope.
- No API route/dependency wiring for this dynamic gateway was found in the inspected v1 router surface; exposure, authentication, session resolution and executor wiring remain unimplemented gates.
- M04/M05/M06 upstream composite identity, trusted transaction, durable mapping/quarantine/audit, RLS and promotion evidence remain prerequisites. The shared envelope contract does not itself authorize positive writes or cutover.

For this documentation-only change, backend/frontend tests, build, compile, lint, formatting, Alembic, migration execution, database/environment changes and all git checks were intentionally not run. Only this document is changed; code, core docs, routes, ORM, shared environments and databases were not changed. Verification remains frozen until the migration is implemented and reviewed, followed by an explicit decision to lift the freeze.
