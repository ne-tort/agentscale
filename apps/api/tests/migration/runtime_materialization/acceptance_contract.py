"""MIG-RES-02 acceptance specification; execution remains frozen."""

# State-machine fixtures live in test_state_machine.py. SQL adapter fixtures
# live in test_sqlalchemy_repository.py. This file keeps the migration gate
# explicit for reviewers and promotion tooling.

ACCEPTANCE_SCENARIOS = (
    "immutable desired revision and canonical provenance digest",
    "deterministic plan and ownership manifest digest",
    "prepare/validate/apply/replay with no-op and idempotency-key conflict",
    "required-operation failure barrier, optional diagnostic, compensation and retry state",
    "atomic generation CAS with stale fence rejection",
    "bridge lease expiry/revocation, projected secret refs and immutable image digest",
    "redacted audit failure denies positive acknowledgement",
    "approved rollback creates a new monotonic generation and preserves history",
    "SQLAlchemy revision and materialization persistence round-trip",
    "exact idempotent plan replay and changed-plan conflict",
    "stale entry generation CAS returns zero affected rows",
    "same-session audit write failure rolls back revision and entry changes",
    "partial materialization persists failed and never ready",
    "missing lease, projected-secret or immutable-image evidence blocks plan claim",
)

VERIFICATION_STATUS = "not_run_by_migration_freeze"

