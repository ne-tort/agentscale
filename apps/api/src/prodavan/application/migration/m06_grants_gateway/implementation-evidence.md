# MIG-M06-02 implementation evidence

Task: `MIG-M06-02`
Status: `shadow-read/mapping/parity implementation authored; promotion blocked`

## Changed Paths

- `adapter.py` contains mapping, source digest/replay handling, exact binding/gates, and atomic redacted evidence contracts.
- `identity_adapter.py` composes the existing authenticated-company, M04, M04E, and M05 resolver facades.
- `legacy_decision_adapter.py` compares the real legacy plugin-gateway repository/scope/quota decisions without invoking an executor.
- `canonical_decision_adapter.py` is a read-only exact-binding canonical-grant projection.
- `__init__.py` exports the M06 application adapters.
- `decision_adapters.py` was removed because its synthetic decision contracts did not match the live canonical grant API.
- `tests/migration/m06_grants_gateway/acceptance_fixtures.py` and `test_acceptance_contract.py` hold source-faithful RLS/handoff fixtures and frozen behavior cases, including construction from live `AppGrant`/`CapabilityGrant` domain models.
- `tests/migration/m06_grants_gateway/partition-parity-fixtures.md` describes negative and parity cases.
- `promotion-record.md` records the blocked gate and no-cutover decision.
- `implementation-evidence.md` records these changes and remaining blockers.


- `adapter.py` now snapshots the real `AppGrant`/`CapabilityGrant` shape, preserves source digests and exact immutable identity bindings, validates operation/resource/domain constraints, quarantines unsupported semantics, detects changed-digest and tenant conflicts, and stages atomic redacted mapping/quarantine/shadow/audit transactions. Missing current RLS, M04/M04E/M05/M06 handoffs, exact identity, or persistence read/commit fails closed. Legacy remains the decision owner.
- `identity_adapter.py` composes the existing authenticated-company resolver, M04 active scope/RLS/membership resolver, M04E definition/version facade, and M05 exact active instance/runtime resolver. Source fields are assertions only; tenant/scope/definition/version/instance/subject/session/generation mismatches block.
- `legacy_decision_adapter.py` projects the live dynamic-plugin gateway checks through repository, scope and quota ports and never calls an executor.
- `canonical_decision_adapter.py` reads canonical grants by exact tenant/app/version/instance and checks complete binding, capability, operation/resource/domain, lifecycle and constraints. It never issues, revokes, mutates, or authorizes a canonical operation.
- `parity.py` remains a bounded deterministic comparison/report contract. Acceptance fixtures/tests cover unknown capability, unsupported method, missing/ambiguous instance, stale generation, allow/deny mismatch, changed digest, cross-tenant replay, expiry/revocation, and exact replay.

## Practical Integration Value

M06 now has an explicit application-level identity resolver and separate legacy/canonical decision projections that a future composition root can bind. It can produce deterministic shadow evidence only when trusted source, current identity, accepted dependency handoffs, and atomic evidence storage are all supplied. The legacy answer remains authoritative; no executor or canonical positive write path was introduced.

## Blockers

- M04/M04E/M05 accepted partition-bound handoffs are absent or blocked in current promotion records; M06 schema/writer and final-delta proof are not bound.
- `DependencyHandoffPort`, `EvidenceReadPort`, and `TrustedEvidenceTransactionPort` remain contracts; no production composition root, global source-key reader, durable atomic writer, evidence schema, uniqueness/RLS review, or append-only quarantine/audit adapter is supplied.
- No source snapshot or bounded tenant partition, accepted counts, parity observations, or observation window exists. No promotion/read cutover is supportable.
- The M06 directory is outside production gateway composition; no API route or runtime decision owner was changed.

## Promotion and Rollback

Promotion record remains blocked. `decision_owner=legacy`, `canonical_read_owner=legacy`, and `canonical_positive_writes=false`. No DDL, live data, environment, route, ORM model, external persistence adapter, cutover, commit, or push was changed/performed. With no target data or production wiring, rollback is a no-op; future partition failures must halt only that partition and preserve mapping/conflict/quarantine/audit/shadow evidence.

## Verification Freeze

Acceptance tests were authored but not run. Tests, build, compile, lint, format, and Git checks were not intentionally invoked. The file-editing tool automatically returned per-file lint feedback during edits; this unintended side effect is disclosed and is not treated as a formal gate. No test/build/compile/format/Git verification result is claimed.

## Recovery pass (MIG-M06-ADAPTER-FIX, 2026-11-04)

A previous agent was interrupted mid-write, leaving two modules syntactically broken:

- `adapter.py` had an unclosed parenthesis in the capability operation/resource/domain guard inside `CapabilityGatewayService.observe`, so the module could not be parsed or imported. The guard is restored as a single balanced condition; no surrounding logic was changed.
- `evidence_store.py` had a leftover `@dataclass` decorator above a bare `ObservationPage = ObservationBatch` alias. The alias is kept (the store returns the parity runner's batch contract) and the now-unused `dataclasses` import was dropped.

Current state, verified by read-only introspection only (`ast.parse` of every module, a package import, and the resolution of all 74 `__all__` names): every file parses, the package imports, and every exported name exists. `parity_runner.py` was extended in the same pass with two fail-closed traversal guards — a page that exceeds the requested limit, and a partition narrowed by `app_version_id`/`plugin_instance_id` that shadow evidence cannot prove — plus a page budget that bounds a source which never completes a truncated window.

New acceptance artifacts (authored, not executed): `test_evidence_store_contract.py` pins context usability, cursor parsing, audit redaction, reason-code mapping, the mapping correlation key and run-key replay/conflict; `test_parity_bounds_contract.py` pins the narrowing refusal, the page overshoot block, the page budget, byte-identical JSON export and blocked-report self-consistency.

Still blocked: no production composition root, no accepted partition-bound upstream handoff evidence, no reviewed RLS/append-only/uniqueness contract, and no durable production report registry (`InMemoryParityReportStore` is a test double that must never be composed in production). See `promotion-record.md`.

