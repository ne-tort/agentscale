"""Release gate and concrete blockers for upstream owner/scope proof."""

# Flip only after independently reviewed M04/M04E proof is registered for a partition.
UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED = False

UPSTREAM_BLOCKERS = (
    "MIG-M04-01 owner-registry promotion evidence is blocked or absent",
    "MIG-M04-02 typed-scope promotion evidence is blocked or absent",
    "MIG-M04E-01 definition/version foundation promotion evidence is blocked or absent",
    "no source-backed bounded definition/version snapshot is supplied",
    "no trusted atomic mapping, quarantine, audit, and checkpoint-CAS adapter is bound",
    "no reviewed database-side immutable-artifact enforcement is evidenced",
    "no active typed-parent proof adapter is bound for this partition",
    "no production artifact content adapter is bound to the M04 content store",
)


def upstream_blockers() -> tuple[str, ...]:
    """Return blockers while the static release gate remains closed."""
    return UPSTREAM_BLOCKERS if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED else ()
