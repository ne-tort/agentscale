# Shared durable identity envelope

Статус: migration-stage source contract, baseline 2026-11-01. Документ фиксирует
общий immutable identity handoff для T01-T08 и M04-M06. Он не является ORM,
Alembic revision, gateway implementation или разрешением positive canonical writes.
Все persistence, database, build, lint, compile и git checks для этой задачи
намеренно не выполняются.

## 1. Purpose and ownership

Один `IdentityEnvelope` переносит доказанную identity snapshot через owner/scope
registry (M04), plugin instance/runtime fencing (M05), canonical grants и
promotion (M06), а также через T01-T08 transport/runtime boundaries. Downstream
adapter обязан использовать тот же digest-linked snapshot, а не повторно выводить
scope из caller claims, JSONB или неполного plugin id.

Envelope является **identity evidence**, не authorization decision. Наличие всех
полей не означает allow: active registry row, parent `EXISTS`, lifecycle,
capability, RLS, audit и promotion gates остаются отдельными проверками.

Canonical tenant vocabulary — `tenant_id`, физический anchor в текущем migration
stage — `companies.id`. `company_id` может быть только compatibility/provenance
input и никогда не заменяет `tenant_id` в envelope.

## 2. Canonical envelope

`IdentityEnvelope` — frozen, slot-based value object. Его canonical JSON payload
сериализуется с сортировкой ключей, компактными separators и UTF-8/ASCII-safe
encoding. `envelope_digest = sha256(canonical_payload)` с префиксом `sha256:`.
Digest покрывает identity, scope anchors, runtime fencing, lease, correlation,
trace и promotion partition. Сам digest в payload не включается; supplied `promotion_partition` при расхождении с derived partition отклоняется (quarantine boundary), а не переписывается.

| Field | Required rule | Meaning |
|---|---|---|
| `tenant_id` | non-empty canonical company identity | tenant policy/RLS anchor |
| `cabinet_id` | optional by scope shape | typed cabinet parent |
| `project_id` | optional; requires cabinet | typed project parent |
| `scope_kind` | `tenant \| cabinet \| project \| plugin_instance` | non-polymorphic scope shape |
| `scope_id` | non-empty; exact typed id | selected scope anchor |
| `plugin_instance_id` | required exact installation | never inferred from app/version |
| `definition_id` | required | app definition identity |
| `version_id` | required same-definition version | version identity |
| `artifact_digest` | immutable `sha256:` digest | executable/materialized artifact |
| `subject_id` | required | authenticated subject/employee binding |
| `session_id` | required | agent/runtime session binding |
| `runtime_generation` | non-negative monotonic epoch | runtime admission fence |
| `writer_generation` | non-negative monotonic epoch | mutation/CAS fence |
| `lease_id` | required | lease identity, not a secret |
| `lease_owner` | required | worker/principal holding lease |
| `lease_until` | timezone-aware | lease expiry snapshot |
| `correlation_id` | required | durable operation correlation |
| `trace_id` | required | distributed trace identity |
| `promotion_partition` | derived complete partition | M06 bounded promotion key |
| `envelope_digest` | derived immutable digest | replay/tamper identity |

For `tenant` scope, cabinet/project anchors are null. For `cabinet`, `scope_id`
equals `cabinet_id`; for `project`, `scope_id` equals `project_id` and
`cabinet_id` is required. For `plugin_instance`, `scope_id` equals
`plugin_instance_id`; installation cabinet/project anchors are retained when
present. A project anchor without a cabinet is invalid. An installation scope
change creates a new forward identity; it does not rewrite old evidence.

`PromotionPartition` repeats the minimum exact identity required to bound M06
promotion: tenant, definition, version, scope, plugin instance, subject and
session. It is compared to the envelope at construction; no wildcard or
instance-independent fallback is allowed.

## 3. Immutability and digest rules

- Contracts are frozen. Any change creates a new envelope and a new digest.
- A receiver may recompute and compare the digest before any positive operation.
- The source/canonical payload digest conflict is quarantine, never overwrite.
- Runtime/writer generations and lease identity are part of the digest. A stale
  generation or expired lease cannot be repaired by copying a newer row after the
  decision; the operation must be retried with a newly resolved envelope.
- `with_propagation_ids()` intentionally creates a new snapshot when a boundary
  starts a new span. It does not mutate the prior evidence.
- No secret, raw token, lease credential, prompt, or arbitrary mutable mapping is
  included in the envelope or propagation headers.

## 4. Quarantine classification

