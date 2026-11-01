# Automation cutover adapter: legacy external API → domain/application

Статус: migration design artifact. Документ описывает только переход между legacy external API webhook surface и новой automation/application surface. Он не меняет legacy routes, ORM или существующую automation foundation.

## 1. Scope and ownership

### Legacy surface

Legacy external API (`/api/v1/external`) currently owns the following compatibility surface:

- `POST /external/webhooks`: creates an outbound subscription and returns its signing secret once;
- `DELETE /external/webhooks/{subscription_id}`: disables/removes a subscription according to the existing route contract;
- `webhook_subscriptions`: `company_id`, optional `project_id`, destination `url`, `events`, `secret_ref`, `enabled`, `fail_count` and audit timestamps;
- `webhook_deliveries`: one row per subscription/event, JSON payload, delivery status, attempt counter, retry time, response/error fields and worker lease (`lease_until`, `leased_by`).

The legacy delivery worker remains the owner of legacy rows until the delivery read/write cutover. Its existing event names and wire headers are compatibility behavior:

- `chat.message.completed` and `chat.turn.failed`;
- `X-Prodavan-Signature: sha256=<hmac>`;
- `X-Prodavan-Event`;
- `X-Prodavan-Delivery`.

`project_triggers` is a related but separate legacy queue for project trigger dispatch. It is not an alias for `webhook_deliveries` and must not be copied into outbound delivery rows merely because both use row leases. `ProjectTriggerService.enqueue` is the legacy trigger ingress: it validates trigger kind, project ownership/status, subscription and pause gates, then creates a queued `ProjectTriggerRow`. `claim_next`/`claim_by_id`, `mark_done`, `mark_failed` and `release_for_retry` are its worker lifecycle.

### Target surface

The new domain/application surface is the owner of new automation semantics:

- `AutomationDefinition`: versioned trigger/action definition and retry policy;
- `InboundWebhookEndpoint`: tenant-scoped endpoint metadata, `automation_id`, `secret_ref`, method and body limits;
- `TaskRun`: durable execution, independent from an agent session, with source event, idempotency key, state, retry and worker lease;
- `OutboundDelivery`: independently retryable delivery linked to a task run;
- `OutboxEvent` plus `OutboxStateRecord`: immutable event and transactional publication state;
- `IdempotencyRecord`: atomic claim and replay record for an ingress operation.

The target application service verifies an ingress, atomically records the deduplication claim and task/outbox intent, then enqueues with a stable idempotency key. A queue or outbox drainer may repair a failed enqueue; it must not create a second run.

The adapter is a bounded compatibility layer. It translates the legacy contract into target commands/read models and records source identity. It does not make legacy tables a second target source of truth after the relevant cutover checkpoint.

**As-built boundary (2026-11-01):** `AutomationService`, `OutboxRepository` and the domain outbox contracts exist as application-level contracts, but no SQL persistence adapter currently maps `WebhookService`/`WebhookDeliveryRow` to them. `WebhookService.emit` still inserts legacy delivery rows, and the existing worker still claims those rows. No complete persistence adapter, backfill, or production dual-write is implemented yet; the remainder of this document is the design and acceptance target.

## 2. Canonical data mapping

### 2.1 Subscription to endpoint/automation

| Legacy field/meaning | Target representation | Adapter rule |
|---|---|---|
| `webhook_subscriptions.id` | compatibility `legacy_subscription_id`; target endpoint id is immutable and separately generated | Preserve the source id for lookup, audit and rollback. Never use a lossy cast as the target primary key. |
| `company_id` | `tenant_id` plus resolved cabinet/project scope | Resolve the tenant from the company. A nullable legacy `project_id` remains a company-wide scope marker; it must not be guessed into one project. |
| optional `project_id` | trigger scope in `AutomationDefinition.trigger_config` or explicit target scope binding | `NULL` means all projects allowed by the legacy contract. Preserve this distinction in the adapter. |
| `url` | target `OutboundDelivery.destination_url` or endpoint delivery target configuration | Validate and normalize once at import; do not rewrite the URL during a retry. |
| `secret_ref` | target endpoint/delivery `secret_ref` | Copy the reference, never the secret value. Secret rotation is versioned and must remain valid for old deliveries during rollback. |
| `events` JSON array | one or more target trigger/event types | Preserve exact event names. Unsupported names are quarantined and block that subscription's cutover; do not silently widen to `*`. |
| `enabled` | `AutomationDefinition.status` (`active`/`paused`/`retired`) and endpoint `enabled` | `false` maps to paused, not deleted. Retired is reserved for an explicit removal decision. |
| `fail_count` | delivery/endpoint health projection | Import for observability only. It must not be used as the target attempt counter. |
| `created_by`, timestamps | target audit/provenance metadata | Preserve source timestamps and actor where available; source values are historical, not mutable target state. |

