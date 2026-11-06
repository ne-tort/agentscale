# Automation persistence adapter contract

Статус: migration-stage persistence contract, documentation-only, baseline 2026-11-01.

Документ задаёт конкретный trusted adapter для automation intent, task/run, outbox и outbound delivery, а также отдельную границу для legacy `project_triggers`. Он сверяет application zone, ранее названную `automation_persistence/T05`, с context handoff, automation cutover adapter и target ORM task map. Документ не меняет code, ORM, Alembic, legacy routes/workers, core docs или shared environments. Он не разрешает backfill, positive writes, worker cutover или cleanup.

## 1. Naming, scope and ownership

В application cross-review automation persistence обозначена как `T05`. В целевой ORM-карте `T05` означает immutable Audit V2 (`2026110105_persistence_audit_v2.py`), а automation intent/outbox/delivery принадлежат `T09`, с плановой ревизией `2026110109_persistence_automation_outbox.py`. Это не две разные automation схемы. В этом контракте:

- `T05 application zone` — существующий application boundary `automation_persistence`, его contracts/ports/service;
- `T05 ORM dependency` — Audit V2 boundary, обязательный для trusted transaction и promotion evidence;
- `T09 persistence task` — будущая ORM-модель `automation_outbox.py`, repositories и generated Alembic revision;
- `T10` — immutable per-partition promotion record, который отдельно разрешает ownership.

До T09/T10 target rows могут существовать только как reviewed shadow/evidence state, если это будет отдельно разрешено implementation gate. Наличие target row, зеркала, успешного application call или истёкшего legacy lease не меняет owner.

Целевые владельцы состояния разделены:

| Boundary | Canonical responsibility | Compatibility responsibility |
|---|---|---|
| Automation definition/endpoint | versioned trigger/action and verified secret reference | mapping from `webhook_subscriptions` |
| Ingress idempotency | tenant-scoped key, request hash, original result/task link | legacy correlation fallback is explicit and weaker |
| Task/run | durable session-independent execution intent and lifecycle | link to `agent_runs`, ordered event, or approval when applicable |
| Outbox event/state | immutable business event and publication lifecycle | no alias to delivery or trigger row |
| Delivery | one retryable external side effect per authorized subscription/source event | projection of `webhook_deliveries` during migration |
| Project trigger queue | separate project trigger intent and worker fence | `project_triggers` only; never webhook delivery ownership |

`webhook_deliveries` and `project_triggers` are separate legacy queues. A common lease column does not make them interchangeable. A project trigger creates an outbound delivery only when the target automation explicitly declares that action.

## 2. Preconditions and trusted transaction context

The adapter must receive a server-resolved transaction context. Caller payload, URL, copied `company_id`, project assertion, subscription row, or legacy JSON cannot establish ownership. The current application aliases (`AutomationIntentRepository`, `OutboxRepository`, `TaskRunRepository`, `DeliveryLeaseRepository`, and `IdempotencyRepository`) are protocol aliases only; their presence or a successful `record_and_enqueue` call does not prove a trusted SQL UoW, business-mutation atomicity, RLS context, or promotion ownership. The implementation adapter must bind the operation to the existing transaction-scoped unit of work and make the business mutation part of that same transaction when a command has one. Before any positive target intent is accepted, the unit of work resolves and verifies:

1. exactly one canonical tenant (`companies.id`, application name `tenant_id`);
2. typed scope/owner row and parent compatibility from the owner registry;
3. project, automation definition/endpoint and subscription mapping;
4. durable task parent: `agent_runs`, ordered event, approval, or an explicit session-independent target parent according to the applicable command;
5. runtime/grant generation and promotion evidence when the automation action depends on them;
6. transaction-local RLS context, correlation ID, writer/worker epoch and authenticated actor.

Missing, contradictory, archived, quarantined, cross-tenant, cross-project or ambiguous context returns `deny`, `quarantine`, `conflict`, `stale-generation` or `system-failure`. It never falls back to a payload-derived tenant or a company-wide interpretation of a project-scoped row.

The trusted unit of work sets the server context and performs all related writes before commit. The required order is:

