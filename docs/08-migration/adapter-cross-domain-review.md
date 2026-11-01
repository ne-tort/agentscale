# Cross-domain static review: M04 -> M05 -> M06 adapters

Статус: migration-stage static review, documentation-only. Baseline: 2026-11-01.

Охват ограничен application adapters: `dynamic_apps_definition_adapter`, `scope_registry_adapter`, `plugin_instances_adapter`, `canonical_grants_method_adapter`, `canonical_grants_persistence_adapter` и promotion surface `canonical_grants_promotion` (отдельного файла `canonical_grants_promotion_adapter` в application tree нет). Проверялся только boundary handoff M04 -> M05 -> M06: tenant/app/version/definition/artifact/scope/plugin/subject/session/generation/lease/correlation identity, quarantine, positive-write gates, async/read/CAS semantics и promotion evidence.

## Findings

### F-01 [Critical] Promotion finalization mutates an append-only evidence identity

`CanonicalGrantsPromotionService.reviewed_final()` возвращает `dataclasses.replace(record, ...)` и сохраняет исходные `record_id` и `correlation_id`, одновременно меняя `outcome`, `reviewed_final`, `reviewer` и `canonical_positive_writes` to `true` (`apps/api/src/prodavan/application/canonical_grants_promotion/service.py:108-123`). Это противоречит контракту promotion records как immutable append-only evidence (`contracts.py:1-5`, `PromotionRecordAppendPort`), а persistence adapter может принять такой объект как очередное evidence без проверки новой версии/идентичности. В результате pre-cutover record с `canonical_positive_writes=false` может быть представлен как тот же самый record, уже разрешающий positive writes; audit/replay не получает однозначного owner-transition события.

**Handoff impact:** M06 persistence -> promotion/cutover. Promotion evidence нельзя считать достаточным для canonical ownership, пока final review не создаёт новый append-only record/version и не связывает его с предыдущим record.

**Required disposition:** implementation/review must make finalization append a new immutable record identity (с parent/previous record reference), reject mutation/reuse of prior `record_id`, and require the persistence boundary to enforce append-only semantics.

### F-02 [High] M04 plugin scope shape disagrees with the cross-domain installation contract

`ScopeAnchors` rejects `cabinet_id` and `project_id` for `ScopeKind.PLUGIN_INSTANCE` (`apps/api/src/prodavan/application/scope_registry/contracts.py:68-73`). The M04-M06 contract requires plugin-instance scope to carry/mirror installation cabinet/project anchors where applicable (`docs/08-migration/m04-m06-adapter-implementation.md:205-211`; `m04-m06-readiness-matrix.md:104-109`). The scope adapter also deliberately quarantines every plugin-instance registration as `plugin_instance_deferred_m05` (`scope_registry/service.py:72-74`, `101-106`), while M05 identity only carries `scope_kind/scope_id` and does not receive typed installation anchors (`plugin_instances/contracts.py:40-61`).

**Handoff impact:** M04 -> M05 -> M06. A project/cabinet-bound instance cannot pass the typed scope proof needed by M06; alternatively, dropping the anchors loses installation ownership evidence. This is a contract mismatch, not merely a deferred implementation detail.

**Required disposition:** reconcile one canonical plugin scope shape across M04 registry, M05 instance identity/FK and M06 grant identity before enabling plugin-instance positive paths. Until then plugin-instance rows must remain quarantine/deny and cannot count as positive parity.

### F-03 [High] M05 CAS adapter accepts a narrow mutation key without full cross-domain identity

`TrustedPluginInstanceRepository.acquire_lease()` and `advance_writer()` send CAS requests containing only `plugin_instance_id`, owner/lease data and expected generations (`plugin_instances_adapter/repository.py:135-171`; `plugin_instances/contracts.py:128-163`). They validate returned rows for instance/generation/lease consistency, but the CAS port itself is not required to receive tenant, definition, version, scope, artifact, subject or session identity. This leaves the actual atomic mutation boundary unable to prove the full M04/M04.e identity before changing a row; post-read mapping cannot repair a mutation that already occurred.

**Handoff impact:** M05 -> M06. A stale or cross-domain runtime row could pass a narrow CAS implementation and then be exposed as current fencing evidence. M06 would receive generation/lease evidence without an atomic proof of tenant/app/version/artifact/scope binding.

