# MIG-M04-02 blocked acceptance artifacts

Status: `blocked` (fail-closed), baseline `2026-11-01`. Application contract and acceptance fixtures are authored; scenarios are not executed under migration verification freeze.

This artifact records acceptance coverage for the dependency-blocked partition. It is not executable proof that the scope registry is implemented or that any source row is accepted. No backfill, active resolver, plugin-instance resolution, or positive path may be inferred from this file.

## Dependency gate

| Dependency | Required evidence | Observed state | Result |
|---|---|---|---|
| MIG-M04-01 | Accepted canonical tenant type and retained typed anchors, compatible parent keys/deletion boundary | Owner-registry foundation exists and was static-reviewed; DB registration/revision/RLS readiness remains unverified | Partial; stop before backfill/resolution |
| Active membership/access proof | Source-backed active `cabinet_company_grants(cabinet_id, company_id)` or separately approved owner relation, plus project tuple compatibility | Contract documents the relation but no partition/source evidence is present | Missing; stop |
| Transaction-local tenant context | Trusted transaction sets valid `app.tenant_id` and optional cabinet/project context; absent/malformed context denies | No implementation evidence available in this task zone | Missing; stop |
| RLS review | ENABLE/FORCE, USING/WITH CHECK, migration/security-only unresolved evidence | No target schema/policy evidence available in this task zone | Missing; stop |

## Acceptance scenarios (specified, not run)

1. Same `(source_kind, source_id, schema_version, digest)` replay returns the original mapping/checkpoint without creating a second registry row or audit event with a different outcome.
2. Same source identity and schema version with changed digest is conflict/quarantine; existing mapping and registry identity are not overwritten.
3. Missing or deleted tenant/cabinet/project parent is quarantined with a stable reason code and partial resolved anchors only; it never resolves active.
4. Ambiguous cabinet/company membership is quarantined; nullable mirrored cabinet company fields are not proof.
5. Project company/cabinet tuple mismatch is quarantined even if each simple FK exists.
6. Cross-tenant relation is quarantined and produces redacted security audit evidence.
7. Archived and quarantined rows are excluded from `ResolveScopePort` positive results; resolution additionally requires exactly one active exact-anchor row.
8. Missing or malformed transaction-local tenant context denies resolution; caller-provided tenant/scope assertions cannot establish authority.
9. `plugin_instance` returns deny/deferred regardless of candidate row until M05 exact identity and reciprocal registry relation are validated.
10. A successful active result requires exactly one typed row, retained tenant, parent compatibility, active membership proof, valid typed shape and reviewed RLS gate.

## Safety assertions

- No request-based ownership inference, guessing, polymorphic `scope_id` FK, overwrite-on-conflict, or audit of raw credentials/arguments/secrets.
- Quarantine and audit are evidence only, inaccessible to ordinary authorization reads.
- Legacy remains the authoritative read/write owner while this task is blocked.
- These assertions are acceptance definitions, not claims of implementation or executed verification.
