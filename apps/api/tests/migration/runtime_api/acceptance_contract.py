"""MIG-RUNTIME-API-01 acceptance inventory; execution remains frozen."""

ACCEPTANCE_SCENARIOS = (
    "observed project and Pod routes are not treated as runtime CAS executors",
    "exact project/company/cabinet/Pod/resource scope and desired revision/generation are pinned",
    "immutable runtime image digest matches desired revision and trusted binding",
    "bridge lease reference, project, Pod, generation, expiry, and revocation are rechecked",
    "trusted evidence and typed state-machine validation fail closed",
    "state-machine outcomes map one-to-one; failure keeps safe operation ID and required status",
    "atomic executor receipt binds scope, desired revision, expected/next generation, plan, image, lease, and ownership",
    "APPLIED, ROLLED_BACK, REPLAYED, and FAILED require a typed atomic executor receipt",
    "atomic replay lookup by idempotency key and plan digest precedes stale-generation rejection",
    "a durable replay result still requires current exact scope, image, and live bridge evidence",
    "no-op does not bump generation; failed desired revision prepares a deterministic retry",
    "stale generation, reused idempotency key, and mismatched receipt never return applied",
    "unknown operation effect requires reconciliation and is not reported as applied or quarantined",
    "runtime materialization cannot claim quarantine or promotion",
    "diagnostics expose only allow-listed codes and sanitized operation identifiers",
)

PROMOTION_RECORD = "promotion_record.md"
VERIFICATION_STATUS = "not_run_by_migration_freeze"
PROMOTION_STATUS = "blocked_until_atomic_executor_and_independent_review"
