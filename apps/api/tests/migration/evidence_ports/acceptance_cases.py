"""Acceptance matrix for MIG-EVIDENCE-01 (review input, not an executed check).

Each case names the contract clause it covers, the port it belongs to and — where the
case is behavioural — the test that pins it. Cases that are *not* executable under the
migration-stage verification freeze are marked ``executable=False`` with the reason, so a
later reviewer cannot mistake the matrix for a green suite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    case_id: str
    port: str
    clause: str
    covered_by: str
    executable: bool = True


ACCEPTANCE_CASES: tuple[AcceptanceCase, ...] = (
    AcceptanceCase(
        "append_creates_row",
        "SqlM05HandoffEvidenceStore.append_marker / SqlM05PromotionEvidenceStore.append_immutable",
        "append-only: one INSERT of one registered row, no UPDATE/DELETE",
        "test_contract_surface.py::test_module_issues_no_update_or_delete_statement",
    ),
    AcceptanceCase(
        "repeat_is_idempotent",
        "append_marker / append_immutable",
        "an exact repeat returns the recorded evidence and inserts nothing",
        "test_identity_and_replay.py::test_exact_marker_repeat_is_a_replay",
    ),
    AcceptanceCase(
        "digest_conflict_is_typed",
        "append_marker / append_immutable",
        "same logical key with a different digest is a typed conflict, never an overwrite",
        "test_identity_and_replay.py::test_marker_with_another_digest_is_a_conflict",
    ),
    AcceptanceCase(
        "read_by_exact_key",
        "SqlPartitionHandoffReadStore.read_partition",
        "read is by the exact tenant/definition/version/instance/writer tuple",
        "test_refusals.py::test_partition_read_uses_every_request_key",
    ),
    AcceptanceCase(
        "absence_is_none",
        "decode_marker_row / decode_promotion_row / load_partition",
        "no stored evidence yields None or a named refusal, never an empty object",
        "test_refusals.py::test_absent_row_decodes_to_none",
    ),
    AcceptanceCase(
        "foreign_partition_invisible",
        "all four ports",
        "a partition outside the trusted tenant is refused before any SQL",
        "test_refusals.py::test_foreign_tenant_is_refused_before_sql",
    ),
    AcceptanceCase(
        "no_in_memory_path",
        "module surface",
        "no in-memory store, no test double and no test-double marker in the module",
        "test_contract_surface.py::test_module_declares_no_in_memory_or_test_double_path",
    ),
    AcceptanceCase(
        "redaction",
        "all append paths",
        "payload/manifest/secret keys are refused, not silently dropped",
        "test_payloads.py::test_payload_refuses_carrying_keys",
    ),
    AcceptanceCase(
        "row_columns_match_models",
        "handoff_marker_row_values / promotion_row_values",
        "every mapped column exists on the registered ORM model",
        "test_payloads.py::test_row_values_match_registered_models",
    ),
    AcceptanceCase(
        "checkpoint_cursor_cas",
        "SqlM04EBackfillEvidenceStore.classify_and_commit_page",
        "an advanced/regressed/foreign durable cursor is a typed conflict",
        "test_backfill_cursor.py::test_cursor_cas_refuses_every_divergent_cursor",
    ),
    AcceptanceCase(
        "ready_outcome_needs_envelope",
        "handoff_marker_row_values / promotion_row_values",
        "the outcome column stays inside its check constraint while the handoff status is exact",
        "test_payloads.py::test_outcome_column_stays_inside_its_check_constraint",
    ),
    AcceptanceCase(
        "insert_under_real_database",
        "all four ports",
        "INSERT/SELECT/replay/conflict against a live PostgreSQL with RLS installed",
        "not written: requires DATABASE_URL and an applied 2026110302/03/04/07 schema",
        executable=False,
    ),
    AcceptanceCase(
        "rls_denial_under_tenant_role",
        "SqlPartitionHandoffReadStore / append paths",
        "a tenant role without the migration GUC sees no foreign row",
        "not written: requires a provisioned non-superuser role (see rls registry review)",
        executable=False,
    ),
)


def case_ids() -> tuple[str, ...]:
    return tuple(case.case_id for case in ACCEPTANCE_CASES)


__all__ = ["ACCEPTANCE_CASES", "AcceptanceCase", "case_ids"]