```text
authenticated request
  -> tenant/scope/project and parent resolver
  -> definition/endpoint and source mapping resolver
  -> expected writer/worker generation CAS
  -> business mutation (when applicable)
  -> idempotency claim + task/run intent
  -> immutable outbox event + outbox state + enqueue intent
  -> delivery projection/intent (when applicable)
  -> full Audit V2 append
  -> promotion/gate evidence (only at its own gate)
  -> commit
```

A narrow application append or a post-commit audit call is not equivalent to this boundary. Audit failure rolls back the related positive mutation and returns an explicit system failure. Queue availability is not part of commit success: a queue kick after commit is a repair optimization.

## 3. Target persistence shape and ORM handoff

The future model module is `apps/api/src/prodavan/infrastructure/persistence/models/automation_outbox.py`. It must be imported deliberately by `models/__init__.py` before model-derived Alembic generation. The implementation owns only the automation model zone and compatibility projection metadata in `external_api.py`/`projects.py`; it does not replace or mutate legacy tables in the T09 revision.

The target schema consists of separate durable concerns. Names below are logical target names; the implementation PR may choose reviewed physical names while preserving each identity boundary.

### 3.1 Automation intent and endpoint mapping

Persist verified automation/endpoint intent with:

- target endpoint/automation identity and immutable version;
- canonical tenant and typed scope/project anchors;
- legacy subscription identity `(source_kind, source_id, source_schema_version)` where imported;
- secret reference only, never secret material;
- enabled/status and trigger/action configuration after canonical validation;
- source payload digest, canonical digest, checkpoint, correlation and quarantine/mapping status.

Use parent FKs only after T01 scope and T02 definition/version evidence. Security/provenance parents use `RESTRICT` or archive transitions. Endpoint/secret references are not authorization evidence by themselves.

### 3.2 Idempotency record and task/run intent

Persist an ingress idempotency record containing tenant, operation, scoped key, request hash, original task/run identity, state, response/replay reference, expiry/retention and source correlation. Persist the task/run separately with:

- tenant, typed scope/project and automation identity;
- immutable source kind/source event identity;
- stable idempotency key;
- durable parent link to `agent_runs`, ordered event, approval, or reviewed target parent;
- immutable input payload/digest and schema version;
- lifecycle, retry counters, availability and terminal diagnostics;
- worker owner, lease expiry and fencing generation.

A task is session-independent. It must not be made durable only by copying an agent session ID, and a delivery cannot exist without a real task/run link. If an applicable control-plane parent is unavailable, the adapter records a blocker/quarantine and does not invent a compatibility parent.

### 3.3 Immutable event, state and enqueue intent

`OutboxEvent` is immutable business data keyed by `(tenant_id, aggregate_type, aggregate_id, aggregate_version)`, or by a preserved immutable source event identity where no aggregate version exists. Its payload is canonical JSON with schema/event type and redacted source metadata. It is never rewritten when a subscription or retry changes.

`OutboxStateRecord` is a separate mutable lifecycle row for `pending`, `leased`, `retry_wait`, `delivered` and `dead_letter`. `OutboxEnqueueIntent` is a separate durable queue intent with event ID, queue name and deduplication key. The event ID, initial state event ID and enqueue intent event ID must match in one transaction. Outbox uniqueness is distinct from task idempotency and delivery uniqueness. Creating these T09 records, or enabling a drainer, does not enable target claims or change ownership; claims remain disabled/shadow-only until T10 promotion for the exact partition.

The adapter must persist the business mutation, idempotency record, task/run intent, immutable event, initial outbox state and enqueue intent atomically when the command creates a business side effect. This requires one transaction-scoped UoW operation that owns both the business mutation and these records; composing separate repository calls or committing `record_and_enqueue` beside an already-committed business mutation is insufficient. If the broker is unavailable after commit, a drainer retries the existing intent using the same deduplication key. It must never create a second task, event or delivery.

### 3.4 Delivery intent and attempts

Persist an independently retryable `OutboundDelivery` with:

- tenant, automation/endpoint and real `task_run_id`;
- authorized subscription identity and immutable `source_event_id`;
- canonical request/body digest and stable delivery id/key;
- destination and secret references, with signed bytes derived from immutable canonical payload;
- state, attempt count, retry schedule and terminal result/error digest;
- persisted lease owner, expiry and fencing generation;
- compatibility projection IDs for `webhook_deliveries` and source schema/checkpoint.

