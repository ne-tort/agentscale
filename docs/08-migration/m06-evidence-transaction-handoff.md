# M06 evidence transaction handoff

Status: migration-stage implementation contract, 2026-11-01. This handoff adds an exclusive application boundary only. It does not add DB/ORM/Alembic, routes, or a persistence implementation.

## Scope

`apps/api/src/prodavan/application/m06_evidence_transaction/` owns the trusted in-memory contract for evidence/promotion/audit atomicity. The package is intentionally separate from compatibility surfaces that accept a narrow `RedactedAuditOutcome`.

The trusted input is one immutable `AuditOutcomeEvidenceV2`. It carries the unchanged shared `IdentityEnvelope` (including its digest and promotion partition) rather than reconstructing tenant, scope, definition/version, plugin instance, subject/session, fencing, lease, correlation, or trace identity locally. It contains:

- full event and tenant identity, decision, capability identity and canonical identity digest;
- promotion lineage (parent record, source snapshot, source/canonical/binding digests and lineage reference);
- explicit quarantine/allow/deny result, with an error code retained for quarantine and deny;
- redacted MCP projection containing only digests and non-secret metadata;
- bundle snapshot identity/schema/digest;
- append-only audit identity/type/time/digest;
- redacted JSON metadata; nested raw arguments, payloads, secrets, tokens and credentials are forbidden.

The envelope rejects mismatched event IDs, shared-identity tenant/digest/correlation/trace/timestamps, promotion source snapshot versus bundle snapshot/digest, redacted result versus outcome, and quarantine evidence that could be represented as allow. Decision fields are typed allow/deny/quarantine values, so arbitrary strings cannot bypass the result contract. Raw arguments and secrets, including nested metadata keys, are explicitly rejected by the service.

## Exclusive transaction rule

`EvidenceTransactionPort.commit_evidence_transaction(evidence, context)` is the only trusted write seam. `M06EvidenceTransactionService.record(...)` validates the complete envelope and makes exactly one call. The implementation must commit all of the following in one transaction or append nothing:

1. the full `AuditOutcomeEvidenceV2`;
2. promotion lineage and immutable parent linkage;
3. quarantine/allow/deny result;
4. redacted projection;
5. bundle snapshot;
6. append-only audit event.

There are deliberately no `append_audit`, `append_promotion`, `append_quarantine`, or independent partial-write methods in this package. A persistence adapter must not expose partial append behind this port. Any audit, mapping, promotion, snapshot, or quarantine failure rolls back the whole positive operation and returns a deny/system failure. Append-only correction is a new forward event, never an update or delete.

The transaction context carries server-resolved tenant, actor, request and writer generation. The service requires tenant and writer-generation agreement before commit; callers cannot use a request assertion to broaden identity. Database RLS/FK/append-only enforcement remains a later persistence concern, not implied by this contract.

## Integration and migration notes

- Existing legacy/narrow audit append ports remain compatibility-only and must not be used as M06 promotion evidence.
- An adapter implementing this port must perform writer-generation CAS, exact scope/definition/version/instance/subject/session checks, digest/idempotency checks and append-only audit insertion inside the same DB transaction when persistence is introduced.
- Quarantine is durable evidence, not a dropped error and not positive parity/authorization evidence.
- Promotion finalization must create a new immutable record linked to its parent and carry the same complete partition identity/digests.
- No route, ORM model, migration revision, or shared-environment write is part of this handoff.

## Acceptance tests

`apps/api/tests/unit/test_m06_evidence_transaction.py` documents the frozen acceptance checks: one atomic port call, no partial append API, complete bundle presence, lineage and snapshot linkage, quarantine preservation, tenant fail-closed behavior, digest-only identity, and inconsistent envelope rejection. Per migration protocol, formal tests/build/lint/format/compile/git checks were intentionally not run.
