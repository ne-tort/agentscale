# Runtime migration audit: modules -> project -> pod/container

**Дата аудита:** 2026-11-01  
**Область:** Agentscale (`apps/api`, актуальная `docs/PRODUCT.md`, as-built runtime docs, tests/infrastructure references)  
**Ограничение:** аудит выполнен без изменения production-кода.

## 1. Executive summary

В текущем as-built связь `module -> project -> pod/container` работает, но это не единый runtime-контракт. Она собирается из нескольких независимых механизмов:

```text
module template / instance / bind
  -> module planner + executor -> MinIO project workspace
  -> container env loader -> Pod env
  -> bridge token minting -> scopes
  -> Pod API -> module rows/meta/actions/infra
  -> OpenClaw config + MCP packages -> agent-runtime process
  -> k8s Pod / init hydrate / readiness
```

Project является правильной границей tenancy и lifecycle: один Project имеет один `ProjectPodRow` и один workspace key; live Pod является эфемерной реализацией проекта. Однако desired runtime сейчас вычисляется повторно и частично разными компонентами. Согласование достигается косвенными механизмами (`materialize_manifest`, `hydrate_generation`, bridge generation, повторный `POST /v1/project/bind`) вместо одного проверяемого immutable runtime snapshot.

**Главный target:** ввести версионируемый `ProjectRuntimeRevision` (immutable desired snapshot) и подписанный/хешированный `CapabilityBundle`, который атомарно описывает для конкретного Project:

- разрешённые module capabilities и их effective SoT;
- prompt contributions и итоговые prompt files;
- MCP packages, executable entrypoints, env references и package digests;
- file artifacts и target paths;
- resolved non-secret env и secret references/leases;
- Pod API scopes и module action policy;
- agent adapter/provider runtime config;
- workspace ownership manifest и composition provenance.

Pod/container должен стартовать только с конкретной revision, а не с «текущими» данными, вычисленными несколькими запросами. Sync должен создавать новую revision, проверять её, а затем атомарно переводить Project/Pod на неё.

## 2. As-built ownership map

| Layer | As-built source/owner | Runtime effect |
|---|---|---|
| Module catalog | `modules`, `module_meta_documents` | reusable template/meta documents |
| Module instance | `module_instances`, instance meta/data JSONB | effective SoT for owner |
| Bind graph | `module_company_grants`, `module_cabinet_bindings`, `module_project_bindings` | visibility, local fork/global inheritance, edit rights |
| Project | `projects` | tenant/lifecycle/workspace/provider identity |
| Project-Pod relation | `project_pods` | desired/observed Pod state, runtime ref, hydrate generation |
| Workspace SoT | MinIO `projects/{workspace_key}/workspace/` | last-good tree used on hydrate |
| Live filesystem | Pod `/workspace` on `emptyDir` | mutable working tree; lost on recreate unless dehydrated |
| Agent runtime | `agent-runtime` container | SDK/OpenClaw loop, MCP processes, workspace tools |
| Platform surface | API `:8001` (`main_pod`) | Bridge-scoped modules/infra/agent/documents endpoints |
| k8s lifecycle | `PodCommand`, runtime adapter, reconcile worker | create/pause/terminate/recreate/readiness/drift correction |

### Module ownership and SoT

The model correctly separates template, instance and binding:

- local bind forks a child instance;
- global bind resolves parent SoT and does not create a child instance;
- project materialization requires an explicit MP bind;
- `project_ids` filters rows within the set of project-bound modules;
- chat scope (`all`/`current`) is orthogonal to local/global bind.

Relevant implementation: `ModuleInstanceService.resolve_sot_instance`, `ModuleBindingService`, and `ProjectRuntimeModuleService`. This is a sound domain foundation and should be preserved in the target design.

## 3. End-to-end as-built flow

### 3.1 Launch

`ProjectCommand.launch` performs the following sequence:

