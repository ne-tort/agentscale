# M06 audit append shape decision

Status: migration-stage decision artifact, documentation only. Baseline: 2026-11-01.

This decision closes the contract gap between `RedactedAuditOutcome` and `AuditOutcomeEvidence`. It does not change ORM, application code, core documentation, database state, or the Alembic chain. The target is the M06 audit persistence boundary used by canonical grants, runtime admission, mapping, quarantine, and promotion evidence.

## 1. Problem and invariants

`RedactedAuditOutcome` is currently a small MCP-oriented value: tool identity, argument digest, result/error, duration, timestamp, and optional correlation. It deliberately omits the full M06 identity needed to replay or authorize the evidence boundary: tenant, cabinet/project, app definition/version/artifact, scope/plugin instance, subject/session, runtime and writer generations, lease, capability/mapping/snapshot identity, source identity, event identity and decision.

`AuditOutcomeEvidence` retains the complete `AuditOutcome` together with the redacted projection and validates correlation equality. The transaction seam therefore has the information required to preserve M06 identity, while a direct `append_audit_outcome(RedactedAuditOutcome)` can persist an event that cannot be bound to the tenant/app/grant/runtime/mapping/payload decision that produced it.

The following invariants are non-negotiable:

- Raw MCP arguments, raw payloads, credentials, tokens and secrets never enter the audit contract or persistence row. Canonical argument/payload/binding/mapping/snapshot digests are sufficient evidence.
- A redacted MCP projection is not an authorization identity and cannot be the sole trusted append input.
- Every trusted event is immutable, tenant-scoped, correlation-replayable and bound to the exact M06 identity available for that event.
- A successful authorization decision and a successful tool execution are different facts. They may be linked, but neither may be inferred from the other.
- Audit append failure is a deny/system failure for a positive mutation. It never becomes an allow and never authorizes a grant, runtime transition, promotion or cutover.

## 2. Options considered

### Option A: extend `RedactedAuditOutcome` to the full M06 envelope

Add tenant/app/grant/runtime/mapping/payload and event fields to the existing redacted type and make it the persistence input.

Advantages: one apparent type and a simple repository signature; existing direct append callers have a migration path.

Rejected because the name and current shape describe a redacted MCP result, while the required fields are decision/provenance identity rather than MCP-only redaction. It encourages callers to manufacture or copy authoritative ownership into a projection and still leaves no explicit relationship between the full decision and the MCP result. It also makes future optional identity fields ambiguous: absent fields could mean “not applicable”, “not resolved”, or “lost before append”. Extending the type alone does not establish atomic coupling to grant/mapping/runtime writes.

### Option B: append a wrapper/full evidence object

Keep `RedactedAuditOutcome` as the MCP projection, and append an `AuditOutcomeEvidence`-equivalent wrapper containing the immutable full `AuditOutcome` plus the redacted projection and explicit event identity.

Advantages: preserves the useful narrow projection, makes the full envelope mandatory at the boundary, and gives readers a clear place to validate projection-to-envelope consistency. It can be used as the input to one trusted transaction containing related grant/mapping/lifecycle writes.

Rejected as the sole shape because a wrapper can still be passed to a non-transactional append implementation unless the port contract explicitly requires the transaction seam. It is retained as the in-memory/domain evidence shape and as the compatibility bridge, but not as permission for independent direct appends.

### Option C: separate normalized identity table

Persist the redacted outcome in `audit_outcomes` and put M06 identity in a separate normalized identity table linked by event/correlation ID.

Advantages: avoids repeating identity on every event and may support identity-oriented queries.

Rejected as the primary boundary. A split row introduces a partial-commit and orphan/reassociation problem, complicates RLS/FK enforcement, and makes an audit event temporarily non-replayable. Correlation IDs are not sufficient ownership proof and may span multiple event types. A normalized identity table may be added later for measured query/load needs, but it must be an immutable child/snapshot written in the same transaction and cannot make the base audit row identity-optional.

### Option D: enforce transaction-only atomic append

Make the full evidence envelope the only trusted append input. The transaction seam receives the full `AuditOutcomeEvidence` (or an equivalent immutable full-evidence command), validates the redacted projection, and atomically appends audit plus any related mapping/quarantine/grant/runtime/promotion evidence. A direct redacted append remains only a legacy/compatibility surface for non-authoritative telemetry and is prohibited for M06 trusted audit.

**Decision: choose Option D, using Option B as the domain evidence shape.**

This preserves the redaction boundary while making loss of M06 identity impossible at the trusted persistence boundary. It also aligns with `m06-trusted-persistence-adapter.md`: grant/mapping/lifecycle mutation and audit must commit together; audit failure rolls back the positive mutation.

## 3. Target contract shape

The conceptual target is:

```text
AuditOutcomeEvidence {
  outcome: AuditOutcome                 # immutable complete M06 envelope
  redacted: RedactedAuditOutcome        # derived MCP projection, no raw values
  event_id: outcome.event_id
  correlation_id: outcome.correlation_id
  identity_digest: digest(full canonical identity + event fields)
  schema_version: audit envelope version
}

TrustedAuditTransaction.append(evidence, transaction_context, related_evidence)
```

The persisted `audit_outcomes` row is a full redacted envelope, not a raw payload store. It includes, where applicable:

```text
event_id, event_type, occurred_at, schema/redaction version
decision, reason_code, result_status, error_code, duration_ms
tenant_id, cabinet_id, project_id, user_id/subject_id, session_id
app_definition_id, app_id, app_version_id, manifest_version, app_artifact_digest
plugin_instance_id, runtime_generation, writer_generation, lease_id
capability_grant_id, capability_key
source_kind, source_id, mapping_version, capability_mapping_digest
capability_snapshot_digest, payload_digest, arguments_digest
operation, resource, domain
request_id, trace_id, span_id, correlation_id
profile_id, tool_server, tool_name, tool_version
redaction marker/version and redacted metadata only
```

Fields that are inapplicable to an event may be null only when the event class explicitly permits that absence. Fields required by the event class must be present; unresolved identity is a quarantine/migration-admin event, not a positive trusted outcome. A client/plugin assertion is retained only as redacted mismatch metadata and never replaces server-resolved identity.

`RedactedAuditOutcome` remains a projection with MCP fields (`mcp_trace_id`/trace, span, profile, tool, argument digest, result, error, duration, occurred time, correlation). It must be derived from the full outcome and checked for equality of correlation, result/error, timestamp and digests before append. It must not be independently accepted as complete M06 audit evidence.

The adapter must preserve the original decision and result. It must not map an error into allow, turn quarantine into positive evidence, or infer a successful execution from an allow decision.

## 4. Correlation, event, and result semantics

### Event identity

`event_id` is the immutable identity of one audit fact. It is unique within the audit owner and is stable across retries. A retry with the same event/source identity and identical canonical digest is idempotent. Reusing an event/source identity with a different digest is a conflict and goes to quarantine/security review; it is never overwritten.

`correlation_id` groups related facts across authorization, invocation and final result. It is not a uniqueness key and must not be used as ownership proof. A single correlation may contain at least:

- authorization/pre-invocation decision;
- invocation/final adapter result;
- mapping, fencing, quarantine, promotion or rollback evidence when those facts belong to the same operation.

`request_id`, `trace_id` and `span_id` retain transport/observability lineage. MCP identity remains separate from declarative `capability_key`; `tool_name` is never converted into an authorization capability.

### Decision and result

`decision=allow` means the authorization policy admitted the operation. `result_status=allow` means the adapter/tool returned success. A final success event is required after a successful adapter return and links to the authorization event through `correlation_id` and request/trace/span fields.

`decision=deny` records fail-closed policy or missing/expired/revoked authorization. `result_status=deny` is used for a denied operation; an error code is required for non-allow results.

`decision=quarantine` records unresolved, ambiguous, unsupported, conflicting or unsafe evidence. It has `result_status=error`, is visible only under the quarantine/RLS policy, and is never positive parity or authorization evidence.

An invocation exception, malformed result, missing adapter, stale generation/lease or audit failure produces a linked error/deny event as appropriate. An allow event without a final result is incomplete execution evidence and cannot be represented as successful execution.

## 5. Atomic append boundary

For a positive issue, revoke/reissue, runtime admission/lifecycle transition, or any event that is evidence for canonical promotion, the trusted transaction must:

1. Resolve the authenticated tenant and typed scope server-side and set transaction-local RLS context (`app.tenant_id`, optional cabinet/project/user/role and writer epoch).
2. Validate exact definition/version/artifact, subject/session, plugin instance, runtime generation, writer generation and lease where the event class requires them.
3. Canonicalize and digest capability payload, mapping identity, binding identity, snapshot identity and redacted MCP arguments. Reject broadening or unknown mappings.
4. Compare source identity and digest for idempotency. A changed digest is conflict/quarantine.
5. Perform the eligible grant/mapping/quarantine/runtime/promotion operation and append the complete audit row in the same commit boundary.
6. Commit only after RLS `USING`/`WITH CHECK`, parent checks, FK/check constraints, trusted role privileges and append-only audit enforcement succeed.

The transaction seam is therefore the only trusted path for M06 audit. A failure rolls back the associated positive mutation and returns deny/system failure. Separate append calls may be used for explicitly non-authoritative telemetry only when marked outside the M06 audit contract; they must not be used to claim a completed grant, runtime transition, mapping, quarantine resolution or promotion.

