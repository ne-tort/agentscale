# Migration execution backlog

Status: executable backlog after design phase; documentation-only planning artifact. Baseline: 2026-11-01.

This backlog turns the reviewed migration contracts into bounded subagent tasks. A task may change only its exclusive file zone and must leave a report before another task consumes its deliverables. No task authorizes shared-environment DDL, backfill, positive writes, cutover, cleanup, or production promotion by itself.

## Execution rules

- `M04` parent scope, `M04.e` definition/version, `M05` instance/runtime, and `M06` grants/gateway are strict dependencies in that order. Other groups may prepare contracts or empty foundations in parallel only when they cannot enable a positive path early.
- Each task has one exclusive file zone. A task may read other zones and existing design docs, but it must not edit them. If an interface needs a change outside the zone, record a blocker and stop at the boundary.
- Source acceptance artifacts are written as tests, fixtures, static checks, migration evidence schemas, or review records. They are designed during verification freeze and are explicitly **not executed**.
- Every task produces a promotion record, even when the result is `blocked` or `quarantined`. A promotion record never means production promotion; it records readiness for the next bounded gate.
- Every parent report is mandatory: task ID, status, inspected/changed files, dependencies consumed, deliverables, acceptance artifacts, promotion record location/content, rollback/gate result, unresolved risks/blockers, and confirmation that tests/build/lint/compile/git checks were not run.

## Common task contract

Each task below must deliver all of the following in its own zone:

1. implementation or migration artifact plus an explicit source/target ownership boundary;
2. deterministic, resumable, idempotent behavior with source identity, schema version, payload digest, checkpoint and redacted audit reason;
3. acceptance artifacts named in the task, including negative/cross-scope/failure cases;
4. immutable promotion record keyed by the smallest bounded partition (normally `(tenant_id, app_definition_id)`, narrowed to scope/instance where required), retaining source snapshot/schema, source and target digests, checkpoint, gate evidence, actor/time, decision owner, writer/fencing generation and correlation ID;
5. rollback plan that preserves evidence and leaves the legacy owner authoritative until the stated gate passes;
6. mandatory parent report using the format above.

A task is `ready` only after static review of its contract and artifacts. A task is `promotable` only when its gate, all upstream gates, and the partition promotion record are complete. Known deny, quarantine, or unsupported data is evidence and never positive parity.

## 1. M04 parent scope

### MIG-M04-01 — canonical tenant and typed anchor preparation

- **Exclusive file zone:** `apps/api/src/prodavan/infrastructure/persistence/models/owner_registry.py`; new migration model helpers only in `apps/api/src/prodavan/infrastructure/persistence/models/owner_registry/**`.
- **Dependencies:** design contract in `docs/08-migration/canonical-tenant-scope-registry.md`, `typed-scope-owner-registry.md`, and `m04-m06-readiness-matrix.md`; current `companies`, `cabinet_instances`, `projects`, and membership models are read-only inputs.
- **Deliverables:** SQLAlchemy foundation for canonical tenant ID/type, typed scope anchors, named uniqueness/check constraints, and non-destructive deletion semantics; no plugin-instance positive branch and no grant table.
- **Source acceptance/test artifacts (not executed):** model-level constraint review; fixture matrix for tenant/cabinet/project valid and impossible shapes; overlength, malformed, orphan, cross-tenant and project-company-cabinet mismatch cases; RLS policy review artifact.
- **Promotion record requirements:** source inventory digest; accepted/quarantined ID counts; constraint names and planned Alembic revision slot; proof that no positive writer is enabled; owner and reviewer signatures.
- **Rollback/gate:** before import, remove only empty foundation objects after dependency inventory; after import, stop the partition and repair forward while retaining mappings/quarantine/audit. Gate is canonical tenant plus deterministic parent compatibility; plugin scope remains deferred.
- **Mandatory parent report:** use common contract and explicitly state unresolved membership ambiguity, hard-delete risks, and verification freeze.