1. Validates provider and resolved AI key.
2. Resolves credentials and enabled module IDs.
3. Calls `ProjectMaterializeService.materialize_project` with `when=project.created`.
4. `MaterializePlanner` resolves project-bound modules, effective instances and `materialize` rules.
5. `MaterializeExecutor` writes files/packages into the workspace object store and returns a module path manifest.
6. Platform MCP package(s) are merged; `mcp.json` and `.prodavan/config.yaml` are written.
7. Project stores `materialize_manifest`.
8. `PodCommand.provision_for_project` creates a durable `ProjectPodRow`.
9. `PodCommand.sync_desired(RUNNING)` loads env, mints Bridge JWT, builds `PodRuntimeContext`, and calls the selected runtime adapter.
10. k8s adapter creates/recreates the Pod; init container hydrates `/workspace`; readiness is observed.
11. Agent session bootstrap/bind registers sessions and pushes runtime project identity where required.

Important: launch returns after create/provision semantics; readiness is observed/reconciled separately and has pull/ready budgets.

### 3.2 Sync/rematerialize

Explicit `POST /projects/{id}/sync` or launch performs:

1. Resolve all cabinet module IDs and enabled project module IDs.
2. Wipe roots of enabled modules and remove disabled module paths from the prior manifest.
3. Re-run planner/executor.
4. Update `projects.materialize_manifest`.
5. Increment `project_pods.hydrate_generation`.
6. Recreate the k8s Pod when generation differs; hydrate the new workspace.
7. Re-bootstrap sessions and rehydrate prior conversation from Postgres when the runtime generation changed.

Cabinet/module writes either mark `workspace_outdated_at`, enqueue rematerialization, or defer while a turn is active (`workspace_sync_pending_*`). The deferred sync is triggered at turn end or by a sweep.

### 3.3 Pause/resume/reload/terminate

- **Pause:** checkpoint/dehydrate workspace, revoke Bridge generation, pause runtime; tenant infra is retained.
- **Resume:** hydrate last-good workspace and restore running state.
- **Reload:** checkpoint, revoke Bridge generation, terminate workload, reset Pod row, recreate.
- **Terminate/purge:** terminate runtime, revoke Bridge generation, purge tenant infra and project-tagged objects.
- **Reconcile:** PG advisory lock protects one pass; desired state is compared with observations; failed/absent desired-running projects may be reprovisioned; orphan managed Pods are reaped after grace.

## 4. Pipeline audit

### 4.1 Module capabilities and dependency composition

Current capability input is distributed across:

- module template meta documents (`materialize`, `materialize_roots`, `columns`, `container_env`, `container_env_secrets`, `mcp_tools`, `mcp_aliases`, `actions`, `views`);
- effective module instance meta/data;
- project binding and bind kind/edit policy;
- module rows and `project_ids`;
- platform-seeded MCP packages;
- project provider/tool policy;
- company runtime policy.

`MaterializePlanner` produces `MaterializeOp` objects. It supports row/rows/static/meta_document sources and formats including `raw`, `copy_blob`, `mcp_package`, JSON, template and legacy SQLite merge. Prompt fragments are stitched cross-module by path and priority.

**Good:** explicit MP requirement, deterministic prompt stitching, path normalization, module manifest for pruning, file `sha256` skip, policy filtering of MCP packages, canonical MCP names, no raw DB/MinIO access from Pods.

**Risk:** the plan is an internal list of operations, not a persisted, typed capability graph. A package, prompt file, env binding and API scope can be produced by different code paths without a shared dependency identity. A module may be materially present but its API scope or MCP policy may be out of sync until a new bind/recreate.

### 4.2 Prompt pipeline

As-built:

```text
prompt fragment rows
  -> MaterializePlanner
  -> PromptContribution(path, priority, body, ids)
  -> deterministic stitch (priority, body length, ids)
  -> AGENTS.md / prompt files in workspace
```

`AGENTS.md` is platform-owned and excluded from dehydrate; generated prompt paths are tracked in `materialize_manifest`. There is no explicit prompt artifact version or provenance stored with the generated file. A user/agent cannot reliably answer which module revision contributed a block after the fact.