A legacy subscription that has only an outbound destination is represented as a compatibility automation definition whose action is `webhook.deliver`. A target automation may have richer actions; the adapter must reject or quarantine a definition that cannot be expressed without changing legacy behavior.

### 2.1.2 Project trigger mapping

`ProjectTriggerService` maps to the same target boundary, but not to outbound webhook delivery: `project_id` resolves `tenant_id` and `automation_id`, `kind` becomes the versioned trigger/action type, and the legacy `payload` becomes immutable `TaskRun.input_payload` plus an `OutboxEvent` publication. The subscription gate, deleted/draft/paused/active project policy and exempt trigger kinds remain explicit policy checks before ingress. `ProjectTriggerRow.id` becomes `source_event_id` (or is linked from an upstream event id); `status`, `attempts`, `available_at`, `lease_until`, `leased_by` and `last_error` map to target task/outbox state and provenance. A project trigger must not be fanned out as a webhook unless a matching target automation explicitly declares that action.

### 2.1.1 Secret reference policy

`secret_ref` is a scoped reference, never a secret value. The target adapter must validate its scheme and ownership against the active company/endpoint scope (and project scope where applicable), fail closed on cross-scope or unreadable references, and ensure plaintext is absent from DB rows, payloads, logs, and audit. Create may return plaintext once; subsequent reads return only the ref/masked prefix. Rotation creates a new secret version/ref, records actor/time/version without the value, and keeps the old version only for an explicit grace window. Revoke prevents new verification/delivery while retaining endpoint, delivery, and audit history.

This is distinct from legacy `api_app_keys.secret_hash`: that field stores a SHA-256 hash of a one-time-shown app token and cannot be used to verify an HMAC webhook or be converted back into a signing secret. App-key migration requires separate reissue/rotation policy; copying its hash into a `secret_ref` is invalid. Existing webhook rows already use refs, but their presence alone does not mean target scope validation, rotation, or revoke semantics are implemented.

### 2.2 Delivery to task, outbox and delivery

| Legacy field/meaning | Target representation | Adapter rule |
|---|---|---|
| `webhook_deliveries.id` | `legacy_delivery_id`; target `OutboundDelivery.id` may be generated from a stable mapping | Keep a one-to-one mapping record. Derive `source_event_id` from an immutable upstream event id when available; otherwise use a documented, immutable legacy-delivery identity only as a weaker fallback and quarantine rows where neither exists. A target `OutboundDelivery` also requires a real `task_run_id`; create/link that run through the application contract or keep the row in compatibility projection, never invent a missing run. Retries must never create another target delivery for the same source row. |
| `subscription_id` | `task_run.automation_id`/endpoint compatibility link | Resolve through the imported subscription mapping; missing subscription is quarantine, not an inferred tenant. The mapping must also provide the target `tenant_id`; never infer tenant from a delivery payload or URL. |
| `event_type` | `OutboxEvent.event_type`, then target task/event projection | Preserve exact type and schema version. |
| `payload_json` | immutable `OutboxEvent.payload` and target delivery payload | Copy as canonical JSON after redaction/size validation. Do not mutate an already queued payload when the subscription changes. |
| `status=pending` | `OutboxState.PENDING` plus `DeliveryState.QUEUED` | Source state is translated only at the boundary; these are separate target state machines. The target state machine remains authoritative after target ownership begins. |
| `attempts` | target delivery attempt counter | Import as historical attempts only if the cutover checkpoint proves no worker can increment the source row concurrently; otherwise start a target attempt counter at zero and retain the source count in provenance. |
| `next_attempt_at` | `available_at`/`next_attempt_at` | Preserve the not-before time, subject to an explicit clock/time-zone normalization. |
| `response_status`, `last_error` | target delivery diagnostics (`last_status`, `last_error`) | Copy redacted diagnostics; no secrets or full response bodies. |
| `lease_until`, `leased_by` | target `leased_by`, `lease_until` / `DeliveryLease` | Never copy an active lease as ownership. Reconcile or wait for expiry, then claim with a new worker id. |
| `delivered_at` | target `delivered_at` and terminal state | A delivered legacy row is terminal; it must not be replayed during backfill. |

The target transactional invariant is: business state, immutable outbox event and enqueue intent commit in one database transaction. A post-commit queue kick is an optimization/recovery action, not the source of truth.

### 2.3 Route mapping