## 6. Append-only, RLS, FK, and retention rules

### Append-only

`audit_outcomes` is insert-only. Application/API roles have no `UPDATE` or `DELETE`; a deny-by-default trigger or equivalent reviewed writer boundary rejects mutation of identity, digests, decision, result, tenant or historical timestamps. Corrections are forward events linked to the prior event; no row is rewritten. Audit evidence is not deleted to hide a mismatch or rollback.

### RLS

The table has `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, with named `USING` and `WITH CHECK` policies. Tenant reads require server-set transaction-local `app.tenant_id`; optional cabinet/project and parent `EXISTS` checks must agree. Inserts are limited to the trusted audit writer/transaction role. Missing, malformed or client-supplied-only tenant context denies. Unresolved migration/quarantine events are migration-admin/security-only and cannot be used for ordinary authorization.

RLS is applied to normalized identity/evidence children too, if introduced. A copied tenant column is an index/RLS aid, not ownership proof; parent anchors and exact compatibility checks remain mandatory.

### FK and deletion semantics

Optional references from audit to grants, definitions, versions, mappings, snapshots or runtime rows use `ON DELETE SET NULL` only where the reference is genuinely optional. The immutable source IDs and digests remain in the audit row. Audit must never cascade-delete with a grant, compatibility projection, runtime revision or legacy table. Owner/provenance parents use `RESTRICT`, archive transitions, or retention-protected records. Any normalized identity child uses a non-null FK to its audit event with `ON DELETE RESTRICT` and is inserted in the same transaction.

### Retention

Retention of audit/security evidence is independent from runtime rollback and grant cleanup. Events remain through the rollback window, legacy projection retirement, grant revoke/expiry and owner archival. A later retention job may archive or purge only after policy-approved legal/security retention, export/replay verification, tenant partition authorization, and dependent-reference review. It must preserve immutable event/source/correlation digests and an archive marker. No downgrade, promotion rollback or compatibility cleanup may delete audit history.

## 7. Migration, backfill, and compatibility

1. **Freeze and inventory.** Record current audit table/schema, producers, direct `RedactedAuditOutcome` append callers, tenant ownership vocabulary, retention policy and existing correlation/source keys. No positive writes or cutover are enabled by this decision.
2. **Create/align empty target shape.** Add the full `audit_outcomes` envelope and append-only/RLS/privilege constraints through the reviewed model/Alembic process. Do not hand-edit an applied revision or perform shared-environment DDL.
3. **Introduce the transaction seam.** Change the trusted adapter contract to require full evidence and linked transaction context. Keep `AuditOutcomeEvidence` as the compatibility/domain bridge. Direct redacted append is explicitly marked non-authoritative and cannot satisfy M06 gates.
4. **Classify legacy rows.** Backfill in bounded, resumable transactions keyed by source/event identity. Preserve source IDs, occurred timestamps and canonical digests. Rows with complete identity become historical audit evidence; rows missing tenant/app/grant/runtime/mapping identity become quarantine/migration-admin evidence, never guessed positive identity.
5. **Dual-read and parity.** Compare legacy and target event counts, event/source identity, decision/result, correlation, full identity digests and redaction invariants. Unknown mappings, unresolved identity, raw-value detection or unexplained mismatch blocks the partition.
6. **Compatibility projection.** If legacy audit consumers remain, project from full target evidence to the old narrow redacted shape. The projection is read-only and may omit fields for presentation, but it cannot write back or become the authorization source. New M06 producers write through the transaction seam only.
7. **Bounded cutover.** Fence legacy authoritative writers for one `(tenant_id, app_definition_id)` partition, drain in-flight work, apply final idempotent delta, append cutover evidence atomically, then switch the reader/owner. Keep legacy data as a read-only projection during the rollback window.
8. **Retirement.** After retention, replay, parity and zero-writer checks, remove only the compatibility adapter/projection in a later forward migration. Keep `audit_outcomes`, mappings, quarantine, promotion records and source digests protected.

A missing legacy field is not backfilled from request assertions, tool names, JSONB guesses or a neighboring event. It is classified as incomplete/quarantined. Historical rows may remain readable under restricted policy without becoming eligible for positive writes.

## 8. Positive-write and promotion gates

No canonical positive grant, reissue, runtime mutation, promotion finalization or canonical-owner cutover may proceed unless all are true in one trusted transaction:

- authenticated principal resolves exactly one retained canonical tenant and transaction-local context is set by the server;
- cabinet/project/scope, definition/version, immutable artifact and exact subject/session/instance identity pass parent proof;
- runtime generation, lease and current writer-generation CAS pass where required;
- capability key is preserved, mapping version is approved, and payload/binding/mapping/snapshot/argument digests are canonical and conflict-free;
- event identity/source identity is idempotent, correlation is present and the redacted projection matches the full evidence;
- decision/result/error semantics are valid; quarantine, stale, unresolved, ambiguous or unsupported evidence is fail-closed;
- audit append succeeds under trusted role, append-only boundary, FK/check constraints and RLS `USING`/`WITH CHECK`;
- related grant/mapping/runtime/promotion evidence is committed atomically, with no stale legacy writer and no unexplained parity mismatch;
- promotion finalization creates a new immutable record identity linked to its parent, rather than mutating a pre-cutover record, and carries the same complete partition identity/digests.

`canonical_positive_writes=true` is valid only in the reviewed final promotion record after these gates and all upstream M04/M05/M06 evidence are complete. It is never inferred from a successful direct redacted append, a live status flag, a correlation ID, or a partial audit row.

## 9. Current implementation gap and unresolved risks

The inspected application surfaces do not yet implement the selected trusted boundary; Option D is a target contract, not an as-built guarantee:

- `AuditOutcome` requires tenant/app/version, event/request/trace/span/correlation and redacted MCP fields, but several M06 identity fields are optional and it has no `scope_kind`/`scope_id`. Event-class-specific requiredness, parent proof and a canonical full-identity digest are not enforced by this contract.
- `AuditOutcomeEvidence` currently checks only correlation equality. `AuditOutcomeAdapter._redacted()` derives the narrow projection, but the wrapper does not validate equality of result/error, timestamp or digest fields. The trusted transaction path must enforce those equalities; do not treat construction of the current wrapper alone as validation.
- `AuditOutcomeAdapter.append()` and `CanonicalGrantsPersistenceAdapter.append_audit_outcome()` still accept/append `RedactedAuditOutcome` separately. The persistence repository protocol permits independent promotion, quarantine or audit arguments and exposes no mandatory atomic transaction group. These are compatibility/as-built surfaces, not trusted M06 success paths; they must be classified and fenced before positive writes or promotion.
- `AuditOutcome` currently requires `decision=allow` to pair with `result_status=allow`. It therefore cannot represent an authorization allow followed by an invocation error in the same outcome while preserving both facts. Keep authorization and final execution as separately identified, linked events, or evolve the contract with explicit semantics before claiming complete result replay.
- The current adapter rejects quarantine outcomes from trusted evidence, so unresolved/quarantine evidence needs its own durable quarantine transaction path; it must not be silently dropped or represented as a positive trusted audit append.
- `RedactedAuditOutcome` carries MCP trace/span/profile/tool, argument digest, result/error, duration, timestamp and optional correlation only. It has no tenant/app/grant/runtime/mapping identity or event ID and cannot independently support trusted M06 replay, RLS ownership or idempotency.

This decision is aligned with:

- [`m06-trusted-persistence-adapter.md`](m06-trusted-persistence-adapter.md), especially the full identity envelope, trusted transaction, audit fields, RLS and positive-write gates;
- [`canonical-grant-target-ddl.md`](canonical-grant-target-ddl.md), especially append-only audit, digest-only MCP evidence, FK/RLS and retention behavior;
- [`m04-m06-adapter-implementation.md`](m04-m06-adapter-implementation.md), especially one-transaction grant/mapping/audit writes and fail-closed classification;
- [`m04-m06-readiness-matrix.md`](m04-m06-readiness-matrix.md) and [`migration-execution-backlog.md`](migration-execution-backlog.md), for bounded partitions, checkpoints and frozen acceptance checks;
- [`adapter-cross-domain-review.md`](adapter-cross-domain-review.md), F-07 and F-01, which identify separate evidence appends, incomplete identity and mutable promotion identity as blockers;
- [`persistence-target-map.md`](persistence-target-map.md), for audit separation from runtime rollback and parent/FK/RLS ordering;
- application contracts `audit_outcomes`, `audit_outcome_adapter`, `canonical_grants_promotion` and `canonical_grants_persistence_adapter` as inspected source acceptance surfaces.

Additional unresolved risks: the database shape and trusted transaction repository are not implemented; optional identity fields and missing scope/event-class validation in current contracts need closure; direct redacted append callers and the separate append protocol require explicit compatibility classification/fencing; authorization-versus-execution result modeling and durable quarantine transactions need implementation; exact retention/legal policy and archive format remain deployment decisions; promotion/persistence code must enforce atomic grouping and immutable lineage before positive writes or cutover. A normalized identity table remains optional and must not weaken the full-row contract.

For this documentation-only task, tests, backend/frontend tests, build, compile, lint, format, Alembic execution, migration execution, environment changes and git checks were intentionally not run. Only this document was changed. Code, ORM, core docs, shared environments and databases were not changed. Verification remains frozen until the migration is implemented, reviewed multiple times and the coordinator explicitly lifts the freeze.
