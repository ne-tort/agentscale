# MIG-RES-01 application lifecycle promotion record

Record ID: `MIG-RES-01:blocked:application-lifecycle-v2:2026-11-02`
Supersedes: `MIG-RES-01:blocked:preflight:2026-11-01`
Status: `blocked` for promotion/import execution; `implementation-ready-for-review` for the application lifecycle and acceptance artifacts.
Partition: `all resource scopes / none admitted`
Decision: `retain legacy owner; do not promote, run import, enable cutover, or perform environment writes`

## Implemented application deliverables

- `lifecycle_contracts.py` defines immutable prompt/file/MCP resource roots, append-only version history, publication lifecycle, immutable Content Service asset references (`asset_id`, `version_id`, `sha256`/asset digest), typed owner/target scopes, explicit project binding policy, session/project proof, provenance, resumable checkpoints and trusted read/import ports.
- `lifecycle_validation.py` recomputes canonical source/resource/version/binding digests, rejects plaintext secret material, validates safe workspace paths and FileRef integrity, and quarantines unsupported MCP manifests or digest conflicts.
- `lifecycle_algorithms.py` implements bounded sorted import pages, source identity + digest conflict classification, checkpoint CAS/resume/replay behavior, cross-tenant/scope proof admission, deterministic profile selection, and one-snapshot read-only exact-version resolution with provenance.
- `resources.py` and package `__init__.py` expose the lifecycle as the sole resource application surface while retaining the historical import path.
- Acceptance fixtures/tests cover asset digest immutability, secret/MCP rejection, duplicate bindings, cross-tenant quarantine, explicit-vs-all-bound profile selection, deterministic tie-breaks, idempotent import replay, changed-digest conflicts, resumable checkpoints, exact snapshot resolution, provenance, lifecycle publication and archived/disabled denial.

## Evidence and gate

- Source snapshot/schema/digest: `not acquired / not inventoried / unavailable`; no source rows were read.
- Target persistence/RLS/FK/unique evidence: `not provided`; no ORM, DDL, Alembic, route, object-store or runtime adapter was added.
- Import and snapshot algorithms require trusted ports. No concrete adapter exists in this zone, so no source row was imported and no positive resolver was enabled.
- Scope dependency: M04 typed scope and transaction-local tenant proof remain an upstream gate. Missing or mismatched proof returns blocked/quarantined outcomes; caller tenant/project assertions never authorize access.
- Cross-tenant, ambiguous `project_ids`/selection policy, duplicate binding, changed source digest, asset digest mismatch, secret material and unsupported MCP manifest are evidence-bearing deny/quarantine outcomes and cannot enter positive state.
- Profile selection is deterministic only after the read-only profile port returns server candidates. Explicit project binding outranks all-bound binding; no implicit latest/default profile is selected.
- Acceptance tests are authored and intentionally not executed under the verification freeze.

## Promotion gate

No promotion is authorized. A future partition must supply accepted M04 scope proof, source inventory/schema/digest, durable immutable asset/version and binding persistence, unique identity + digest CAS, checkpoint fencing, quarantine/audit retention, exact tenant-scoped snapshot resolution, and reviewed RLS/privileges. The trusted adapter must atomically preserve the first mapping on a changed-digest conflict and must return one consistent binding/resource/version/proof/generation snapshot. Positive project/profile resolution requires explicit binding policy and no unresolved quarantine. The coordinator must explicitly release the verification freeze before executing authored tests or formal checks.

## Rollback

No target data was imported and no environment changed; rollback is a no-op. Keep legacy readers/writers and source assets authoritative. If a bounded partition is later enabled and fails, stop only that partition, retain source mappings, immutable versions/assets, quarantine, audit and checkpoints, disable affected bindings, and repair forward with a new superseding record. Never rewrite historical versions, silently rebind a resource to another project, or delete evidence to hide a digest conflict.

This record is implementation evidence, not production promotion, schema readiness, import completion, authorization eligibility, or cutover approval. Corrections require a linked superseding immutable record.

Verification freeze confirmed: tests, builds, compilation, lint/format commands, git checks, DDL/Alembic, live data writes, cutover, environment changes, commit and push were intentionally not performed.
