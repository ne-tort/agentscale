"""MIG-RES-02 acceptance fixtures; specifications only, intentionally not run."""
from dataclasses import replace

import pytest

from prodavan.application.migration.runtime_materialization.materialization import (
    ApplyOutcome, DependencyEvidence, DesiredRevision, PlanAction, Provenance,
    RollbackCommand, plan_materialization,
)


@pytest.fixture
def provenance():
    return Provenance("agent_run", "run-fixture-1", "revision-1", "corr-fixture-1")


@pytest.fixture
def blocked_desired(provenance):
    return DesiredRevision("pod/runtime-1", 1, {"replicas": 1}, provenance, DependencyEvidence())


def test_default_missing_contracts_fail_closed(blocked_desired):
    plan = plan_materialization(blocked_desired, None, idempotency_key="idem-1")
    assert plan.action is PlanAction.BLOCKED
    assert {"upstream_resource_contract_missing", "bridge_lease_missing",
            "projected_secret_reference_missing", "immutable_image_digest_missing",
            "agent_run_link_missing", "approval_link_missing", "event_link_missing"} <= set(plan.blockers)


def test_desired_revision_is_immutable_and_digest_bound(provenance):
    desired = DesiredRevision("pod/runtime-1", 1, {"replicas": 1}, provenance, DependencyEvidence())
    with pytest.raises(Exception):
        desired.revision = 2
    altered = replace(desired, spec={"replicas": 2})
    assert altered.digest != desired.digest


def test_plan_is_deterministic_and_idempotency_scoped(provenance):
    evidence = DependencyEvidence("lease/1", "secret-ref/1", "sha256:" + "a" * 64,
                                  "run-1", "approval-1", "event-1", True)
    desired = DesiredRevision("pod/runtime-1", 1, {"replicas": 1}, provenance, evidence)
    first = plan_materialization(desired, None, idempotency_key="idem-1")
    repeated = plan_materialization(desired, None, idempotency_key="idem-1")
    other_key = plan_materialization(desired, None, idempotency_key="idem-2")
    assert first == repeated
    assert first.action is PlanAction.CREATE
    assert first.next_generation == 1
    assert first.plan_digest != other_key.plan_digest


def test_rollback_requires_approval_and_complete_evidence():
    command = RollbackCommand("pod/runtime-1", 1, 3, None, "rollback-1")
    assert "rollback_approval_missing" in command.blockers(DependencyEvidence())


# Additional adapter-level acceptance scenarios required before promotion:
# - CAS race: only one apply for the same expected generation succeeds.
# - Identical apply replay returns the exact AppliedRevision without generation bump.
# - Same idempotency key with a changed plan conflicts.
# - Generation mismatch and malformed plan binding never invoke apply_cas.
# - Bridge lease expiry/revocation, missing projected-secret reference, mutable image
#   tags, and unlinked agent_run/approval/event each deny before side effects.
# - Rollback is a new monotonic generation referencing an immutable prior applied
#   revision; it never decrements generation or mutates desired/applied history.
# - Redacted audit is mandatory and failure prevents positive acknowledgement.
# Tests/build/lint/format/compile/git checks are intentionally not run.