**Target requirement:** each generated prompt file records contribution IDs, module revision IDs, order key, content hash and policy decisions in the runtime revision manifest.

### 4.3 MCP pipeline

As-built:

```text
FileRef/seed package
  -> validator (manifest + content hash)
  -> extract packages/{manifest.name}
  -> mcp.json
  -> .prodavan/config.yaml mcp.servers
  -> runtime startup/bind
```

Packages are filtered by the effective tool policy before both config renderings. `prodavan-modules` and `prodavan-equipment` are platform-added packages. MCP package env values use placeholders such as `${PRODAVAN_*}`; Cursor stdio MCP requires bridge-side expansion/injection because it does not inherit Pod env in the same way.

**Current gap:** package identity is content-hashed during validation but there is no first-class project-level lockfile mapping package name to immutable digest, source module/instance, API contract version and runtime compatibility. A row update can invalidate workspace freshness while a running process still has old MCP processes/config until recreation/rebind.

### 4.4 File pipeline

The three phases are correctly separated:

1. Content upload creates a Content Asset/blob in MinIO.
2. Module row stores a canonical FileRef (`asset_id`, `version_id`, `storage_key`, metadata, `sha256`).
3. Materialize rules copy the referenced blob into the project workspace; init hydrate transfers it to `/workspace`.

`MaterializeExecutor` resolves `storage_key` or blob version, supports copy-by-hash and package extraction. Workspace dehydrate excludes platform-owned paths (`mcp.json`, `AGENTS.md`, `packages`, `.prodavan`, manifest paths), preventing stale generated artifacts from overwriting fresh platform state.

**Gap:** FileRef ACL/existence validation at row write is still documented as a gap. Materialization errors are logged per operation and do not necessarily fail the whole revision, so a partially materialized module can appear successful unless consumers inspect logs/result details.

### 4.5 Env and secret pipeline

`ContainerEnvLoader` separately reads `container_env` and `container_env_secrets`, resolves static values, row references and `foreach_rows`, applies project filters and lifecycle conditions, and reads scoped secrets from `SecretStore`. `PodCommand._apply_running` injects the resulting pairs into `PodRuntimeContext`; `pod_spec.py` renders them into container env.

The model correctly forbids plaintext secret values in module rows and checks cabinet scope for cabinet secret refs.

**Critical current security gap:** when a per-Pod Bridge token is available, `pod_spec.py` places it as a literal env value (`PRODAVAN_AUTH_TOKEN`). It is therefore observable through Pod inspection/proc surfaces. The code itself marks this as a follow-up. Target must use a per-Pod Secret or short-lived projected credential, with rotation/revocation independent from ordinary module env.

**Consistency gap:** env is resolved at launch/recreate and again during project bind. There is no persisted env resolution snapshot or hash proving that the Pod process and bind payload used the same input set.

### 4.6 Bridge and Pod API

Bridge JWT claims include project, cabinet, company, pod, generation and scopes. Launch scopes include platform surfaces plus per-module rows/actions/meta scopes. `main_pod` exposes only the Pod API on `:8001`; network policy and middleware prevent general API access.

Pod module routes enforce:

- `bridge.project_id == path project_id`;
- company/cabinet tenancy match;
- module-specific scope;
- explicit project bind and local/global edit policy;
- session header for chat-scoped rows;
- same action executor path as employee/UI flows.

First-party MCP wraps these routes, so the agent never receives raw SQL or direct Redis/Mongo/OpenSearch/MinIO DSNs. This boundary is professionally sound.

**Current gap:** scopes are derived from module IDs only. They do not encode capability version, action policy revision, or exact effective SoT. A long-lived token can remain structurally valid while the module capability definition changes; recreation/rebind is the operational remedy, but the token itself cannot explain the mismatch.

### 4.7 Workspace sync and durability

The intended durability split is correct:

- Postgres is chat transcript SoT.
- MinIO project workspace is last-good durable tree.
- Pod `/workspace` is mutable runtime state.
- `.openclaw-data` is crash-buffer and excluded from dehydrate.
- Before pause/reload, API checkpoints Pod workspace.
- On hydrate, init container writes archive and fixes ownership to UID/GID 1000.

The sync policy protects active turns by deferring workspace changes. This avoids replacing a runtime during an in-flight turn.

**Current gaps:**

1. `materialize_manifest` is a mutable JSON map, not an immutable revision manifest.
2. Module writes can mark outdated and schedule jobs through multiple paths (direct/Celery/Kafka), increasing operational state combinations.
3. Materialization is not transactionally coupled to Pod recreation: object-store write, DB manifest update, generation increment and k8s create can fail at different points.
4. There is no explicit `desired_revision` vs `applied_revision` status; `workspace_outdated_at` is a coarse signal.
5. Partial operation failures are possible because executor logs an operation exception and continues.

### 4.8 Pod API, runtime config and k8s lifecycle

The lifecycle abstraction is clean at the port level (`PodRuntimePort`, `HydratePort`, workspace/dehydrate ports) and supports `stub`, `k8s` and `sandbox` modes. k8s Pod spec has one `agent-runtime` container, a hydrate init container, `emptyDir` workspace, readiness/liveness probes, managed labels and generation labels.

Runtime state is reconciled from DB desired state and k8s observations. Pulling, hydrating, fatal image/container errors and transient missing Pods are distinguished; node/WSL-like absence can recover without immediately pinning the project in `error`.

**Delivery gap:** dynamic project Pods consume `:latest` with `imagePullPolicy: Always`; a running Pod changes only after recreate, and exact digest/rollback is not persisted in Project runtime state. The runtime image delivery document explicitly identifies digest pinning as P3.

## 5. Findings ranked by risk

### F1 — No single immutable runtime revision (high)

The same effective project state is recomputed by planner, env loader, Bridge scope builder, provider config builder and bind path. A change between those reads can produce a Pod with one combination of workspace/config/env/scopes. There is no atomic desired/applied revision and no reliable postmortem artifact.

**Effect:** stale MCP/config, scopes not matching package set, difficult rollback and difficult diagnosis of partial sync.

### F2 — Secret Bridge token in literal Pod env (high)

`pod_spec.py` currently renders `PRODAVAN_AUTH_TOKEN` as a literal value when a token is minted. This leaks through Kubernetes inspection/proc surfaces and makes credential rotation less disciplined.

### F3 — Materialization is best-effort per operation (high)

`MaterializeExecutor.execute` catches each operation exception and continues. The caller can persist a manifest even when one file/package failed. Runtime readiness does not imply capability completeness.

### F4 — Module capability/version identity is implicit (high)

Module IDs and MCP package names are not enough to identify a reproducible capability. Meta document edits, package bytes, API routes, seed code and runtime compatibility are not represented as one versioned dependency.

### F5 — `:latest` image consumption prevents deterministic rollback (medium/high)

New/recreated Pods get whatever digest currently resolves. Project state does not retain image digest as part of runtime identity.

### F6 — Sync state is coarse and multi-path (medium)

`workspace_outdated_at`, pending fields, Celery/Kafka/direct fallback and generation labels work, but lack a durable state machine and idempotency record for a revision apply operation.

### F7 — FileRef ACL validation and provenance (medium)

A row can hold a stale or inaccessible FileRef; generated files do not retain source provenance in a user-visible runtime manifest.

### F8 — Effective bind semantics are not uniformly surfaced (medium)

The data APIs resolve local/global SoT correctly, but materialization, scopes, env and UI paths each re-query bindings. A single effective capability resolver would reduce divergence.

## 6. Target architecture

### 6.1 Core objects

Introduce the following conceptual objects (implementation can be incremental):

#### `ModuleCapability`

A versioned, immutable capability definition identified by:

```text
capability_id       stable logical id, e.g. mod_equipment
capability_version  semver or monotonic release version
schema_version      meta DSL/API schema version
artifact_digest     hash of normalized capability definition + package artifacts
runtime_compat      supported agent-runtime / Pod API contract range
```