**Required disposition:** CAS port/request must carry and atomically check the complete instance composite identity plus artifact and required subject/session binding; zero-row stale/conflicting mutations must be the only success boundary. The adapter should not treat a returned row as sufficient proof if the underlying CAS predicate was narrower.

### F-04 [High] Definition adapter does not propagate scope, correlation or durable quarantine evidence

`DynamicAppDefinitionRepositoryAdapter.resolve()` catches malformed ORM mapping and returns an in-memory `DefinitionResolution` with `evidence={"adapter_error": ...}` (`dynamic_apps_definition_adapter/repository.py:75-84`). The adapter has no quarantine/mapping/audit port and its resolver query only takes tenant/app/version (`repository.py:46-73`). The resolved reference carries tenant, definition, version and artifact, but no scope-registry identity, subject/session, generation/lease or correlation/checkpoint handoff.

**Handoff impact:** M04.e -> M05/M06. A malformed or ambiguous definition can be fail-closed for this call, but the source row is not durable quarantine evidence and downstream adapters cannot prove the same resolution/correlation snapshot was used. The next adapter must re-resolve scope and runtime identity without a shared evidence key; this permits evidence drift between reads.

**Required disposition:** keep this adapter read-only, but add an explicit boundary result/evidence contract (source identity, digest, reason, checkpoint, correlation) consumed by quarantine/promotion. Do not treat an in-memory negative result as completed M04 classification.

### F-05 [Medium] Scope positive writes are exposed before the M04 evidence gate and lack cross-domain CAS/idempotency

`ScopeRegistryServiceAdapter` exposes `register_active_scope()` directly (`scope_registry_adapter/service_adapter.py:62-73`), and `ScopeRegistryService` calls `put_scope(entry)` after local parent proof (`scope_registry/service.py:119-126`). The application boundary does not require source digest conflict checking, active-row uniqueness/CAS, checkpoint, audit append, or a migration writer generation. It can therefore create an active registry row before the documented M04 gate has classified all retained anchors and before M05 reciprocal instance closure.

**Handoff impact:** M04 -> M05. An active row written through this adapter is not necessarily durable migration evidence and can be mistaken by M05/M06 as a trusted parent. Plugin rows are deferred, but ordinary rows also lack a durable gate marker.

**Required disposition:** separate evidence/backfill registration from positive authorization writes; require idempotent source identity/digest comparison, append-only quarantine/audit, writer fencing and an explicit gate state. Until then active registry writes must not be consumed as promotion evidence.

### F-06 [Medium] Method adapter contract allows an unproven direct grant handoff

`CanonicalGrantsMethodRequest` requires `company_id`, app/version, plugin instance and MCP correlation, but does not carry resolved `tenant_id`, `app_definition_id`, immutable artifact, typed scope, subject/session, lease or generation (`canonical_grants_method_adapter/contracts.py:49-66`). The method adapter forwards caller-facing company/app/version/plugin values to `CanonicalGrantRequest` and only forwards optional fencing (`service.py:57-71`). The underlying gateway may resolve some identity, but the method adapter port itself does not require that proof or a shared resolution snapshot.

**Handoff impact:** M05 -> M06. A direct use of this adapter can reach a grant decision with incomplete identity and no explicit quarantine propagation for missing scope/subject/session; it relies on downstream implementation discipline rather than a type-level boundary. Correlation is propagated to mapping/grant request, but not tenant/definition/artifact/generation evidence.

**Required disposition:** require a resolved context object (or equivalent) containing the complete identity and gate evidence before grant authorization; reject missing context as deny/quarantine. Preserve request assertions separately for mismatch audit.

### F-07 [Medium] Persistence adapter has no atomic multi-evidence transaction or identity completeness check

`TrustedEvidenceRepository.append_evidence()` accepts optional promotion, quarantine and audit values (`canonical_grants_persistence_adapter/ports.py:31-37`), but `CanonicalGrantsPersistenceAdapter` appends each record through separate calls (`adapter.py:44-55`). No transaction/session or atomic grouping is required, and the adapter does not validate that audit/promotion evidence shares partition, source/canonical/binding identity, or the complete tenant/app/version/scope/plugin/subject/session/generation/lease envelope.

**Handoff impact:** M06 adapter -> promotion/audit persistence. A promotion record can be durable while its quarantine or audit counterpart fails, or an audit outcome can be stored without the identity snapshot used for the decision. This weakens promotion evidence and correlation replay even though positive grant writes are blocked.

