"""R-06 acceptance fixtures; execution is deferred by migration verification freeze."""

# Cases are declarative so a later owner can bind them to the repository's test harness.
ACCEPTANCE_CASES = (
    "full_selection_digest_and_generation_are_immutable",
    "optional_failure_blocks_and_clears_all_operations",
    "apply_without_persistence_adapter_is_blocked",
    "atomic_apply_checks_generation_snapshot_and_complete_operation_set",
    "partial_failure_never_reports_ready",
)