A capability contains typed declarations, not arbitrary untracked side effects:

- `data_contract`: tables, row schemas, chat scope, project filtering;
- `ui_contract`: hubs/views/actions;
- `prompt_contributions`;
- `files`/`artifacts` and ownership;
- `mcp_servers` and tool declarations;
- `env_bindings` and secret references;
- `api_scopes` and action permissions;
- dependency constraints and conflict policy;
- migration/upgrade hooks.

Existing module meta documents can remain the authoring format initially, but publication should normalize and validate them into this contract.

#### `EffectiveModuleBinding`

A resolved project-level object:

```text
project_id
module_id
capability_version
source_instance_id / SoT owner
bind_kind
child_may_edit
row_selection / project_ids
policy decisions
```

It must be computed once per runtime revision and reused by planner, env resolver, Bridge scopes and Pod APIs.

#### `CapabilityBundle`

An immutable, content-addressed bundle for one project revision:

```text
bundle_id = sha256(canonical manifest)
project_id
revision
module locks
dependency graph
workspace files + hashes
MCP package locks + hashes
openclaw config hash
non-secret env hash/values
secret binding descriptors (never secret values)
Bridge scope set
Pod API contract version
runtime image digest
```

The bundle may be stored as a DB manifest plus object-store artifacts; it need not be a single tarball. A lockfile-like canonical JSON is required.

#### `ProjectRuntimeRevision`

Durable lifecycle state:

```text
desired_revision_id
applied_revision_id
pod_id
pod_uid
hydrate_generation
bridge_generation
runtime_image_digest
state: planned | validated | applying | ready | failed | superseded
failure_code / diagnostics
created_by / source_event
```

`ProjectRow.materialize_manifest` and `workspace_outdated_at` can be retained as compatibility projections while this becomes the source of truth.

### 6.2 Composition algorithm

1. Load module graph and bindings for the Project.
2. Resolve effective SoT for each module once.
3. Select capability versions according to a project lock policy:
   - default: platform-compatible version range;
   - production/reproducible projects: pinned version;
   - explicit upgrade: create a new revision and show diff.
4. Build a dependency graph. Dependencies are typed (`requires`, `provides`, `conflicts`, `optional`) and include version ranges.
5. Apply deterministic conflict rules:
   - capability ID uniqueness;
   - file path ownership conflict is an error unless a declared merge strategy exists;
   - env name collision is an error unless priority/override is explicit;
   - MCP server name collision is an error unless same artifact digest;
   - API scope is the union of effective capabilities, minus policy denies;
   - prompt path collisions use declared priority and preserve provenance.
6. Validate the full graph before writing anything.
7. Produce the canonical `CapabilityBundle` and content digest.
8. Store it as desired revision; only then apply workspace, Pod config and credentials.

The composition result must be deterministic for the same input locks and source snapshots.

### 6.3 Materialization contract

Materialization should be a two-phase operation:

**Prepare:**

- resolve and validate all sources;
- fetch/verify FileRefs and MCP package manifests;
- render prompt files and configs in a staging prefix;
- calculate all hashes and ownership manifest;
- validate env names, secret scopes, package/runtime compatibility and API scopes;
- persist bundle/revision in `validated` state.

**Apply:**

- atomically promote the staging workspace prefix to the revision workspace snapshot;
- update desired revision;
- recreate or bind the runtime with the exact revision ID and digests;
- verify runtime reports the same revision, workspace hash, config hash and capability set;
- mark `ready` only after hydration and readiness checks pass.

A failed operation must fail the revision; partial best-effort materialization should be retained only as diagnostics, never presented as a ready runtime.

### 6.4 Runtime/Bridge contract

`POST /v1/project/bind` should accept a revision-aware payload:

```json
{
  "project_id": "...",
  "pod_id": "...",
  "runtime_revision_id": "rr_...",
  "bundle_digest": "sha256:...",
  "workspace_key": "...",
  "config_digest": "sha256:...",
  "env_digest": "sha256:...",
  "scopes": ["..."],
  "credential_lease": "..."
}
```