`QuarantineEvidence` is redacted, retryable evidence tied to the complete envelope.
It contains classification, stable source identity/digest, reason, checkpoint,
observation time and string-only redacted metadata. Quarantine can be counted,
reviewed and retried, but it can never satisfy registry resolution or authorize a
positive write.

Canonical classifications are:

- `malformed`, `unsupported`: shape/schema cannot be represented;
- `orphan`, `ambiguous`: missing or non-unique parent/definition/version;
- `cross_tenant`, `cross_scope`, `cross_definition`: typed ownership mismatch;
- `artifact_mismatch`: version and immutable artifact disagree;
- `subject_mismatch`, `session_mismatch`: caller/runtime binding differs;
- `stale_runtime`, `stale_writer`, `lease_invalid`: fencing/lease failure;
- `digest_conflict`: same source identity observed with another payload;
- all other cases remain deny/quarantine until an explicit classification exists.

Unknown required parent mappings block the affected tenant/definition promotion
partition. Quarantine rows are durable evidence and are not authorization query
inputs. A quarantine append or audit failure must fail the enclosing positive
operation rather than produce an allow.

## 5. Context propagation

`IdentityContext` wraps the envelope with an explicit adapter `hop` and optional
string-only baggage. `IdentityPropagationPort` has `inject`/`extract` operations,
but implementations must preserve the original digest and reject missing,
malformed or conflicting headers.

The transport allow-list is:

```text
x-prodavan-identity-digest
x-prodavan-correlation-id
x-prodavan-trace-id
x-prodavan-identity-hop
```

Lease owner, lease id, artifact bytes, credentials and arbitrary request claims
must not be propagated as authority. Headers are correlation/evidence transport;
the receiving boundary still resolves authenticated server context, compares any
client assertions, validates the digest and sets transaction-local context only
from the trusted envelope. Missing tenant context is deny, not platform-wide
fallback.

`IdentityContextPort` represents the transaction boundary: set context from a
resolved envelope before repository work and clear it at transaction end. It does
not replace RLS, parent compatibility checks, runtime admission or capability
checks.

## 6. M04 -> M05 -> M06 handoff

1. **M04** resolves tenant and typed scope, proving cabinet/project parent
   compatibility and definition ownership. It emits an envelope only for a
   deterministic candidate; ambiguous/orphan/cross-tenant rows emit quarantine.
2. **M05** adds exact plugin instance, same-definition version, artifact digest,
   subject/session, runtime/writer generations and lease snapshot. CAS predicates
   must check the complete identity, not only `plugin_instance_id`.
3. **M06** carries the unchanged envelope into grant mapping, authorization,
   audit and promotion. Promotion records bind their source/canonical/binding
   evidence to `envelope_digest`, correlation and trace IDs. A narrower partition
   cannot drop definition, artifact, subject, session or fencing fields.
4. **T01-T08** may serialize or transport the context, but cannot broaden scope,
   substitute a caller-provided tenant, select another instance, or manufacture
   positive evidence. Boundary adapters must fail closed and classify the reason.

The canonical positive-write transaction remains responsible for lock/CAS,
registry resolution, definition/version proof, artifact check, exact instance,
subject/session and lease checks, mapping idempotency, audit append and RLS
`USING`/`WITH CHECK`. The envelope only makes the shared proof explicit and
replayable.

## 7. Ports and source contract

The new exclusive application zone exposes only four protocol boundaries:

- `IdentityEnvelopeResolverPort`: server-authenticated resolution;
- `IdentityPropagationPort`: digest-linked inject/extract;
- `IdentityQuarantinePort`: durable redacted append/count;
- `IdentityContextPort`: transaction-local set/clear.

Implementations belong to later migration slices. No ORM, persistence model,
Alembic, gateway, core or existing application zone is changed by this contract.

## 8. Acceptance criteria and unresolved risks

Source-level acceptance tests cover frozen snapshots, canonical digest and
 tamper rejection, typed plugin scope, propagation allow-list and typed redacted
quarantine. They are intentionally not executed during migration verification
freeze.

Remaining risks/blockers:

- Existing M04/M05/M06 zones still need convergence on this exact envelope;
  current adapters may omit anchors, correlation, lease or durable classification.
- Database composite keys, RLS policies, append-only evidence and atomic
  multi-record persistence are not implemented here.
- Existing runtime and promotion implementations must reject stale or conflicting
  digest evidence before any positive cutover; this source contract alone does
  not authorize canonical writes.
- Header extraction and cryptographic/trust-boundary policy require a later
  transport implementation review; untrusted headers are assertions only.

Verification freeze: tests, build, lint, formatting, compile, Alembic, database,
environment and all git checks were intentionally not run.