During compatibility operation, legacy paths remain stable:

| Legacy route | Adapter behavior | Target command/read model |
|---|---|---|
| `POST /api/v1/external/webhooks` | Validate legacy request/auth/status codes; create or reserve a compatibility subscription and target endpoint/definition according to the active mode | `create_endpoint` + `create_automation` with an operation idempotency key |
| `DELETE /api/v1/external/webhooks/{id}` | Resolve legacy id through mapping; preserve legacy response behavior | Pause/retire endpoint and definition; do not hard-delete deliveries or audit history |
| Legacy delivery worker | Allowed only for rows still owned by legacy | Target delivery/outbox worker after delivery cutover; never run both workers for the same ownership partition |
| Existing event fanout | During dual-write, emit the target event/outbox intent in the same logical operation and mark correlation metadata | `OutboxEvent` with source aggregate/event identity |

New target-native routes may be introduced separately. They must not be silently exposed through a legacy route unless the request/response, auth scope, error codes, secret-once behavior and event payload have an approved compatibility mapping.

## 3. Migration phases and dual-read/dual-write policy

The adapter uses an explicit per-tenant, per-subscription (and, for deliveries, per-delivery partition) mode. No global switch is inferred from row existence.

### Phase A: inventory and import-only

1. Freeze the legacy schema contract: row counts, status values, event names, retry settings, ownership scope and active leases.
2. Create mapping/provenance records and import definitions/endpoints without changing legacy reads or writes.
3. Import only immutable or safely replayable delivery data. Quarantine orphan subscriptions, invalid scopes, unsupported event names, malformed payloads and secret references that cannot be resolved.
4. Generate parity reports: subscription counts, enabled state, company/project scope, event sets, destination fingerprints, pending/delivered/exhausted counts and delivery identity hashes.

No target worker may claim an imported delivery in this phase.

### Phase B: dual-read parity, legacy owner

Legacy remains authoritative for route responses, event fanout and delivery state. The adapter performs target reads for diagnostics only and compares:

- scope and enabled status;
- event type and canonical payload hash;
- destination and secret reference identity;
- attempt/retry schedule;
- terminal outcome and delivery id.

A mismatch is observable and blocks promotion of that partition. Dual-read is bounded by a declared checkpoint and observation window; it is not a permanent read strategy.

### Phase C: dual-write, legacy owner

For a bounded partition, every accepted legacy subscription mutation and every newly created legacy delivery writes the corresponding target command/projection. The legacy transaction remains the request's authoritative commit until ownership changes.

Dual-write rules:

- the same operation idempotency key and correlation id are used on both paths;
- target write failure fails closed for operations where target parity is required; it must not be hidden as a successful legacy-only mutation;
- a target write is retried through a durable repair/outbox mechanism, never by a second ad hoc insert;
- no independent target-side event is emitted that could cause a duplicate external delivery;
- updates are ordered by source version/updated timestamp, with a tie-breaker from the mapping record; stale writes are ignored and audited;
- legacy and target workers do not both deliver the same delivery row.

The migration should prefer target projection/intent writes in the same application transaction once the repository supports it. If the legacy transaction cannot atomically include the target intent, the adapter must record a durable reconciliation marker and keep the partition in legacy-owner mode until the marker is complete.

### Phase D: target read, legacy compatibility write

After parity is zero for the observation window, target reads become authoritative for management and delivery status. Legacy route responses are served from the target projection or a verified compatibility facade, while legacy rows remain a mirror for rollback.

Legacy writes are translated into target commands. Direct legacy table mutation is prohibited for the migrated partition. The facade must preserve legacy response semantics and reject ambiguous updates rather than silently applying them to both models.

### Phase E: target owner

The target endpoint/automation, task run, outbox and delivery state are authoritative. Target workers claim and deliver. Legacy rows are retained as a read-only compatibility snapshot with source/target ids, cutover checkpoint and last mirrored version. No new legacy delivery row may be created.

## 4. Idempotency and duplicate side effects

Idempotency is required at ingress, task creation, outbox publication and outbound delivery. It is not sufficient to deduplicate only by URL or payload hash.

### Atomic delivery deduplication and conflicts

The target delivery identity is `(subscription_id, source_event_id)`, with `tenant_id` and `automation_id` resolved from the immutable subscription mapping. `request_hash` is SHA-256 over a versioned canonical request envelope and excludes lease, attempt, response status, timestamps used only for diagnostics, and other mutable fields. Enforce a database unique constraint/index on `(subscription_id, source_event_id)`; retain `request_hash` on the row and compare it on every duplicate attempt. A separate outbox/event uniqueness key must cover the target event identity required by `OutboxEvent` (tenant, aggregate type/id/version), and must not be confused with delivery uniqueness.