### MIG-M04-02 — scope registry mapping and quarantine job

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/m04_scope/**` and its task-owned fixtures under `apps/api/tests/migration/m04_scope/**`.
- **Dependencies:** MIG-M04-01; active membership/access proof; no dependency on M05 instance identity.
- **Deliverables:** resumable per-source-row registry backfill, owner mapping, quarantine and append-only redacted audit records; active-only `ResolveScopePort`; plugin-instance rows deny/deferred.
- **Source acceptance/test artifacts (not executed):** fixture and static acceptance definitions for idempotent replay, changed digest conflict, missing/deleted parent, ambiguous cabinet/company, project mismatch, cross-tenant relation, archived/quarantined exclusion, and missing transaction-local tenant context.
- **Promotion record requirements:** source schema/digest/checkpoint, counts by scope kind and reason code, exactly-one-active-row evidence, membership and parent compatibility evidence, RLS review, legacy read/write owner.
- **Rollback/gate:** halt only affected partition; preserve evidence and legacy authority; never rewrite scope identity to hide installation changes. Gate requires zero unresolved required parent mappings for the partition and no plugin positive resolution.
- **Mandatory parent report:** common contract plus partition status and any blocked source identities.

## 2. M04.e definition/version

### MIG-M04E-01 — definition/version keys and immutable artifact foundation

- **Exclusive file zone:** `apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app_definition_version.py`; matching ORM/migration model additions only in that file zone.
- **Dependencies:** MIG-M04-01 and accepted retained tenant/scope evidence; contracts in `owner-registry-implementation-plan.md` and `m04-m06-adapter-implementation.md`.
- **Deliverables:** definition owner key `(id, tenant_id)`, same-definition version key, deterministic manifest-version uniqueness, lifecycle checks, immutable artifact digest proof, and staged current-version cycle plan. Do not create canonical grants.
- **Source acceptance/test artifacts (not executed):** static schema review; fixtures for orphan/cross-tenant/cross-definition versions, duplicate manifest versions, changed artifact digest, invalid lifecycle/current-version cycle; named deferred-FK validation and downgrade inventory.
- **Promotion record requirements:** definition/version source snapshot and digests, valid/archived/quarantined counts, current-version resolution evidence, artifact equality evidence, no-positive-write flag, reviewer and revision lineage.
- **Rollback/gate:** before evidence import, remove only empty new constraints; after import, stop mapping and repair forward, preserving evidence and legacy reads/writes. Gate requires zero unresolved required definition/version rows in the partition and deterministic `(definition, manifest_version)` resolution.
- **Mandatory parent report:** common contract plus any canonical ID conversion or artifact immutability blocker.

### MIG-M04E-02 — definition/version evidence backfill and resolver

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/m04e_definition_version/**` and `apps/api/tests/migration/m04e_definition_version/**`.
- **Dependencies:** MIG-M04E-01 and active typed parent scope from M04; M05 and M06 are downstream only.
- **Deliverables:** bounded definitions-then-versions backfill, conflict quarantine, server-side `DefinitionVersionRepository` resolution, and evidence export consumed by M05.
- **Source acceptance/test artifacts (not executed):** resumability/replay fixtures; tenant/definition/version/artifact parity report schema; deny cases for caller-supplied tenant, version ID without definition, missing artifact, changed digest, archived version, and invalid current-version link.
- **Promotion record requirements:** source/canonical digests, checkpoint, accepted/quarantined rows, resolver proof, artifact and lifecycle evidence, explicit M05 handoff marker.
- **Rollback/gate:** legacy definitions/versions remain read/write owner; stop a partition and forward-repair conflicts. No instance publication or grant import until the handoff marker exists.
- **Mandatory parent report:** common contract plus handoff readiness and unresolved version identities.

## 3. M05 plugin instance/runtime fencing

### MIG-M05-01 — exact plugin instance identity and reciprocal registry link

- **Exclusive file zone:** `apps/api/src/prodavan/infrastructure/persistence/models/plugin_instance_registry.py`.
- **Dependencies:** MIG-M04-02 and MIG-M04E-02; exact source installation identity and scope policy available.
- **Deliverables:** exact `plugin_instance_id`, canonical tenant/scope anchors, definition/version/artifact composite key, lifecycle checks, named staged and reciprocal registry FKs; nullable/deferred staging only as a temporary migration step.
- **Source acceptance/test artifacts (not executed):** constraint review and fixtures for duplicate identity, cross-tenant scope, definition/version mismatch, artifact mismatch, missing registry row, and invalid plugin shape; validation evidence for both FK directions.
- **Promotion record requirements:** source identity/digest, composite-key uniqueness evidence, FK names/validation status, quarantine counts, lifecycle distribution, M04 dependency digests, and no-M06-positive-write flag.
- **Rollback/gate:** before instance import, drop only empty staged objects; after import, stop partition and preserve rows/evidence, repairing forward. Gate requires exact key and reciprocal FK validation before plugin positive resolution.
- **Mandatory parent report:** common contract plus cyclic-FK status and unresolved source identity blockers.

### MIG-M05-02 — instance backfill, runtime generations, and fencing port

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/m05_instance_runtime/**` and `apps/api/tests/migration/m05_instance_runtime/**`.
- **Dependencies:** MIG-M05-01; M04.e artifact proof; subject/session and runtime evidence sources.
- **Deliverables:** resumable instance import; `ResolvePluginInstancePort`, lifecycle service, monotonic `runtime_generation`, CAS `writer_generation`, runtime evidence without secrets, stale-worker denial and redacted audit.
- **Source acceptance/test artifacts (not executed):** fixtures for missing subject/session, stale generation, restart increment, duplicate instance, unsupported lifecycle, cross-tenant instance, stale writer zero-row CAS, quarantine retry and idempotent replay.
- **Promotion record requirements:** instance/source/target digests, lifecycle and quarantine counts, runtime/writer generation checkpoint, stale-worker denial evidence, exact subject/session proof, audit correlation, M06 handoff marker.
- **Rollback/gate:** before M05 gate, legacy runtime remains authoritative; after a fence event, do not restore stale workers, issue a new forward generation. Gate requires active scope, exact identity, artifact equality, current runtime/writer generations and successful audit.
- **Mandatory parent report:** common contract plus generation/fencing readiness and unresolved policy for instance-independent grants.

## 4. M06 canonical grants/gateway

### MIG-M06-01 — canonical grant persistence, RLS, and trusted writer

- **Exclusive file zone:** `apps/api/src/prodavan/infrastructure/persistence/models/canonical_grants.py`.
- **Dependencies:** MIG-M04E-02, MIG-M04-02, MIG-M05-01; approved capability identity and method-to-operation mapping.
- **Deliverables:** mapping/quarantine/audit foundation; canonical grant schema with exact definition/version/instance FKs, approved plugin scope branch, checks/indexes, active uniqueness, RLS/FORCE RLS, append-only audit privileges and trusted writer epoch. No positive canonical import.
- **Source acceptance/test artifacts (not executed):** schema/static review for all named constraints and indexes; RLS `USING`/`WITH CHECK` no-context/cross-tenant cases; append-only audit mutation denial; instance FK and generation compatibility cases; moving-clock uniqueness explicitly excluded.
- **Promotion record requirements:** dependency gate digests; schema/revision lineage; constraint/index/policy inventory; trusted role/epoch evidence; explicit `canonical_positive_writes=false`.
- **Rollback/gate:** before import, remove only empty objects after dependency review; after import, preserve evidence and repair forward. Schema gate alone cannot authorize allows/issues/revokes.
- **Mandatory parent report:** common contract plus any PostgreSQL/RLS/trusted-writer blocker.

### MIG-M06-02 — adapter shadow-read, mapping, and parity evidence

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/m06_grants_gateway/**` and `apps/api/tests/migration/m06_grants_gateway/**`.
- **Dependencies:** MIG-M06-01; all M04/M05 handoff markers; legacy remains decision owner.
- **Deliverables:** `CapabilityGatewayService` adapter, server-side tenant/scope/definition/version/instance resolution, canonical digesting, approved method mapping, mapping/quarantine/audit writes, shadow comparison and bounded partition parity report. Requested capabilities and caller ownership are never authorization.
- **Source acceptance/test artifacts (not executed):** fixtures for unknown capability, unsupported method, missing/ambiguous instance, stale generation, legacy/canonical allow-deny mismatch, changed source digest, cross-tenant replay, expired/revoked grant, and idempotent exact replay.
- **Promotion record requirements:** source/canonical/binding digests, unknown/unresolved counts, mismatch report, final-delta checkpoint, writer epoch, decision owner, observation window, and explicit `canonical_read_owner=legacy`.
- **Rollback/gate:** stop adapter/backfill writes for a partition; retain canonical evidence and keep legacy authority. Gate requires zero unexplained mismatches, zero unresolved positive bindings, complete audit, and no stale-generation acceptance; no cutover yet.
- **Mandatory parent report:** common contract plus parity result and every blocked mapping.

### MIG-M06-03 — bounded canonical read/write cutover record

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/m06_cutover/**` and `apps/api/tests/migration/m06_cutover/**`.
- **Dependencies:** MIG-M06-02 parity gate; final-delta drain; legacy writer fence plan; approved partition owner.
- **Deliverables:** explicit per-partition cutover command/state machine, writer fencing and lease/epoch checks, canonical read owner transition, legacy read-only projection marker, and immutable append-only promotion evidence.
- **Source acceptance/test artifacts (not executed):** state-machine fixtures for missing gate, duplicate promotion, final-delta race, stale writer, retry/replay, fail-closed gateway, canonical rollback fence, and projection-only legacy access.
- **Promotion record requirements:** required M04/M04.e/M05/M06 evidence, source snapshot/schema, all digests, final-delta checkpoint, fencing epoch, current writer generation, actor/time, decision owner, canonical read/write owner and rollback window. A live status flag cannot substitute for this record.
- **Rollback/gate:** before read cutover, halt and restore legacy authority; after cutover there is no dual-owner rollback: fence canonical generation, stop issue/revoke workers, fail closed, and repair with canonical CAS/idempotency commands. Gate enables only the named partition, never globally.
- **Mandatory parent report:** common contract plus cutover record identifier, owner transition, rollback readiness, and unresolved residual risk.

## 5. Resources and materialization

### MIG-RES-01 — resource roots, versions, bindings, and scope resolver

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/resources/**` and `apps/api/tests/migration/resources/**`.
- **Dependencies:** M04 typed scope resolver; runtime foundation/tenant context; content asset/version contract.
- **Deliverables:** typed resource lifecycle and explicit project binding, immutable resource versions, scope/session/project resolution, secret references only, and compatibility import from prompt/MCP/file sources.
- **Source acceptance/test artifacts (not executed):** fixtures for cross-tenant binding, ambiguous `project_ids=null`, version immutability, secret-value rejection, unsupported MCP manifest, duplicate binding, and deterministic profile selection.
- **Promotion record requirements:** source/target resource and content digests, scope/binding counts, quarantined secrets/manifests, version/publish evidence, legacy owner and partition marker.
- **Rollback/gate:** retain legacy resource readers and immutable source assets; stop import and repair forward; never silently rebind to another project. Gate requires explicit binding and no secret material in payloads.
- **Mandatory parent report:** common contract plus unresolved scope, content, or manifest blockers.

### MIG-RES-02 — runtime revision and materialization planner/apply path

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/runtime_materialization/**` and `apps/api/tests/migration/runtime_materialization/**`.
- **Dependencies:** MIG-RES-01; capability bundle/runtime revision contract; agent-sandbox workspace boundary; runtime bridge credential lease/projected-secret contract and immutable image-digest source.
- **Deliverables:** immutable desired/applied runtime revisions, deterministic materialization plan, idempotency/generation keys, provenance entries, fail-closed apply states, revision rollback command and projection ownership markers; explicit short-lived bridge credential lease/projected-secret reference (never a literal Pod env token) and persisted runtime image digest; handoff markers for `agent_runs`/ordered event and durable approval ownership where materialization is invoked by an agent run.
- **Source acceptance/test artifacts (not executed):** plan digest/provenance fixtures; repeated apply idempotency; partial operation failure -> `failed`; stale generation; prune only revision-owned manifest; previous-ready revision rollback; no literal pod secret/env evidence; bridge lease expiry/reissue and image-digest provenance review; explicit agent-run/approval/event trace linkage or a recorded blocker when the control-plane contract is not yet available.
- **Promotion record requirements:** bundle/revision/materialization digests, operation checkpoint, workspace generation, applied/failed state, provenance manifest, image/runtime references, rollback target and owner.
- **Rollback/gate:** failed or partial apply is never `ready`; stop apply, retain diagnostics, select prior ready revision through an explicit forward command. Gate requires complete materialization and inspectable provenance.
- **Mandatory parent report:** common contract plus materialization status and rollback target.

## 6. Web shell/plugins

### MIG-WEB-01 — shell registry, schema, and request context

- **Exclusive file zone:** `apps/web/src/dynamic/registry.ts`, `schema.ts`, `protocol.ts`, `policy.ts` and their task-owned tests.
- **Dependencies:** backend registry/API contract; M04/M05 identity context; dynamic UI implementation contract.
- **Deliverables:** signed registry snapshot resolution, discriminated schema validation, immutable `RequestContext`, route requirements, ETag/TTL handling, explicit unknown/invalid artifact error states and no privileged fallback.
- **Source acceptance/test artifacts (not executed):** TypeScript/browser fixtures for invalid schema/version/signature/digest, missing context dimension, route scope mismatch, stale ETag, replay correlation, and manifest-as-grant denial.
- **Promotion record requirements:** registry snapshot digest, schema versions, context dimensions, API contract version, browser artifact digest and observation checkpoint.
- **Rollback/gate:** disable the affected app/version and retain prior signed snapshot; never fall back to a privileged hardcoded screen. Gate requires schema/context boundary review.
- **Mandatory parent report:** common contract plus browser isolation or API contract blockers.

### MIG-WEB-02 — declarative renderer and plugin import-graph guard

- **Exclusive file zone (after dead-host cleanup):** `apps/web/src/dynamic/declarative/renderer.ts`, its focused `renderer.test.ts` and `README.md`, plus `apps/web/src/dynamic/legacy-host-import-graph.test.ts`.
- **Dependencies:** MIG-WEB-01 for the preserved schema/registry path. The current plugin bootstrap/RPC source path under `apps/web/src/features/plugins/**` is a separate work zone and was not changed by this cleanup.
- **Deliverables:** allowlisted declarative renderer, focused renderer fixtures and a static graph from `main.tsx` through the current bootstrap/RPC modules. The unreferenced React host/barrel and registry-artifact iframe host/broker, together with their obsolete host/RPC tests, were removed; no broad dynamic→plugin rename was made.
- **Source acceptance/test artifacts (not executed):** text-only rendering, table states, unknown-node rejection, disabled unbound buttons, current plugin-path reachability and rejection of imports to removed legacy modules.
- **Promotion record requirements:** preserved renderer/schema/registry paths and the router→bootstrap→iframe/RPC source chain. The graph guard is not live plugin acceptance and does not change STOP-SHIP status.
- **Rollback/gate:** do not recreate legacy hosts to satisfy their former fixtures. Any restoration requires a new production reference and contract review; registry, shared schema/context utilities and M05 `dynamic-apps` remain intact.
- **Mandatory parent report:** deleted and preserved files, import/reference evidence and intentionally unrun verification commands.

## 7. Equipment ingestion/domain

### MIG-EQ-01 — ingestion run, raw artifact, and normalized catalog snapshot

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/equipment_ingestion/**` and `apps/api/tests/migration/equipment_ingestion/**`.
- **Dependencies:** canonical tenant/source ownership; equipment scope policy; canonical supplier/source identity contract; one approved S4B/IMAP adapter and raw artifact store.
- **Deliverables:** source adapter interface, resumable ingestion run, immutable raw artifact/provenance, normalized catalog snapshots/offers with explicit supplier/source identity (same P/N remains separate per supplier), freshness/last-good state and rebuildable search projection contract.
- **Source acceptance/test artifacts (not executed):** representative import fixtures; re-import identity/digest parity; source outage with stale last-good visibility; same P/N from multiple suppliers with distinct source/offer identities; currency/observed_at/source_ref/run_id; agent-authoritative-price denial.
- **Promotion record requirements:** source/run/raw/normalized digests, row counts and quarantine reasons, freshness checkpoint, adapter version, projection generation and legacy owner.
- **Rollback/gate:** retain raw snapshots and last-good projection; stop new import and restore previous snapshot pointer, never delete source evidence. Gate requires reproducible import and explicit availability policy.
- **Mandatory parent report:** common contract plus source/parser/provenance blockers.

### MIG-EQ-02 — requests, matching, association backfill, and procurement projection

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/equipment_domain/**` and `apps/api/tests/migration/equipment_domain/**`.
- **Dependencies:** MIG-EQ-01; canonical `(tenant_id, project_id)` anchors; association-table scope preparation; human-selection policy; canonical supplier/source identity policy.
- **Deliverables:** typed requests/request lines/candidate groups/tenders/budget projections, JSONB association backfill preserving order, scoped FK/trigger checks, deterministic candidate projection, human-only selection and procurement export; supplier/source identity remains explicit so same part number from different suppliers stays separate, with source/offer ownership chain preserved.
- **Source acceptance/test artifacts (not executed):** fixtures for duplicate/missing IDs, cross-tenant/project links, same-request tender invariant, selected-offer membership, `on_order` versus available, stale offers, budget totals, same-P/N multi-supplier separation and source-chain mismatch, agent selection denial and idempotent ordinal reorder.
- **Promotion record requirements:** source/target association digests, scope/backfill counts, duplicate and quarantine report, selected-offer evidence, tender/request compatibility proof, projection generation and export checkpoint.
- **Rollback/gate:** freeze affected writes or use a declared consistent dual-write gate; retain JSONB source until parity and rollback window close; detach associations before any destructive parent operation. Gate requires zero unresolved cross-scope links and reviewable selection/procurement output.
- **Mandatory parent report:** common contract plus scope or domain invariant blockers.

## 8. Automation/outbox

### MIG-AUTO-01 — automation definitions, ingress idempotency, and task runs

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/automation_ingress/**` and `apps/api/tests/migration/automation_ingress/**`.
- **Dependencies:** tenant/scope resolver; existing `AutomationService` contracts; legacy webhook and project-trigger ownership inventory; agent control-plane contract for durable runs, ordered events, approvals and trace/correlation links.
- **Deliverables:** target automation definition/endpoint mapping, inbound verification, atomic idempotency claim, durable task run creation, source identity/provenance and compatibility route command mapping; explicit ownership mapping from ingress/task intent to `agent_runs`/ordered event envelope/approval records where applicable, with session-independent run identity and trace links.
- **Source acceptance/test artifacts (not executed):** fixtures for duplicate exact request, same identity/different hash conflict, orphan subscription, unsupported event, invalid secret reference, project trigger versus outbound delivery separation, queue outage after commit, durable run/event/approval idempotency and out-of-order rejection; missing control-plane persistence is an explicit blocker, not an implicit compatibility fallback.
- **Promotion record requirements:** source subscription/event digests, mapping/quarantine counts, idempotency checkpoint, secret-ref ownership evidence, legacy worker owner and route compatibility mode.
- **Rollback/gate:** legacy ingress/worker remains authoritative; stop import or dual-write repair via durable marker; never copy secret values or infer tenant/project from payload/URL. Gate requires atomic deduplication and no duplicate task intent.
- **Mandatory parent report:** common contract plus secret, scope, or idempotency blockers.

### MIG-AUTO-02 — outbox, delivery worker, and bounded ownership cutover

- **Exclusive file zone:** `apps/api/src/prodavan/application/migration/automation_outbox/**` and `apps/api/tests/migration/automation_outbox/**`.
- **Dependencies:** MIG-AUTO-01; target outbox contracts; durable queue/lease semantics; explicit per-partition cutover checkpoint.
- **Deliverables:** transactional outbox event/state/enqueue intent adapter, independent outbound delivery mapping, claim/lease/retry worker, no-double-delivery fence, legacy compatibility projection and per-partition ownership state machine.
- **Source acceptance/test artifacts (not executed):** fixtures for atomic business+outbox commit, queue outage repair, lease expiry, duplicate delivery, conflicting request hash, active legacy lease reconciliation, retry/backoff, delivered terminal row, and legacy-worker fencing.
- **Promotion record requirements:** source/target identity and request hashes, final-delta checkpoint, delivery/outbox digests, lease/fencing epoch, worker generation, owner transition, observation window, compatibility projection marker and rollback window.
- **Rollback/gate:** before target ownership, stop target claims and keep legacy worker authoritative; after ownership, fence target generation, stop workers, fail closed and repair through idempotent commands. Never run both workers for one delivery partition. Gate requires zero duplicate intents, drained legacy writer, and one current owner.
- **Mandatory parent report:** common contract plus ownership, lease, duplicate-side-effect, or queue durability blockers.

## Cross-task promotion and freeze record

The coordinator may promote a task only after its parent report and immutable promotion record are present. For M06 cutover, the record must additionally include:

```text
M04 scope: one active typed scope and parent compatibility proof
M04.e: retained tenant, definition/version owner, deterministic version and artifact proof
M05: exact instance key/FK, artifact/subject/session and current generations
M06: checks/FKs/indexes/RLS/FORCE/trusted audit writer
backfill: zero unknown mappings and unresolved positive bindings
parity: zero unexplained mismatches and final-delta checkpoint
fencing: legacy writer drained and one current writer generation
cutover: named partition owner and legacy read-only projection
```

All acceptance checks, tests, builds, lint, compile, Alembic execution, migration execution, browser runs, environment changes, and git checks remain intentionally unexecuted under verification freeze. The backlog itself does not authorize their execution; lifting the freeze requires an explicit coordinator decision after implementation and review.
