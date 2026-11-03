"""MIG-PROJECT-RUNTIME-RECONCILE-01 authored acceptance gates; execution frozen."""

ACCEPTANCE_SCENARIOS = (
    "deterministic immutable DesiredRuntimeSnapshot binds exact project revision, module/provider bindings, config provenance, workspace and image digest",
    "missing project scope, version evidence, complete materialization, or immutable image blocks closed",
    "observed Pod owner/UID, runtime revision, config, workspace generation/digest, and runtime image drive noop/apply/replace/quarantine",
    "CAS generation, idempotency, exact project scope, plan digest, and fencing token bind every positive runtime receipt",
    "health mismatch never advances last-known-good; rollback uses the previous verified snapshot under the active fence",
    "foreign or ambiguous Pod ownership is quarantined only through an exact-scope CAS/fenced containment receipt",
    "idempotent replay returns only when the original claim and full plan digest match; otherwise it is blocked",
    "hydrate_generation remains distinct from runtime_materialization generation and both are observed explicitly",
    "ProjectRuntimeRevision and runtime materialization are read-only source contracts with no database or Kubernetes adapter",
)

VERIFICATION_STATUS = "not_run_by_explicit_migration_freeze"
