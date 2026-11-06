"""Acceptance matrix for MIG-M04-WRITER-01 (review input, not an executed check).

Each case names the contract clause, the artefact that pins it and whether it is executable
under the migration-stage verification freeze. Nothing here was run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    case_id: str
    clause: str
    covered_by: str
    executable: bool = True
    note: str = ""


ACCEPTANCE_CASES: tuple[AcceptanceCase, ...] = (
    AcceptanceCase(
        "ready_row_is_accepted_by_the_reader",
        "the writer produces exactly what review_m04_scope_evidence accepts",
        "test_writer_acceptance.py::"
        "test_a_ready_row_is_appended_and_accepted_by_the_partition_reader",
        note="writer -> reader round trip for all three owner scope kinds",
    ),
    AcceptanceCase(
        "canonical_digest_is_the_m04_02_digest",
        "canonical_digest equals the digest the M04-02 coordinator validates against, which "
        "the M05 promotion record cites as its M04_scope gate",
        "test_writer_acceptance.py::test_the_canonical_digest_is_the_m04_02_accepted_digest",
        note="computed with ScopeMigrationCoordinator._canonical_digest, not the writer helper",
    ),
    AcceptanceCase(
        "typed_scope_shape",
        "tenant/cabinet/project shape per ck_m04_scope_promotion_typed_shape",
        "test_writer_acceptance.py::test_the_stored_scope_shape_is_the_typed_anchor",
    ),
    AcceptanceCase(
        "plugin_instance_denied",
        "a plugin-instance scope is refused and no row is written",
        "test_writer_refusals.py::test_a_plugin_instance_scope_is_refused_and_no_row_is_written",
        note="also proven unrepresentable by ck_m04_scope_promotion_scope_kind",
    ),
    AcceptanceCase(
        "repeat_is_idempotent",
        "an exact repeat returns the recorded row and inserts nothing",
        "test_writer_acceptance.py::"
        "test_an_exact_repeat_returns_the_recorded_row_and_inserts_nothing",
    ),
    AcceptanceCase(
        "digest_change_under_fixed_revision_is_a_conflict",
        "same revision_id with different content is a typed conflict, never an overwrite",
        "test_writer_acceptance.py::"
        "test_a_changed_digest_under_a_fixed_revision_is_a_typed_conflict",
    ),
    AcceptanceCase(
        "digest_change_appends_a_forward_revision",
        "append-only: changed evidence becomes a new revision; the older one is untouched",
        "test_writer_acceptance.py::"
        "test_a_changed_digest_under_a_derived_revision_appends_a_forward_revision",
    ),
    AcceptanceCase(
        "ready_requires_actor",
        "a ready row names the actor whose membership was proven in the same transaction",
        "test_writer_refusals.py::test_a_ready_row_without_an_actor_is_refused",
        note="the database check ck_m04_scope_promotion_ready_requires_actor is asserted too",
    ),
    AcceptanceCase(
        "non_ready_requires_reason",
        "a blocked/quarantined row carries a redacted reason code",
        "test_writer_refusals.py::test_a_non_ready_row_without_a_reason_code_is_refused",
        note="the database check ck_m04_scope_promotion_non_ready_requires_reason is asserted too",
    ),
    AcceptanceCase(
        "foreign_partition_refused_before_sql",
        "a partition outside the trusted tenant is refused before any statement",
        "test_writer_refusals.py::test_a_partition_outside_the_trusted_tenant_is_refused",
        note="ExplodingSessionFactory proves the refusal precedes the transaction",
    ),
    AcceptanceCase(
        "untrusted_writer_generation",
        "the writer epoch comes from TrustedEvidenceScope, never from the request",
        "test_writer_refusals.py::test_an_untrusted_writer_generation_is_refused",
    ),
    AcceptanceCase(
        "ready_requires_complete_proof",
        "an ACTIVE verdict without same-transaction parent/RLS/actor proof writes nothing",
        "test_writer_refusals.py::test_an_active_verdict_without_complete_proof_writes_nothing",
    ),
    AcceptanceCase(
        "append_only_no_update_no_delete",
        "no UPDATE/DELETE statement or ORM mutator exists in the module",
        "test_writer_contract.py::test_the_module_issues_no_update_or_delete_statement",
        note="static AST scan, including an import-level check",
    ),
    AcceptanceCase(
        "immutable_field_parity",
        "the writer's immutability list is the model's _APPEND_ONLY_FIELDS minus recorded_at",
        "test_writer_contract.py::"
        "test_the_immutable_field_list_is_the_model_guard_minus_recorded_at",
    ),
    AcceptanceCase(
        "redaction",
        "no raw payload/manifest/token material is persisted; a payload-shaped key is refused",
        "test_writer_contract.py::test_a_payload_shaped_key_is_refused_rather_than_dropped",
    ),
    AcceptanceCase(
        "single_transaction_with_tenant_binding",
        "one transaction per append, tenant GUCs bound and read back before any statement",
        "test_writer_contract.py::test_the_transaction_binds_the_tenant_before_any_statement",
        note="readback mismatch is also exercised behaviourally by the refusals suite",
    ),
    AcceptanceCase(
        "readback_inside_the_transaction",
        "the stored row is re-read and compared field by field",
        "test_writer_refusals.py::test_a_readback_that_differs_from_the_written_row_is_corruption",
    ),
    AcceptanceCase(
        "database_execution",
        "the writer against a live PostgreSQL under the installed RLS policy",
        "none",
        executable=False,
        note="verification freeze: no test, DDL, migration or database run happens in this "
        "worktree; the case stays open until the freeze is lifted",
    ),
    AcceptanceCase(
        "composition_wiring",
        "a composition root constructs the writer and the partition read consumes its rows",
        "none",
        executable=False,
        note="composition.py is outside this task's file zone; recorded as an open gap",
    ),
)


__all__ = ["ACCEPTANCE_CASES", "AcceptanceCase"]