An exact `(subscription_id, source_event_id, request_hash)` match returns the original delivery/run (`accepted=true`, `duplicate=true`) and creates no second intent; an optional broker enqueue is a repair operation and must itself be idempotent. The same subscription and source event with a different request hash is an explicit idempotency conflict (stable 409/error plus redacted audit), never an overwrite or silent second side effect. The same source event/hash for different subscriptions creates one delivery per authorized subscription. Legacy rows without source event identity are imported only with provenance or quarantined; identity cannot be guessed from a mutable timestamp. `record_and_enqueue` must atomically persist the `IdempotencyRecord`/task run, immutable outbox event, initial state, and `OutboxEnqueueIntent` in one DB transaction; a queue outage after commit is repaired by the durable intent, never by creating another run.

`SELECT`-then-insert is not sufficient under concurrency. `ON CONFLICT DO NOTHING` is acceptable only when the adapter reads the winner and verifies immutable identity/hash; it must not hide a payload conflict. The current `OutboxRepository` protocol requires event + initial state + queue intent atomicity, while the `AutomationRepositoryPort` requires ingress/task/outbox atomicity; the corresponding SQL adapters are not implemented as-built.

### Ingress and route operations

- For legacy create/update/delete requests, resolve `tenant_id` from the authenticated app-key company mapping (and preserve cabinet/project scope), then derive an operation key from authenticated principal, scope, route operation and caller-provided key where available. If the legacy request has no key, the adapter assigns a deterministic compatibility key from the request correlation id and records that limitation.
- Store `(tenant_id, operation, key, request_hash, task_run_id/response)` atomically. `request_hash` is SHA-256 of a versioned canonical request envelope; exclude secrets, lease/attempt state, response diagnostics and timestamps that are not request inputs. Reusing a key with a different canonical request hash is a conflict, not a second mutation.
- Retrying the same `POST /external/webhooks` returns the original mapped endpoint/legacy response; it must not create a second secret or subscription. The legacy create path currently has no idempotency-key field, so the adapter must define the correlation-key lifetime/retention and reject ambiguity rather than claim stronger semantics.
- A duplicate webhook event is identified by stable source plus `source_event_id`, which must be copied to `TaskRun.source_event_id` and included in the stable task idempotency key. If the legacy producer supplies neither, derive `source_event_id` from the immutable legacy delivery mapping only as a documented weaker fallback; never use a mutable timestamp. Record the weaker-idempotency decision in audit.

### Event and delivery identity

- Legacy delivery identity is `(legacy_subscription_id, legacy_delivery_id)`; target identity is the mapping row plus immutable event id.
- Target outbox identity is the aggregate event key `(tenant, aggregate_type, aggregate_id, aggregate_version)`, or the preserved legacy event id when the source has no aggregate version.
- The destination receives one stable delivery id and idempotency header/key across all retries and across rollback. A timeout after remote acceptance is therefore treated as an unknown outcome: retry with the same key, never mint a new delivery. This is explicitly **at-least-once** delivery, not exactly-once; downstream must honor the stable key, persist dedup results for the replay/rollback window, and reject the same key paired with a different body/hash.
- Payloads and signatures are computed from the immutable canonical raw body. Re-serializing a mutable mapping must not change the signed bytes after enqueue.

## 5. Lease ownership and worker fencing

A lease is ownership, not a hint. Every claim must be an atomic conditional update using the row id, eligible state and an expired-or-empty lease condition. `FOR UPDATE SKIP LOCKED` may reduce contention but does not replace the ownership predicate.

The target due/reclaim predicate is `(state in {queued, retry_wait} AND available_at/next_attempt_at <= now) OR (state = leased AND lease_until IS NOT NULL AND lease_until <= now)`. For the outbox state machine the equivalent due states are `PENDING` and `RETRY_WAIT`; `LEASED` is reclaimable only after expiry. Terminal states are excluded. A successful claim atomically sets a new unique `owner_id`, a monotonically increasing `lease_generation` (or opaque fencing token), `lease_until`, and increments attempts. A concurrent loser or stale owner receives zero affected rows. The generation/token must be persisted on the task/delivery lease state and included in every completion/retry CAS predicate; `owner_id` alone is insufficient after an expired lease is reclaimed.

For every delivery/task claim:

1. Set a unique worker/owner id, a new fencing generation/token, and `lease_until` in the same transaction as the state transition. Legacy `leased_by` + `lease_until` rows have no fencing generation, so they must be drained/expired and fenced by partition/generation before target claims; do not treat them as target-safe leases.
2. Increment the attempt counter only once, by the successful claimant.
3. On completion or retry, update only if the stored owner and fencing generation/token still match (and, where used, the lease is still valid). A stale worker cannot mark a row delivered after lease takeover.
4. On crash, leave the row recoverable; after expiry another owner may claim it. Do not manually clear a live lease during normal migration.
5. Before changing ownership from legacy to target, stop new legacy claims, wait for active leases to finish or expire, reconcile rows with expired leases, then atomically mark the partition target-owned.
6. A target worker must not claim a legacy-owned row merely because a target mirror exists. Ownership is recorded in the mapping/cutover checkpoint. For `project_triggers`, the current legacy implementation has only `leased_by`/`lease_until` and owner-agnostic completion methods; it is a separate queue and cannot be declared safely fenced or migrated by this webhook adapter without adding an explicit generation/CAS or a partition-level fencing barrier.

The lease timeout must exceed the maximum expected attempt transaction/transport window and be observable. Lease expiry, takeover, stale completion and duplicate-send suspicion are migration alerts.

## 6. Rollback

Rollback is a forward routing change, not a destructive schema downgrade.

### Before target read cutover

Disable target writes/repair for the affected partition and continue legacy reads/writes. Leave imported target rows and provenance intact. Re-run parity before any later attempt.

### After target read cutover but before target worker ownership

Do not switch the read authority back to legacy: freeze further promotion, keep target reads authoritative, and stop only the affected target mutations/claims. Preserve target run/outbox history and reconcile any stale legacy mirror from immutable target events. A legacy mirror is never a rollback authority after the read boundary.

### After target worker ownership

1. Stop target claims and wait for target leases to drain or expire.
2. Fence the target worker generation so stale workers cannot complete deliveries.
3. Keep the target event/outbox and mapping authoritative; do not restore ownership to legacy and do not make legacy workers eligible for target rows.
4. Re-drive only through the target path. For unknown remote outcomes, retry with the original target delivery id/key; never create a replacement event or switch to a legacy delivery id.
5. Keep target runs, outbox events, mappings and audit records readable. Do not delete them or reset attempts to conceal the rollback.
6. Record the rollback checkpoint, reason, affected scope and replay decision. A target row that has no safe target representation remains target-readable and is quarantined for forward repair; an unreconciled legacy mirror is not a rollback authority.

A rollback is not approved if it would make both workers eligible for the same side effect or lose the only stable idempotency key.

## 7. Removal criteria and cleanup order

The adapter and legacy external webhook tables/routes may be removed only after all criteria below are evidenced for the agreed retention and rollback window:

- every legacy subscription has a target endpoint/automation mapping, or an explicit owner-approved retirement/quarantine decision;
- no new legacy subscription, delivery or direct `project_triggers` compatibility write occurs for the migrated scope;
- all legacy routes are either retired with a published deprecation response or are implemented by a target-backed facade with no legacy table dependency;
- target reads and workers have owned the full scope for the observation window;
- dual-read mismatches, orphan mappings, stale leases and duplicate-side-effect alerts are zero or explicitly accepted with an owner and expiry;
- target idempotency records, aggregate event keys and delivery ids remain replayable through the retention window;
- all in-flight legacy leases are drained/expired and legacy worker claims are permanently fenced; this includes an explicit owner/generation CAS or partition-level fence for any queue being migrated, while `project_triggers` remains gated by its own trigger-queue decision;
- retry/backoff, exhausted/dead-letter, endpoint disablement, secret rotation and audit behavior have target parity evidence;
- inbound webhook `secret_ref` scope/rotation and outbound HMAC `secret_ref` scope/rotation are tested as separate contracts, and neither is conflated with `api_app_keys.secret_hash` (SHA-256 of the one-time bearer token);
- rollback has been rehearsed from the retained target/legacy snapshots and does not require live legacy writes;
- dashboards, alerts, runbooks, route owners and compatibility facade removal date are recorded;
- product/API owners approve removal independently of schema migration approval.

Cleanup is staged:

1. disable legacy writes and worker claims;
2. keep tables read-only for the retention/rollback period;
3. export or snapshot mappings, delivery outcomes and audit metadata;
4. remove legacy route adapters and worker code in a separate reviewed change;
5. archive/drop legacy tables only in a later migration after the retention decision.

Dropping `webhook_subscriptions`, `webhook_deliveries` or related indexes is never part of the first target schema or route cutover. The same rule applies to `project_triggers`: it is removed only under its own trigger-queue cutover decision, not as incidental cleanup of outbound webhooks.
