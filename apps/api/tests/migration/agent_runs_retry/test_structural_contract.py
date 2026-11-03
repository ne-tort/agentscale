"""Structural guarantees for the retry/reconcile modules (contract-level).

These assertions protect the properties the acceptance vectors rely on but
that are easier to check structurally than behaviourally: no schema ownership
leaked into the application layer, no sleeping loops, fixed blocker/reason
codes, and a redacted audit payload.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from prodavan.application.agent_runs import reconcile as reconcile_module
from prodavan.application.agent_runs import retry as retry_module
from prodavan.application.agent_runs.reconcile import (
    RECONCILIATION_AUDIT_REQUIRED_COLUMNS,
    RECONCILE_BLOCKERS,
    UNKNOWN_OUTCOME_REQUIRED_COLUMNS,
)
from prodavan.application.agent_runs.retry import (
    RETRY_BLOCKERS,
    RETRY_SCHEDULE_REQUIRED_COLUMNS,
)

from .acceptance_fixtures import DIGEST_A, NOW

MODULES = (retry_module, reconcile_module)


def _source(module) -> str:
    return inspect.getsource(module)


def test_modules_never_sleep_or_spin() -> None:
    """Work is selected by comparing ``now`` with a persisted time, never by waiting."""
    forbidden = ("asyncio.sleep", "time.sleep", "anyio.sleep", "while True")
    for module in MODULES:
        source = _source(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} contains {token}"


def test_application_modules_register_no_orm_metadata_and_no_ddl() -> None:
    from prodavan.infrastructure.persistence.models.base import Base

    owned = {"agent_run_retry_schedules", "agent_run_unknown_outcomes"}
    assert not owned & set(Base.metadata.tables)
    for module in MODULES:
        source = _source(module)
        for token in ("CreateTable", "op.create_table", "ForeignKey(", "declarative_base"):
            assert token not in source, f"{module.__name__} must not own schema: {token}"


def test_published_table_contracts_are_complete_and_unique() -> None:
    for columns in (
        RETRY_SCHEDULE_REQUIRED_COLUMNS,
        UNKNOWN_OUTCOME_REQUIRED_COLUMNS,
        RECONCILIATION_AUDIT_REQUIRED_COLUMNS,
    ):
        assert len(columns) == len(set(columns))
        assert all(isinstance(name, str) and name for name in columns)

    # The idempotency keys and the due-selection columns must be part of the contract.
    assert {"tenant_id", "run_id", "run_generation", "run_attempt"} <= set(
        UNKNOWN_OUTCOME_REQUIRED_COLUMNS
    )
    assert {"schedule_key", "retry_at", "state", "lease_until"} <= set(
        RETRY_SCHEDULE_REQUIRED_COLUMNS
    )
    assert {"reconcile_key", "transition_key", "evidence_digest"} <= set(
        UNKNOWN_OUTCOME_REQUIRED_COLUMNS
    )


def test_blocker_codes_are_distinct_and_stable() -> None:
    assert RETRY_BLOCKERS["table"] == "agent_run_retry_schedules_table_missing"
    assert RECONCILE_BLOCKERS["table"] == "agent_run_unknown_outcomes_table_missing"
    assert retry_module.WIRING_BLOCKER == "agent_run_retry_journal_unwired"
    assert reconcile_module.WIRING_BLOCKER == "agent_run_unknown_outcome_journal_unwired"
    # Both match the readiness-blocker vocabulary already published to the API.
    assert retry_module.WIRING_BLOCKER.startswith("agent_run_")
    assert reconcile_module.WIRING_BLOCKER.startswith("agent_run_")


def test_reason_codes_are_redacted_namespaced_constants() -> None:
    for name in [item for item in dir(retry_module) if item.startswith("RETRY_REASON_")]:
        value = getattr(retry_module, name)
        assert value.startswith("agent_run.retry."), name
    for name in [item for item in dir(reconcile_module) if item.startswith("RECONCILE_REASON_")]:
        value = getattr(reconcile_module, name)
        assert value.startswith("agent_run.reconcile."), name


def test_sql_adapters_fail_closed_before_touching_schema() -> None:
    for module, adapter, blocker in (
        (retry_module, "SqlAlchemyAgentRunRetryJournal", retry_module.RETRY_TABLE_BLOCKER),
        (reconcile_module, "SqlAlchemyUnknownOutcomeJournal", reconcile_module.RECONCILE_BLOCKER_TABLE),
    ):
        cls = getattr(module, adapter)
        assert hasattr(cls, "ensure_ready")
        source = inspect.getsource(cls.ensure_ready)
        assert blocker in source
        assert "ProgrammingError" in source


def test_due_selection_uses_skip_locked_in_both_adapters() -> None:
    for module, adapter in (
        (retry_module, "SqlAlchemyAgentRunRetryJournal"),
        (reconcile_module, "SqlAlchemyUnknownOutcomeJournal"),
    ):
        source = inspect.getsource(getattr(module, adapter).claim_due)
        assert "skip_locked=True" in source
        assert "tenant_id ==" in source


def test_audit_payload_has_no_payload_or_message_fields() -> None:
    from prodavan.application.agent_runs.reconcile import (
        CaseDecision,
        CaseResolution,
        ReconciliationAuditEntry,
    )

    entry = ReconciliationAuditEntry(
        audit_id="ara_1",
        tenant_id="tenant-a",
        project_id="project-1",
        run_id="agr_1",
        generation=1,
        attempt=1,
        decision=CaseDecision.SUCCEEDED,
        resolution=CaseResolution.SUCCEEDED,
        reason_code="agent_run.reconcile.settled",
        evidence_id="provider-attempt-1",
        evidence_digest=DIGEST_A,
        recorded_at=NOW,
    )
    payload = entry.redacted
    assert not {"payload", "output", "message", "error", "secret", "token"} & set(payload)
    assert all(not isinstance(value, dict) for value in payload.values())


def test_modules_are_reachable_from_the_agent_runs_package_path() -> None:
    root = Path(retry_module.__file__).parent  # type: ignore[arg-type]
    assert (root / "retry.py").is_file()
    assert (root / "reconcile.py").is_file()
    # This migration task must not have modified the fenced execution surface.
    execution = (root / "execution.py").read_text(encoding="utf-8")
    assert "unknown provider outcomes must be reconciled" in execution