The primary delivery uniqueness boundary is `(subscription_id, source_event_id)` after tenant/automation mapping has been resolved and validated. A target event/enqueue uniqueness key remains separate. Do not use URL, payload hash, mutable timestamp, response status or lease fields as the sole identity. A legacy row without a trustworthy source event is provenance-only or quarantine; a timestamp-derived ID is forbidden.

## 4. Scoped idempotency and request hash

The adapter derives an injective idempotency key from stable identity, with tenant and operation scope included. For webhook delivery the minimum identity is:

```text
(tenant_id, subscription_id, source_kind, source_event_id)
```

For `project_triggers` the context handoff identity is:

```text
(tenant_id, project_id, trigger_id)
```

The key is shared by the task/run, event/enqueue intent and optional delivery projection, but each table retains its own identity and uniqueness constraint. The key is never broadened to a tenant-wide URL or payload-only key.

`request_hash` is SHA-256 of a versioned canonical request envelope. It includes the stable tenant/operation/source identity, task/action identity, canonical payload and immutable body digest. It excludes lease owner, lease expiry, attempts, response status, diagnostic timestamps and other mutable lifecycle fields. Secrets, credentials and raw sensitive values are excluded or represented by a reference/digest according to the redaction policy.

The repository/UoW must implement the following atomic decision. The scoped uniqueness constraint is on the stable identity `(tenant_id, operation, key)` (or an explicitly reviewed equivalent), while `request_hash` is immutable data compared after the winner is read; including the hash in the uniqueness key would allow the same logical key to create multiple rows instead of surfacing a conflict.

| Existing claim | Result |
|---|---|
| no row for scoped key | insert claim, task/run, event/state/intent and applicable delivery; return accepted |
| same key and identical hash | return original task/run and IDs with `duplicate=true`; create no second side effect |
| same key and different hash | return explicit idempotency conflict, quarantine/audit redacted evidence; never overwrite |
| same source event for another authorized subscription | create one separately keyed delivery per subscription |
| source identity absent or ambiguous | quarantine/provenance-only; never guess from timestamp |

`SELECT` then insert is insufficient. `ON CONFLICT DO NOTHING` is acceptable only when the adapter reads the winner in the same transaction and compares immutable identity and hash. Conflict must remain visible to the caller; it cannot be converted to accepted success. Idempotency records and source mappings retain schema version, source digest, checkpoint and correlation for replay and cutover evidence. A schema-level uniqueness key that includes `request_hash` is not sufficient for this contract because it permits same-key/different-hash rows; the hash must be compared against the single scoped-key winner.

## 5. CAS, leases and stale worker fencing

All task, delivery and outbox lifecycle mutations use conditional updates. A claim is eligible only when the row is due and either has no lease or has an expired lease:

```text
(task/delivery state is queued or retry_wait and available_at <= now)
OR
(state is leased and lease_until <= now)
```

Outbox equivalents are `pending`/`retry_wait`, with `leased` reclaimable only after expiry. Terminal states are excluded. `FOR UPDATE SKIP LOCKED` may reduce contention but cannot replace the ownership predicate.

A successful claim atomically:

1. verifies row identity, tenant/partition and eligible state;
2. verifies empty or expired lease;
3. assigns unique worker ID;
4. increments a monotonic fencing generation/token;
5. sets lease expiry and the leased state;
6. increments the attempt counter exactly once;
7. returns the complete `LeaseToken(owner_id, generation, lease_until)`.

Completion, retry, release and dead-letter operations update only when task/delivery/event ID, partition, current owner and fencing generation match the lease. Where policy requires it, the lease must also still be valid. A zero-row update returns `StaleCasOutcome`, never success. A conflicting terminal/current state returns `Conflict`; a valid state transition returns the updated state. The adapter must not silently retry a stale command with a new lease.

Lease expiry makes a row reclaimable; it does not authorize the old worker to finish. The new claimant receives a strictly newer generation. Unknown remote delivery outcomes are retried with the same stable delivery key/body hash, preserving at-least-once semantics. The adapter must emit redacted audit evidence for lease expiry, reclaim, stale completion, duplicate-send suspicion and worker fencing.

## 6. Separate `project_triggers` contract

`project_triggers` is a separate trigger queue and must have a separate adapter/worker decision. Its current legacy row contains `project_id`, `kind`, immutable-ish JSON payload, status, attempts, `available_at`, `lease_until`, `leased_by` and `last_error`, but no target-safe generation. Existing owner-agnostic completion methods cannot prove that a stale worker lost ownership.

