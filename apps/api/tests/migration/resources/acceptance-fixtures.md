# MIG-RES-01 acceptance fixtures

Status: `authored / not executed`; baseline `2026-11-01`.

Executable fixtures are in `test_acceptance_fixtures.py`. Expected acceptance includes:

1. M04 resolver absent or not ready returns a denial before resource/binding reads.
2. Tenant, session, and project scope identities are typed; missing or cross-tenant context never resolves.
3. A positive result requires an active resource, active explicit binding, matching tenant/scope, authorized server-resolved context, and the exact pinned version.
4. Resolution requires one immutable trusted snapshot containing binding, resource, exact bound version, scope proof, and matching generation; independent reads, in-memory version selection, or a missing snapshot port fail closed before application.
5. A binding to one version cannot resolve a different version; appending a version does not mutate older versions.
6. Raw secret-like fields are rejected; secret material is supplied only by external reference.
7. Canonical import digest is recomputed from the complete source envelope, source identity, all resource versions, and binding; caller-supplied mismatch is quarantined before persistence.
8. Compatibility import is blocked by default and whenever the trusted atomic import port is absent. The port must perform identity+digest CAS and preserve the first winner/conflict in one transaction; no find-then-write path is accepted.
9. Archived, disabled, mismatched, missing, or unauthorized records deny resolution.

Fixtures were not run because the migration verification freeze prohibits tests, build, compilation, lint, formatting, and git checks. These are acceptance definitions, not evidence of execution or persistence guarantees.