**Required disposition:** define an atomic evidence transaction for linked records, or make each record self-contained with required parent/evidence IDs and durable retry semantics. Reject incomplete identity/correlation envelopes before append; audit failure must fail the enclosing evidence decision.

### F-08 [Medium] Promotion gate evaluation can replace supplied evidence and promote on an untrusted evaluator result

`collect_pre_cutover()` merges source evidence and then applies `merged.update(evaluated)` from `PromotionDecisionPort` (`canonical_grants_promotion/service.py:51-69`). Only missing gates are restored to `passed=false`; an evaluator can replace an existing `{passed: false}` source gate with `{passed: true}` without a separately validated evidence lineage. The comment says the port cannot manufacture missing proof, but it does not prevent replacement of contradictory proof.

**Handoff impact:** M06 persistence -> promotion. A gate evaluator can turn contradictory or failed M04/M05 evidence into `PROMOTABLE`; `reviewed_final()` then enables canonical positive writes once all resulting values read as passed.

**Required disposition:** gate evaluation may add derived evidence only under a distinct derived-evidence namespace with source references. Existing failed/contradictory gates must remain failed and block promotion; every promoted gate needs immutable evidence ID/digest and lineage.

### F-09 [Low] Promotion contracts do not validate partition/identity completeness for narrowed scopes

`PromotionPartitionKey` validates allowed scope names and only enforces `scope_id == plugin_instance_id` for `plugin_instance` (`canonical_grants_promotion/contracts.py:70-92`). It does not require plugin instance identity for non-plugin narrowed partitions, nor cabinet/project installation anchors, definition artifact/version, subject/session, lease or runtime generation. `PromotionRecord` validates digest syntax and gate-key presence but not that gate evidence contains those identities (`contracts.py:94-150`).

**Handoff impact:** promotion records can be syntactically valid yet not bindable to the exact M04/M05/M06 partition they claim to promote. A live status/reviewer can see all required keys while identity evidence remains absent or unrelated.

**Required disposition:** require identity/binding evidence IDs and digest-linked partition context in the promotion record; validate exact plugin/scope/definition/version/subject/session/generation/lease fields where the policy requires them.

## Positive-write and quarantine conclusion

No inspected adapter provides evidence that canonical positive grant writes are safe to enable globally. The current application surfaces are predominantly fail-closed/read-only, and promotion/persistence explicitly reject pre-review `canonical_positive_writes`; however, F-01 and F-08 make the promotion path unsafe as an authorization of cutover until immutable evidence lineage is fixed. F-02 and F-03 prevent a complete M04 -> M05 -> M06 identity handoff. Quarantine is generally propagated as an in-memory or append-only record, but F-04 and F-05 show that definition/scope classification is not consistently durable, idempotent and fenced.

## Inspected files

- `apps/api/src/prodavan/application/dynamic_apps_definition_adapter/repository.py`
- `apps/api/src/prodavan/application/dynamic_apps_definition/service.py`
- `apps/api/src/prodavan/application/scope_registry_adapter/service_adapter.py`
- `apps/api/src/prodavan/application/scope_registry/service.py`
- `apps/api/src/prodavan/application/scope_registry/contracts.py`
- `apps/api/src/prodavan/application/plugin_instances_adapter/repository.py`
- `apps/api/src/prodavan/application/plugin_instances_adapter/service.py`
- `apps/api/src/prodavan/application/plugin_instances/service.py`
- `apps/api/src/prodavan/application/plugin_instances/contracts.py`
- `apps/api/src/prodavan/application/canonical_grants_method_adapter/contracts.py`
- `apps/api/src/prodavan/application/canonical_grants_method_adapter/service.py`
- `apps/api/src/prodavan/application/canonical_grants_persistence_adapter/adapter.py`
- `apps/api/src/prodavan/application/canonical_grants_persistence_adapter/ports.py`
- `apps/api/src/prodavan/application/canonical_grants_promotion/contracts.py`
- `apps/api/src/prodavan/application/canonical_grants_promotion/service.py`
- `apps/api/src/prodavan/application/canonical_grants_promotion/ports.py`
- `apps/api/src/prodavan/application/canonical_grants/service.py`
- Related unit-contract tests were inspected but not executed.

## Verification freeze

Tests, build, lint, format, compile, Alembic/migration execution, environment changes and all git checks were intentionally not run. No code, ORM, shared environment or database was changed. This review is static only; findings remain unresolved until implementation and a later explicit verification decision.