The context handoff requires the typed parent:

```text
tenant_id -> project_id -> run_id
```

`ProjectTriggerIntent` preserves source `trigger_id`, immutable payload, trigger kind/version, tenant/project partition, request hash, checkpoint and correlation. It maps to a target task/run and immutable outbox publication. It creates an outbound delivery only when a target automation explicitly declares that action. The legacy trigger payload must not be copied into webhook delivery rows merely because both rows have leases.

Before target claims are enabled for a project-trigger partition, the implementation must establish one of these reviewed fences:

- add a target-compatible owner/generation CAS boundary and drain legacy leases; or
- stop legacy claims, wait for active claims to complete or expire, and install an atomic partition-level fence that target claims and completions check.

Until that evidence exists, `ProjectTriggersPort` remains a separate compatibility boundary with no target ownership operations. A target mirror or expired `leased_by` value is insufficient. Promotion evidence must explicitly record legacy claims stopped, active leases drained, parity zero for the observation window, replay/idempotency verified, checkpoint ID and target worker fence.

## 7. Legacy mapping and cutover state machine

The adapter imports legacy rows as shadow evidence with source identity and digest. It preserves the distinction between:

- `webhook_subscriptions` -> endpoint/automation mapping;
- `webhook_deliveries` -> task/event/outbox/delivery mapping;
- `project_triggers` -> project-trigger task/outbox mapping.

The legacy `project_id = NULL` subscription scope remains company-wide under its original contract; it is not guessed into one project. `fail_count` and legacy attempts are historical provenance unless a fence proves no source worker can mutate the row concurrently. Active legacy leases are never copied as target ownership.

A bounded per-tenant/subscription/delivery or tenant/project trigger partition follows these states:

1. **inventory** — freeze source shape, event names, scope, leases and counts;
2. **import/shadow** — create mappings and target evidence; target claims disabled; invalid/orphan/unsupported/hash-conflict rows quarantined;
3. **parity** — compare tenant/scope, source identity, event/payload digest, schedule, terminal outcome and lease state during a declared observation window;
4. **dual-write, legacy owner** — accepted operations record durable target intent in the same trusted logical transaction or a durable reconciliation marker; target worker remains disabled;
5. **final delta/fence** — stop legacy claims/writes for the partition, drain or expire leases, reconcile final delta, install one writer generation and record immutable evidence;
6. **target read/compatibility write** — target reads are authoritative; legacy rows are read-only projection/snapshot; compatibility routes translate into target commands;
7. **target owner** — target claims are the only eligible worker path, legacy writers are fenced, and T10 promotion records the exact owner transition.

No global switch is inferred from row existence. A mismatch, unknown mapping, unresolved parent, duplicate side effect, active unfenced worker or missing audit blocks only the affected partition but prevents its promotion.

## 8. Rollback and recovery

Before target read cutover, stop target import/repair for the affected partition, keep legacy authoritative, retain target mappings/quarantine/audit and repair forward. After target read cutover, do not restore legacy as a second authority: freeze target mutations/claims as needed, fence the target generation, keep target reads authoritative and repair through target CAS/idempotency commands.

After target worker ownership:

1. stop target claims and drain or expire target leases;
2. advance the target worker fence so stale workers receive zero-row CAS;
3. retain target event, task, delivery, mappings and audit history;
4. replay only through the target identity and original stable key;
5. record rollback checkpoint, partition, reason and remote-outcome decision;
6. keep legacy rows projection-only and never re-enable them for the same side effect.

Rollback cannot delete evidence, reset attempts, mint a replacement event, or cause both workers to become eligible. Cleanup/drop of legacy tables and trigger queues is a later retention decision, separate from T09 and from webhook cutover.

## 9. Target ORM/Alembic implementation handoff

The implementation must follow the target ORM map and current parent-before-child chain:

| Dependency/task | Required automation implication |
|---|---|
| T01 `scope_registry` (`2026110101`) | typed tenant/cabinet/project/plugin anchors and RLS parent proof before automation intent |
| T02 definition/version (`2026110102`) | exact automation/endpoint parent and version/artifact identity |
| T03/T04 instance/FK closure (`2026110103`/`0104`) | required where an automation action binds a plugin instance/runtime |
| T05 Audit V2 (`2026110105`) | full immutable audit append and trusted transaction; no narrow post-commit audit |
| T06/T07 grants and RLS (`2026110106`/`0107`) | required when the task/action has capability authorization; writer epoch is checked, not inferred |
| T08 runtime/resources (`2026110108`) | required for runtime/materialization/resource side effects and exact revision/binding provenance |
| T09 automation outbox (`2026110109`) | `automation_outbox.py`: intent, idempotency, task/run, event/state/enqueue, delivery, leases and compatibility markers |
| T10 promotion (`2026110110`) | immutable per-partition ownership record, final delta, worker fence and rollback window |

If the repository head differs from the planned baseline, the implementation must generate a new linear revision from the confirmed head. The planned IDs are not permission to run Alembic or rewrite an applied revision. The ORM model is the source of schema truth; revisions are generated and reviewed in a later implementation PR. Constraints may be staged `NOT VALID`, but positive claims require explicit validation evidence. New tenant-owned tables require `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, named `USING` and `WITH CHECK` policies, and trusted-role privilege review.

Required target constraints/indexes include, at minimum:

- unique scoped idempotency identity and request-hash conflict detection;
- separate event/enqueue identity and delivery identity constraints;
- real task/run parent and source mapping FKs with `RESTRICT` for provenance/security parents;
- due-state, lease-expiry, worker-generation and retry indexes;
- tenant/scope/project RLS parent `EXISTS` checks;
- no secret-value columns and no polymorphic FK pretending to prove ownership;
- no cascade that removes audit, mapping, quarantine, delivery evidence or promotion history.

The repository adapter must use the existing transaction-scoped unit of work and RLS GUC composition. It must not expose unconstrained ORM inserts to handlers or workers. Each operation returns an explicit outcome: `allow`, `deny`, `quarantine`, `conflict`, `stale-generation` or `system-failure`.

## 10. Acceptance artifacts and unresolved blockers

The implementation PR must provide, without executing them during the current freeze:

- model/constraint review for all identity boundaries and parent-before-child FKs;
- atomic business mutation + idempotency + task + event/state/enqueue evidence;
- exact duplicate replay and same-key/different-hash conflict fixtures;
- queue outage repair fixture proving no second run/event/delivery;
- task, delivery and outbox claim/reclaim/complete/retry/dead-letter CAS fixtures;
- stale owner/generation zero-row evidence and attempt-increment-once evidence;
- project-trigger versus webhook delivery separation and explicit trigger fence evidence;
- legacy lease drain, final-delta and one-worker ownership evidence;
- secret-reference, redaction, RLS/FORCE and cross-tenant/cross-project negative cases;
- shadow parity, immutable promotion record, pre-cutover rollback and post-cutover forward repair evidence.

Known blockers remain:

- current `ProjectTriggerRow` has no target-safe generation and legacy completion is owner-agnostic;
- application `ProjectTriggersPort` is intentionally empty, so trigger-queue fencing is not yet represented;
- current application aliases several repository responsibilities to one protocol and does not itself prove one trusted UoW, RLS context or T10 promotion owner;
- current `TaskRun`/`DeliverySchedule` compatibility contracts do not carry the complete typed scope/project/run/approval evidence required by the target map;
- no inspected SQL adapter or `automation_outbox.py` target ORM exists as-built;
- Audit V2, scope registry, exact parent/FK closure, RLS policy validation and promotion lineage remain upstream gates;
- legacy delivery/task rows without stable source event or real task/run parent require provenance-only retention or quarantine;
- retention policy, final-delta observation window and queue/worker fencing operational evidence are not yet approved.

Until these blockers are closed by implementation evidence and an immutable T10 promotion record, target positive automation intent, outbox delivery ownership and project-trigger target claims remain disabled or shadow-only.

## 11. Verification freeze

For this documentation-only review, the contract, the named migration source documents and the cited application automation contracts/ports were inspected. Only `docs/08-migration/automation-persistence-adapter-contract.md` was changed. Other code/core docs/ORM/Alembic/database/shared environments were not changed. Tests, build, lint, formatting, compilation, Alembic execution, migration execution and git checks were intentionally not run. Verification remains frozen until the migration is implemented, reviewed multiple times and the coordinator explicitly lifts the freeze.
