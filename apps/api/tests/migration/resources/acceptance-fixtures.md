# MIG-RES-01 acceptance fixtures

Status: `authored / not executed`; baseline `2026-11-02`.

Executable fixtures are in `test_acceptance_fixtures.py` (legacy contract) and `test_lifecycle_acceptance.py` (resource lifecycle acceptance). Expected acceptance includes:

1. M04 resolver absent or not ready returns a denial before resource/binding reads.
2. Tenant, company/cabinet, session, and project scope identities are typed; missing or cross-tenant context never resolves.
3. A positive result requires an active resource, active explicit binding, matching tenant/target scope, authorized server context, published exact pinned version, and one immutable snapshot.
4. Prompt/file/MCP versions are immutable; content and asset digests are recomputed from canonical payloads. Appending/publishing a version never mutates historical payloads.
5. FileRef requires immutable `asset_id` + `version_id` + `sha256` digest; a declared asset digest conflict quarantines before persistence.
6. Raw secret-like fields are rejected; MCP env/authorization values may contain only external references. Unsupported MCP manifests quarantine.
7. `project_ids` and selection policy are explicit. `null`/missing policy is ambiguous; duplicate project bindings and cross-project assertions quarantine.
8. Profile selection is read-only and deterministic: explicit projects outrank all-bound candidates, then priority and stable identity tie-break; no implicit latest/default selection.
9. Canonical import digest includes source envelope, source identity/schema, all immutable versions/assets and binding policy. Caller mismatch is quarantined before persistence.
10. Compatibility import is blocked by default and whenever the trusted atomic import port is absent. The port performs identity+digest CAS and preserves the first winner on changed-digest conflict.
11. Resumable import pages are sorted and bounded; row evidence and checkpoint advance are one port operation. Exact replay returns the original identity, changed digest remains conflict/quarantine, and completion is fenced.
12. Read-only resolution returns exact resource/version/binding scope proof plus immutable provenance. Archived, disabled, mismatched, missing, or unauthorized records deny resolution.

Fixtures were not run because the migration verification freeze prohibits tests, build, compilation, lint, formatting, and git checks. These are acceptance definitions and authored tests, not execution or persistence evidence.
