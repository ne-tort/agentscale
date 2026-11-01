# MIG-M06-02 bounded partition parity fixtures

Acceptance artifacts only; deliberately not executed during migration verification freeze.

- Partition is exactly `(tenant_id, app_definition_id)` and a report rejects mixed-partition observations.
- At most 1,000 observations per report call by default (hard cap 10,000); a larger partition is continued with a stable checkpoint rather than loaded unboundedly.
- Exact replay of `(source_kind, source_id, schema_version, source_digest)` is idempotent for mapping identity and may append a fresh shadow observation; same identity/schema with changed source digest is conflict/quarantine and cannot overwrite the first mapping.
- Multiple source entries with the same capability key are ambiguous and quarantine; the adapter never selects the first match.
- Dependency gate evidence must be a well-formed SHA-256 digest for every upstream slice; arbitrary non-empty markers do not satisfy a handoff.
- Source, canonical payload and binding digests use stable SHA-256 canonical JSON; arrays are sorted and duplicate-free. Binding digest includes tenant, definition/version, scope, subject/session, exact instance, artifact and runtime/writer generations.
- Missing M04, M04.e, M05, M06 schema or trusted writer evidence blocks capture. Missing exact instance, session, subject, active scope, or current runtime/writer generation is unresolved positive binding, never positive parity.
- `expires_at <= issued_at` is rejected. At fixed timezone-aware `checked_at`, `issued_at > checked_at` and `expires_at <= checked_at` are quarantined as explicit source-future/source-expired outcomes before identity resolution, never mapped positive evidence.
- Trusted persistence exposes one `commit_evidence` transaction operation: mapping, quarantine, shadow and audit for a decision commit all-or-none, with append-only identities and unique source keys. Audit/persistence failure rolls back the complete evidence unit; no sequence of required write ports is acceptable.
- Compare normalized legacy and canonical shadow decisions. Expired, revoked and explicitly denied entries are known deny classes; unexplained allow/deny mismatch blocks the partition. Quarantine explanations remain visible and do not count as positive parity.
- Report includes source snapshot digest, source/canonical/binding aggregate digests, observation window, mismatch counts, unknown/unresolved counts, audit completeness, writer epoch and final-delta checkpoint.
- `canonical_read_owner=legacy` and `canonical_positive_writes=false` are invariant in this stage; a `ready_for_review` status is not a cutover authorization.
- Negative cases: unknown capability; unsupported method/constraint; missing/ambiguous instance; cross-tenant replay; stale runtime/writer generation; expired/revoked grant; audit failure; no final delta; absent dependency digest; legacy/canonical decision mismatch.
