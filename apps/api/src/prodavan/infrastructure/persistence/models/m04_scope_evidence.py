"""M04-02 accepted scope/RLS promotion evidence (task MIG-DDL-M04EVID-01).

Why this table exists
---------------------
``application/migration/evidence_ports.py`` declares
``m04_scope_promotion_evidence_missing`` in :data:`DECLARED_READ_BLOCKERS`, so
``SqlPartitionHandoffReadStore.load_partition`` can never return accepted evidence: no
registered table stored the *accepted* M04-02 scope row together with its RLS and gate
proofs. Without it the M04 -> M04E -> M05 -> M06 chain stops at its first link and the
cutover is unreachable. This module supplies that artefact.

What a row is (and is not)
--------------------------
A row is the redacted, append-only record that one ``(tenant_id, scope_kind, scope_id)``
partition passed the M04-02 resolution gates: a typed active scope-registry entry, the
same-transaction parent-cardinality proof, the reviewed RLS proof and the actor
membership proof (see ``application/migration/m04_scope/scope_migration.py``:
``ScopeResolutionEvidence``, ``ScopeParentEvidence``, ``ScopeRlsEvidence``,
``ScopeActorMembershipEvidence``). Only identifiers, digests, counts, gate names and
redacted reason codes are stored; source payloads, manifests, tokens and raw request
material never reach a column (``gate_evidence`` is a JSONB envelope of *derived*
evidence, and its digest is the row's ``gate_evidence_digest``).

A row is never itself an authorization decision. ``canonical_positive_writes`` is pinned
to ``false`` by a database check, exactly like the M04E/M05/M06 promotion tables: the
legacy owner stays authoritative and this table only stops the read port from refusing
on a *missing* artefact.

Deliberate difference from the older promotion tables
-----------------------------------------------------
``m04e_definition_version_promotion_evidence`` and
``m05_plugin_instance_promotion_evidence`` constrain ``outcome`` to
``('blocked','quarantined')`` and keep the true handoff status inside the redacted
``gate_evidence`` envelope (blocker ``promotion_outcome_cannot_express_ready``). That
limit exists because those tables are historical and rewriting an applied CHECK is a
data migration, not a schema fix. ``m06_canonical_grant_promotion_evidence`` already
admits ``('blocked','quarantined','ready','promotable')``; this table follows that
precedent with the three M04 states, so a reader can select ``outcome = 'ready'``
without decoding an envelope. The two older tables are left untouched.

Scope kinds
-----------
Only the three typed owner scopes are admissible. ``plugin_instance`` scope is refused
both by the M04-02 contract (``ScopeParentEvidence.invalid_reason_for`` returns
``plugin_instance_scope_denied``) and by this table's check constraints, so a
plugin-instance row can never claim scope-evidence for a partition it does not own.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    inspect as sa_inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


#: This is the only M04 promotion-evidence table that may record an accepted status.
#: The two oldest tables keep ``('blocked', 'quarantined')`` on purpose (see module
#: notes); M06 already admits ``ready``/``promotable``.
_PROMOTION_STATES = "('blocked', 'quarantined', 'ready')"
_SCOPE_KINDS = "('tenant', 'cabinet', 'project')"

#: Every column is append-only: an accepted evidence row is never rewritten.
#: The guard below rejects an ORM UPDATE that changes any of these fields, and the
#: database-side append-only privilege gate remains a separate requirement.
_APPEND_ONLY_FIELDS = (
    "id",
    "tenant_id",
    "scope_kind",
    "scope_id",
    "cabinet_id",
    "project_id",
    "scope_registry_id",
    "revision_id",
    "previous_revision_id",
    "outcome",
    "source_snapshot_id",
    "source_snapshot_digest",
    "canonical_digest",
    "canonical_schema_version",
    "checkpoint_digest",
    "gate_evidence_digest",
    "rls_policy_digest",
    "rls_security_review_id",
    "rls_database_role",
    "gate_evidence",
    "reason_code",
    "writer_generation",
    "correlation_id",
    "actor_id",
    "canonical_positive_writes",
    "recorded_at",
)


class M04ScopePromotionEvidenceRow(Base):
    """Immutable accepted M04-02 scope/RLS evidence row for one bounded partition.

    The partition key is ``(tenant_id, scope_kind, scope_id)``. ``revision_id`` is the
    append-only identity inside that partition (``uq_m04_scope_promotion_revision``), so
    replaying the same evidence key is a conflict rather than a second row.
    """

    __tablename__ = "m04_scope_promotion_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scope_kind",
            "scope_id",
            "revision_id",
            name="uq_m04_scope_promotion_revision",
        ),
        ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_m04_scope_promotion_registry",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"outcome IN {_PROMOTION_STATES}", name="ck_m04_scope_promotion_outcome"),
        CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_m04_scope_promotion_scope_kind"),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id "
            "AND cabinet_id IS NULL AND project_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND scope_id = cabinet_id "
            "AND project_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
            "AND cabinet_id IS NOT NULL)",
            name="ck_m04_scope_promotion_typed_shape",
        ),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0", name="ck_m04_scope_promotion_tenant_nonempty"
        ),
        CheckConstraint(
            "length(btrim(scope_id)) > 0", name="ck_m04_scope_promotion_scope_id_nonempty"
        ),
        CheckConstraint(
            "length(btrim(source_snapshot_id)) > 0",
            name="ck_m04_scope_promotion_source_snapshot_nonempty",
        ),
        CheckConstraint(
            "length(btrim(canonical_schema_version)) > 0",
            name="ck_m04_scope_promotion_canonical_schema_nonempty",
        ),
        CheckConstraint(
            "source_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_source_sha256",
        ),
        CheckConstraint(
            "canonical_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_canonical_sha256",
        ),
        CheckConstraint(
            "checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_checkpoint_sha256",
        ),
        CheckConstraint(
            "gate_evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_gate_sha256",
        ),
        CheckConstraint(
            "rls_policy_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04_scope_promotion_rls_sha256",
        ),
        CheckConstraint(
            "length(btrim(rls_security_review_id)) > 0 AND length(btrim(rls_database_role)) > 0",
            name="ck_m04_scope_promotion_rls_review_nonempty",
        ),
        CheckConstraint(
            "jsonb_typeof(gate_evidence) = 'object'",
            name="ck_m04_scope_promotion_gate_evidence_object",
        ),
        CheckConstraint(
            "length(btrim(correlation_id)) > 0",
            name="ck_m04_scope_promotion_correlation_nonempty",
        ),
        CheckConstraint(
            "writer_generation >= 0", name="ck_m04_scope_promotion_writer_generation_nonnegative"
        ),
        CheckConstraint(
            "canonical_positive_writes = false", name="ck_m04_scope_promotion_no_positive_writes"
        ),
        # A READY row is the only row a partition read may consume, so it must name the
        # actor whose membership was proven in the same transaction.
        CheckConstraint(
            "outcome <> 'ready' OR (actor_id IS NOT NULL AND length(btrim(actor_id)) > 0)",
            name="ck_m04_scope_promotion_ready_requires_actor",
        ),
        # A non-READY row is evidence of a refusal and must say why, with a redacted
        # reason code rather than a payload excerpt.
        CheckConstraint(
            "outcome = 'ready' OR (reason_code IS NOT NULL AND length(btrim(reason_code)) > 0)",
            name="ck_m04_scope_promotion_non_ready_requires_reason",
        ),
        Index(
            "ix_m04_scope_promotion_partition",
            "tenant_id",
            "scope_kind",
            "scope_id",
            "recorded_at",
        ),
        Index("ix_m04_scope_promotion_recorded_at", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Existence of the accepted registry row. Tenant equality is *not* proven by this
    # single-column reference: scope_registry exposes no (id, tenant_id) unique key, and
    # adding one is a registry-revision change outside this task's file zone. The
    # trusted writer plus the tenant-scoped RLS policy are the enforcement today; the
    # residual gap is named in M04_SCOPE_EVIDENCE_DEFERRED.
    scope_registry_id: Mapped[str] = mapped_column(String(40), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_revision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 'ready' is admissible here; the two oldest promotion tables stay
    # blocked/quarantined on purpose (blocker promotion_outcome_cannot_express_ready).
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="blocked", server_default="blocked"
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_snapshot_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scope-registry.v1", server_default="scope-registry.v1"
    )
    checkpoint_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    gate_evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rls_policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rls_security_review_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rls_database_role: Mapped[str] = mapped_column(String(120), nullable=False)
    # Redacted derived evidence: parent cardinalities, gate names/digests, RLS
    # enforcement booleans, policy names and the actor-membership tuple. No payload.
    gate_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    writer_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    canonical_positive_writes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@event.listens_for(M04ScopePromotionEvidenceRow, "before_update")
def _reject_scope_evidence_mutation(_mapper, _connection, target) -> None:
    """Append-only policy: an accepted evidence row is never rewritten."""
    state = sa_inspect(target)
    changed = tuple(
        sorted(
            name
            for name in _APPEND_ONLY_FIELDS
            if state.attrs[name].history.has_changes()
        )
    )
    if changed:
        raise ValueError(
            "m04 scope promotion evidence is append-only; "
            f"immutable fields cannot be updated: {', '.join(changed)}"
        )


# Static review inputs owned by MIG-DDL-M04EVID-01. Written down, not executed: the
# verification freeze forbids running tests, DDL or migrations in this worktree.
M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES = (
    "outcome_admits_blocked_quarantined_and_ready",
    "only_a_ready_row_is_consumable_partition_evidence",
    "ready_row_requires_actor_membership_proof",
    "non_ready_row_requires_a_redacted_reason_code",
    "typed_scope_shape_matches_the_scope_registry_owner_kinds",
    "plugin_instance_scope_cannot_claim_scope_evidence",
    "every_scope_digest_column_is_sha256_formatted",
    "repeated_revision_id_in_one_partition_is_rejected",
    "append_only_row_rejects_every_orm_update",
    "canonical_positive_writes_stays_false",
    "no_payload_or_secret_material_is_persisted",
    "partition_read_no_longer_refuses_for_a_missing_table",
)

#: Gaps this table deliberately does not close, recorded so a reviewer can tell a
#: missing artefact from a reviewed trade-off.
M04_SCOPE_EVIDENCE_DEFERRED = {
    "rls_policy_installation": (
        "The table is registered in infrastructure/persistence/rls/policies.py with "
        "owner_revision 2026110313, but revision 2026110313 only creates the table. The "
        "ENABLE/FORCE RLS policy is installed by the next registry re-apply revision "
        "(the same pattern as 2026110307 -> 2026110310 -> 2026110312). Two pre-existing "
        "gates stay red until that revision lands, both owned outside this task: "
        "tests/migration/rls/test_policy_install_order.py::"
        "test_every_registered_table_is_policied_after_its_creating_revision (no "
        "installer at or after 2026110313) and tests/migration/rls/"
        "test_policy_registry_contract.py::test_every_wave_table_is_registered_exactly_"
        "once plus ::test_each_owner_revision_owns_exactly_its_declared_tables "
        "(WAVE_TABLES has no 2026110313 entry)."
    ),
    "registry_version_not_bumped": (
        "policies.REGISTRY_VERSION still reads 2026110307.1. Bumping it would change "
        "application/migration/tenant_context.py::SECURITY_REVIEW_ID, which is the "
        "identity of the security-reviewed RLS manifest used as "
        "ReviewedM04RlsEvidence.review_id; that re-approval belongs to the RLS "
        "follow-up task, not to a table-creation task."
    ),
    "scope_registry_tenant_equality": (
        "fk_m04_scope_promotion_registry proves the registry row exists, not that it "
        "belongs to the same tenant: scope_registry has no (id, tenant_id) unique key "
        "and adding one would mutate a table owned by revision 2026110301 from this "
        "task's zone. Tenant equality is enforced by the trusted writer and by the "
        "tenant-scoped RLS policy on tenant_id."
    ),
    "in_place_append_only_enforcement": (
        "The ORM guard is a mapper-level before_update listener. Bulk/raw SQL "
        "immutability still needs a database privilege or trigger gate, exactly like "
        "the M04E/M05 promotion rows."
    ),
    "writer_and_composition_root": (
        "No production writer appends rows and no composition root binds this store to "
        "SqlPartitionHandoffReadStore; evidence_ports.py and the application layer are "
        "owned by other tasks. The table removes the 'missing table' blocker; it does "
        "not by itself make load_partition return accepted evidence for a partition."
    ),
}

__all__ = [
    "M04ScopePromotionEvidenceRow",
    "M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES",
    "M04_SCOPE_EVIDENCE_DEFERRED",
]