Runtime health/readiness should expose the applied revision and hashes. Bridge JWT should include `runtime_revision_id`/bundle digest (or a compact revision claim) and capability contract version. API must reject a Pod call if the claim is for the wrong project or stale revision where the endpoint requires current capability state.

Secrets should be delivered via short-lived per-Pod Secret/lease or runtime credential broker, never as a literal long-lived Pod env token. Module secrets remain references in the bundle; actual values are injected only at the narrowest runtime boundary.

### 6.5 Workspace ownership

Keep the existing ownership split but make it revision-aware:

- platform-owned generated paths have exact hashes and source capability IDs;
- user/agent-owned paths are checkpointed separately;
- generated files are staged/promoted and cannot be overwritten by dehydrate;
- a revision apply never deletes user-owned paths;
- manifest records delete/prune decisions explicitly.

### 6.6 k8s lifecycle

Pod identity should include:

- Project ID;
- runtime revision ID/bundle digest;
- runtime image digest;
- hydrate generation;
- Bridge generation.

Recommended rollout:

1. Create immutable Pod spec from revision and image digest.
2. Create/recreate with labels and annotations for all identities.
3. Init hydrate fetches exactly the revision snapshot (or verifies bundle digest after API archive).
4. Readiness probe is not enough: runtime health must report revision/config/capability hashes.
5. Reconcile desired revision against observed Pod labels/runtime health.
6. Keep old revision snapshot for rollback/forensics until retention policy expires.

### 6.7 Versioning policy

Use three separately versioned dimensions:

1. **Capability version:** semantic behavior/meta/API/MCP contract (`mod_equipment@2.1.0`).
2. **Bundle/revision:** concrete project composition and source data snapshot (`rr_...`, content digest).
3. **Runtime image/API contract:** agent-runtime image digest plus Pod API protocol version.

Rules:

- A capability patch may change docs/templates only if API/tool behavior is compatible.
- Any MCP tool schema, route/scope, env contract or file ownership change requires at least a minor version; breaking changes require major.
- Bundle digest changes on every material input change, including source FileRef hash, prompt body, row selection, policy, provider endpoint, env binding or dependency lock.
- Runtime image is always pinned by digest in a revision; tags are only release aliases.
- Rollback selects a prior validated bundle and compatible runtime image, then applies it as a new deployment attempt; it does not mutate history.
- Store provenance for every generated file, package, env entry and scope.

## 7. Migration plan

### Phase 0 — Instrument current system (low risk)

- Emit a canonical `composition_input` and hashes from existing planner, env loader, config builder and scope builder.
- Add structured diagnostics for every materialize operation; convert logged exceptions into a result with `failed_paths`.
- Persist current package hashes, config hash, env hash and scope hash alongside `materialize_manifest`.
- Add runtime health fields for workspace/config/package hashes.

### Phase 1 — Unify effective capability resolution

- Extract a project-scoped resolver that returns `EffectiveModuleBinding` objects once.
- Make materialize planner, env loader, Bridge scope minting and Pod module authorization consume that result.
- Preserve existing local/global semantics and `project_ids` behavior.
- Add tests for global vs local MP, child edit policy, no MP, row project filters, chat scope and module removal.

### Phase 2 — Introduce immutable bundle/lockfile

- Add normalized capability publication and validation over existing meta documents.
- Build deterministic dependency composition and path/env/MCP conflict checks.
- Store `ProjectRuntimeRevision` and bundle manifest; keep old fields as projections.
- Stage workspace output and promote only after full validation.

### Phase 3 — Revision-aware runtime binding

- Extend bind payload, Bridge claims and Pod API diagnostics with revision/bundle identity.
- Verify runtime identity on readiness and on agent send.
- Make sync idempotent by `(project_id, desired_revision_id)` and record failures/retries.
- Replace literal Bridge token env with per-Pod Secret/lease delivery.

### Phase 4 — Deterministic image and rollback delivery

- Resolve/persist runtime image digest when creating a revision.
- Include image/API compatibility in bundle validation.
- Reconcile Pod labels and runtime health against the desired revision.
- Provide rollback to a prior validated revision without mutating module history.

### Phase 5 — Remove compatibility projections

- Deprecate direct planner recomputation from mutable module state during apply.
- Retire coarse `workspace_outdated_at` as the primary state; expose it only as UI compatibility.
- Remove legacy MinIO hydrate path where cluster policy allows; retain API-mediated hydrate as the only sandbox path.

## 8. Acceptance criteria

A migration is complete when:

- One canonical project composition produces workspace, MCP, env, Bridge scopes and Pod API authorization.
- A runtime can report exactly which project revision, bundle digest, capability versions and image digest it runs.
- A failed file/package/env operation cannot produce a `ready` revision.
- Sync is idempotent, resumable and observable with desired/applied revision state.
- Prompt/MCP/file/env outputs have source provenance and hashes.
- Secret values never appear in module JSON, bundle manifests, Pod specs, logs or ordinary Pod env inspection.
- Local/global binds and chat-scoped rows retain current behavior under pinned capability versions.
- A prior validated revision can be rolled back deterministically.
- Reconcile repairs drift without silently applying a different module composition.

## 9. Source map

Primary code and docs inspected:

- `docs/PRODUCT.md` — product invariants, module model, workspace durability, lifecycle.
- `apps/api/src/prodavan/application/projects/materialize.py` — project materialization orchestration.
- `apps/api/src/prodavan/application/projects/materialize_planner.py` — rule resolution and prompt stitching.
- `apps/api/src/prodavan/application/projects/materialize_executor.py` — workspace/MCP/file execution.
- `apps/api/src/prodavan/application/projects/openclaw_config_materializer.py` — runtime config and MCP policy rendering.
- `apps/api/src/prodavan/application/pod_service/container_env_loader.py` — env/secret resolution.
- `apps/api/src/prodavan/application/pod_service/command.py` — Pod lifecycle, env load, Bridge token, hydrate generation.
- `apps/api/src/prodavan/application/agent/project_bind.py` — post-ready project bind and env push.
- `apps/api/src/prodavan/application/agent/openclaw_bridge.py` — runtime endpoint, bind refresh and recovery.
- `apps/api/src/prodavan/application/pod_identity/bridge.py` — scoped JWT and generations.
- `apps/api/src/prodavan/api/v1/pod_modules.py` and `apps/api/src/prodavan/application/tenant_infra/pod_modules.py` — Pod module API and ACL.
- `apps/api/src/prodavan/infrastructure/k8s/sandbox/pod_spec.py` — container/init/env/volume spec.
- `apps/api/src/prodavan/infrastructure/k8s/sandbox/client.py` and `apps/api/src/prodavan/application/pod_service/reconcile.py` — observations and reconciliation.
- `apps/api/src/prodavan/application/projects/workspace_sync_policy.py` — deferred/scheduled sync behavior.
- `apps/api/src/prodavan/application/pod_service/workspace_dehydrate_rules.py` — generated/user workspace ownership.
- `docs/target/06-modules/meta-syntax/12-content-file-pipeline.md` — file pipeline.
- `docs/target/06-modules/meta-syntax/13-container-env-secrets.md` — env/secret pipeline.
- `docs/target/12-layer-docs/tenant-infra-gateway.md` — Pod API isolation and scopes.
- `docs/07-infrastructure/agent-runtime-delivery.md` — runtime image delivery and digest-pinning gap.

## 10. Final assessment

The existing design has the right high-level boundaries: Project is the runtime tenant, Pod is disposable, workspace is durable outside the Pod, module data is mediated by effective SoT and Bridge scopes, and the Pod has no direct infrastructure credentials. The migration priority is therefore not a wholesale rewrite. It is to make the already existing composition explicit, immutable, revisioned and observable, then make every downstream consumer use that one result.
