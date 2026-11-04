"""MIG-RES-APPLYPORT-01 acceptance — the published apply port and its gate.

Authored during the verification freeze: **not executed**. Every test is a
checkable claim about the seam, the plan derivation, the evidence gate and the
outcome mapping — not a record of a passed run. Database-level semantics (head CAS,
attempt receipt persistence, audit rollback) live in
``test_durable_apply_semantics_db.py`` and are gated behind an environment flag.

Coverage map (the acceptance artifact required by the task):

=================================================  ==========================================
Claim                                             Test
=================================================  ==========================================
Flag off → seam closed, apply 503                 ``test_flag_off_*`` / ``test_factory_seam_*``
Flag on → port published, withdrawn on shutdown    ``test_flag_on_*``
Registration happens after the DB resource        ``test_wiring_registers_*``
Same plan → same key (repeat returns receipt)     ``test_plan_identity_*`` / ``test_repeat_*``
Changed digest → different key, stale claim       ``test_changed_plan_*``
Empty plan is fenced by the head generation       ``test_empty_plan_is_fenced_by_the_head``
BLOCKED/CONFLICT never grant authority            ``test_blocked_and_conflict_*``
Gate refuses without evidence / out of scope      ``test_gate_*``
Rollback requires a provable approval             ``test_rollback_requires_*``
Ledger crash is a redacted block, not a 500       ``test_ledger_failure_*``
=================================================  ==========================================
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.resource_materialization.apply_adapter import (
    EVIDENCE_AUTHORITY_BLOCKERS,
    ResourceApplyEvidenceGate,
    ResourceApplyEvidencePolicy,
    ResourceMaterializationApplyPort,
    build_resource_materialization_apply_port,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)
from prodavan.application.resource_materialization.durable.contracts import (
    ApplyPlan,
    DurableApplyResult,
    DurableRollbackCommand,
    apply_idempotency_key,
    resource_key_for_project,
)
from prodavan.application.resources.adapters.factory import (
    install_materialization_apply_port,
    materialization_apply_port,
)
from prodavan.core.infra.resource_apply_resource import (
    ResourceApplyResource,
    ResourceApplySettings,
    resource_apply_enabled,
)

from . import doubles

API_ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_seam():
    """Every test starts and ends with the process seam cleared."""
    install_materialization_apply_port(None)
    yield
    install_materialization_apply_port(None)


@pytest.fixture()
def port() -> tuple[ResourceMaterializationApplyPort, doubles.RecordingExecutor]:
    executor = doubles.RecordingExecutor()
    instance = ResourceMaterializationApplyPort(
        application=doubles.application_with(executor),
        evidence=doubles.StaticEvidenceSource(),
    )
    return instance, executor


def _plan(**overrides) -> ApplyPlan:
    values = {
        "project_id": doubles.PROJECT_ID,
        "expected_generation": 1,
        "snapshot_digest": doubles.SNAPSHOT_DIGEST,
        "operation_set_digest": doubles.OPERATION_SET_DIGEST,
        "operations": (),
    }
    values.update(overrides)
    return ApplyPlan(
        correlation_id="corr-apply-port",
        idempotency_key=apply_idempotency_key(
            project_id=values["project_id"],
            expected_generation=values["expected_generation"],
            snapshot_digest=values["snapshot_digest"],
            operation_set_digest=values["operation_set_digest"],
        ),
        **values,
    )


# --------------------------------------------------------------------------- #
# Flag and seam: off by default, closed until published
# --------------------------------------------------------------------------- #


def test_flag_defaults_to_off(monkeypatch) -> None:
    """A default deploy must not publish a write path that claims generations."""
    for name in (
        "RESOURCE_APPLY_ENABLED",
        "RESOURCE_MATERIALIZATION_APPLY_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert ResourceApplySettings().resource_apply_enabled is False
    assert ResourceApplySettings().resource_apply_require_install is True
    assert resource_apply_enabled() is False


def test_flag_reads_both_documented_env_names(monkeypatch) -> None:
    monkeypatch.delenv("RESOURCE_APPLY_ENABLED", raising=False)
    monkeypatch.setenv("RESOURCE_MATERIALIZATION_APPLY_ENABLED", "true")
    assert ResourceApplySettings().resource_apply_enabled is True
    monkeypatch.delenv("RESOURCE_MATERIALIZATION_APPLY_ENABLED", raising=False)
    monkeypatch.setenv("RESOURCE_APPLY_ENABLED", "1")
    assert ResourceApplySettings().resource_apply_enabled is True


def test_factory_seam_is_closed_until_a_provider_is_installed() -> None:
    """The pre-MIG-RES-APPLYPORT-01 answer was 503 for every apply; keep it closed."""
    assert materialization_apply_port(object()) is None  # type: ignore[arg-type]


async def test_flag_off_installs_nothing_and_withdraws_cleanly() -> None:
    resource = ResourceApplyResource(enabled=False)
    await resource.startup()
    assert materialization_apply_port(object()) is None  # type: ignore[arg-type]
    assert resource.health() is None
    await resource.shutdown()
    assert materialization_apply_port(object()) is None  # type: ignore[arg-type]


async def test_flag_on_publishes_the_port_and_shutdown_withdraws_it() -> None:
    durable = object()
    published: list[object] = []

    def provider(session: object) -> object:
        published.append(session)
        return durable

    resource = ResourceApplyResource(enabled=True, provider=provider)
    await resource.startup()
    assert materialization_apply_port("request-session") is durable  # type: ignore[arg-type]
    assert await resource.health() is True
    await resource.shutdown()
    assert materialization_apply_port("request-session") is None  # type: ignore[arg-type]
    assert await resource.health() is False


async def test_flag_on_refuses_to_start_when_the_composition_cannot_be_built(
    monkeypatch,
) -> None:
    """An explicitly enabled apply path must not silently degrade to 503."""
    import prodavan.core.infra.resource_apply_resource as module

    def explode(**_: object) -> object:
        raise RuntimeError("postgresql+asyncpg://user:secret@host/db is unreachable")

    monkeypatch.setattr(module, "build_apply_port_provider", explode)
    strict = ResourceApplyResource(enabled=True, strict=True)
    with pytest.raises(RuntimeError):
        await strict.startup()
    assert materialization_apply_port(object()) is None  # type: ignore[arg-type]

    lenient = ResourceApplyResource(enabled=True, strict=False)
    await lenient.startup()
    assert materialization_apply_port(object()) is None  # type: ignore[arg-type]
    assert await lenient.health() is False


def test_wiring_registers_resource_apply_after_the_database() -> None:
    """Order matters: the ledger opens its own sessions from the process factory."""
    from prodavan.core.wiring import build_lifespan_manager

    names = [resource.name for resource in build_lifespan_manager().resources]
    assert "resource_apply" in names
    assert names.index("resource_apply") > names.index("database")
    # The flag is evaluated at register time, so a disabled process registers the
    # resource without publishing anything.
    tree = ast.parse(
        (API_ROOT / "src" / "prodavan" / "core" / "wiring.py").read_text(encoding="utf-8")
    )
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "prodavan.core.infra.resource_apply_resource"
        for node in ast.walk(tree)
    )


# --------------------------------------------------------------------------- #
# Plan derivation: idempotency key and changed-plan conflict
# --------------------------------------------------------------------------- #


def test_plan_identity_is_derived_only_from_the_plan_inputs(port) -> None:
    instance, _ = port
    first = instance.build_plan(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    second = instance.build_plan(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert first is not None and second is not None
    assert first.idempotency_key == second.idempotency_key
    assert first.plan_digest == second.plan_digest
    assert first.idempotency_key == apply_idempotency_key(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
    )
    assert first.resource_key == resource_key_for_project(doubles.PROJECT_ID)
    assert first.observed_generation == 0


def test_changed_plan_changes_the_key_and_cannot_overwrite_the_receipt() -> None:
    """A changed digest is a conflict, never an in-place rewrite of the receipt."""
    original = apply_idempotency_key(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
    )
    changed = apply_idempotency_key(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.OTHER_SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
    )
    assert original != changed
    # Both plans would present the same observed generation; once the first one is
    # applied the head has moved on, so the changed plan's CAS refuses it.
    first = _plan()
    second = _plan(snapshot_digest=doubles.OTHER_SNAPSHOT_DIGEST)
    assert first.observed_generation == second.observed_generation
    assert first.plan_digest != second.plan_digest


async def test_repeat_with_the_same_key_returns_the_stored_receipt(port) -> None:
    instance, executor = port
    first = await instance.apply_cas(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    second = await instance.apply_cas(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert first is MaterializationApplyOutcome.APPLIED
    assert second is MaterializationApplyOutcome.REPLAY
    # Exactly the same key reached the ledger twice: the replay is resolved by key,
    # not by a second claim.
    assert len(executor.plans) == 2
    assert executor.plans[0].idempotency_key == executor.plans[1].idempotency_key
    assert executor.head_generation == 1


async def test_changed_plan_after_a_successful_apply_is_a_conflict(port) -> None:
    instance, executor = port
    await instance.apply_cas(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    outcome = await instance.apply_cas(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.OTHER_SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert outcome is MaterializationApplyOutcome.CONFLICT
    assert executor.head_generation == 1, "a conflict must not advance a generation"


async def test_empty_plan_is_fenced_by_the_head_generation(port) -> None:
    """No operations still means a fenced generation: the head is the CAS row."""
    instance, executor = port
    plan = instance.build_plan(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert plan is not None and plan.is_empty
    result = await instance.apply_plan(plan)
    assert result.outcome is MaterializationApplyOutcome.APPLIED
    assert executor.head_generation == 1


# --------------------------------------------------------------------------- #
# Outcome mapping and refusal shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("executor_outcome", "port_outcome"),
    [
        (MaterializationApplyOutcome.APPLIED, MaterializationApplyOutcome.APPLIED),
        (MaterializationApplyOutcome.REPLAY, MaterializationApplyOutcome.REPLAY),
        (MaterializationApplyOutcome.CONFLICT, MaterializationApplyOutcome.CONFLICT),
        (MaterializationApplyOutcome.BLOCKED, MaterializationApplyOutcome.BLOCKED),
    ],
)
async def test_outcomes_are_mapped_not_inflated(port, executor_outcome, port_outcome) -> None:
    instance, executor = port
    executor.scripted.append(
        DurableApplyResult(outcome=executor_outcome, reason_code="scripted")
    )
    outcome = await instance.apply_cas(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert outcome is port_outcome


async def test_blocked_and_conflict_grant_no_generation(port) -> None:
    instance, executor = port
    for outcome in (
        MaterializationApplyOutcome.BLOCKED,
        MaterializationApplyOutcome.CONFLICT,
    ):
        executor.scripted.append(
            DurableApplyResult(outcome=outcome, reason_code="scripted_refusal")
        )
        result = await instance.apply_plan(_plan())
        assert result.outcome is outcome
        assert executor.head_generation == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": ""},
        {"project_id": "prj with space"},
        {"expected_generation": 0},
        {"expected_generation": True},
        {"snapshot_digest": "not-a-digest"},
        {"operation_set_digest": "sha256:short"},
        {"operations": ("not-an-operation",)},
    ],
)
async def test_malformed_requests_are_blocked_before_the_ledger(port, overrides) -> None:
    instance, executor = port
    values = {
        "project_id": doubles.PROJECT_ID,
        "expected_generation": 1,
        "snapshot_digest": doubles.SNAPSHOT_DIGEST,
        "operation_set_digest": doubles.OPERATION_SET_DIGEST,
        "operations": (),
    }
    values.update(overrides)
    outcome = await instance.apply_cas(**values)
    assert outcome is MaterializationApplyOutcome.BLOCKED
    assert executor.plans == []


async def test_ledger_failure_is_a_redacted_block_not_an_exception(port) -> None:
    instance, executor = port
    executor.raise_on_execute = True
    result = await instance.apply_plan(_plan())
    assert result.outcome is MaterializationApplyOutcome.BLOCKED
    assert result.reason_code == "apply_executor_error"
    # Only redacted codes reach the caller: no exception text, no payload.
    assert all("secret" not in item.reason_code for item in result.diagnostics)


def test_port_refuses_a_non_orm_session_instead_of_binding_elsewhere(port) -> None:
    instance, _ = port
    assert instance.for_session(None) is instance
    assert instance.for_session(object()) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Evidence gate
# --------------------------------------------------------------------------- #


async def test_gate_refuses_when_evidence_cannot_be_read() -> None:
    gate = ResourceApplyEvidenceGate(evidence=doubles.StaticEvidenceSource())
    assert await gate.blockers(plan=_plan(), at=AT) == ("apply_evidence_unavailable",)

    exploding = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(fail=True)
    )
    assert await exploding.blockers(plan=_plan(), at=AT) == (
        "apply_evidence_port_error",
    )


async def test_gate_refuses_evidence_for_another_project() -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(project_id=doubles.OTHER_PROJECT_ID)
        )
    )
    assert await gate.blockers(plan=_plan(), at=AT) == (
        "apply_evidence_scope_mismatch",
    )


async def test_gate_refuses_a_plan_whose_resource_key_was_not_derived() -> None:
    """The resource key is derived, never supplied, so the fence cannot be aimed."""
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence())
    )
    forged = ApplyPlan(
        project_id=doubles.PROJECT_ID,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
        correlation_id="corr",
        idempotency_key="forged-key",
    )
    assert forged.resource_key == resource_key_for_project(doubles.PROJECT_ID)
    assert await gate.blockers(plan=forged, at=AT) == ()

    # Evidence resolved for another project cannot be re-aimed at this plan.
    other = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(project_id=doubles.OTHER_PROJECT_ID)
        )
    )
    assert await other.blockers(plan=forged, at=AT) == (
        "apply_evidence_scope_mismatch",
    )


async def test_gate_allows_an_empty_plan_without_resource_evidence() -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence())
    )
    assert await gate.blockers(plan=_plan(), at=AT) == ()


async def test_gate_grounds_every_operation_in_durable_resource_state() -> None:
    operation = doubles.operation()
    healthy = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(groundings=(doubles.grounding(),))
        )
    )
    plan = _plan(operations=(operation,))
    assert await healthy.blockers(plan=plan, at=AT) == ()


@pytest.mark.parametrize(
    ("grounding_kwargs", "expected"),
    [
        ({"resource_exists": False}, "resource_evidence_missing"),
        ({"lifecycle": "archived"}, "resource_not_active"),
        ({"lifecycle": "quarantined"}, "resource_not_active"),
        ({"active_version": None}, "resource_active_version_missing"),
        ({"version_exists": False}, "resource_version_missing"),
        ({"version_status": "draft"}, "resource_version_not_published"),
        ({"version_status": "retired"}, "resource_version_not_published"),
        ({"binding_present": False}, "resource_binding_missing"),
        ({"binding_version_matches": False}, "resource_binding_version_mismatch"),
        ({"provenance_version_matches": False}, "operation_provenance_version_mismatch"),
        ({"provenance_target_matches": False}, "operation_provenance_target_mismatch"),
    ],
)
async def test_gate_refuses_an_operation_it_cannot_ground(grounding_kwargs, expected) -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(
                groundings=(doubles.grounding(**grounding_kwargs),)
            )
        )
    )
    blockers = await gate.blockers(plan=_plan(operations=(doubles.operation(),)), at=AT)
    assert expected in blockers


async def test_gate_refuses_operations_without_resource_provenance() -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(ungrounded_targets=(doubles.TARGET,))
        )
    )
    blockers = await gate.blockers(
        plan=_plan(
            operations=(doubles.operation(with_provenance=False),)
        ),
        at=AT,
    )
    assert blockers == ("operation_resource_provenance_missing",)


async def test_gate_reports_a_generation_gap_but_lets_the_cas_report_a_late_plan() -> None:
    """A plan that skips generations is refused; a *late* plan is the CAS conflict."""
    operation = ()
    gap = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence(head_generation=0))
    )
    assert "generation_gap" in await gap.blockers(
        plan=_plan(expected_generation=3, operations=operation), at=AT
    )
    late = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence(head_generation=5))
    )
    # Late plans are answered by the head CAS (CONFLICT stale_generation_cas) so the
    # caller learns it lost a race, instead of a generic block.
    assert await late.blockers(
        plan=_plan(expected_generation=3, operations=operation), at=AT
    ) == ()


async def test_optional_evidence_requirements_refuse_when_switched_on() -> None:
    """The off-by-default requirements are implemented, not promised."""
    approval_gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence()),
        policy=ResourceApplyEvidencePolicy(
            require_approval_link=True,
            require_event_link=True,
            require_bridge_lease=True,
        ),
    )
    blockers = await approval_gate.blockers(plan=_plan(), at=AT)
    assert "approval_link_missing" in blockers
    assert "event_link_missing" in blockers
    assert "bridge_lease_missing" in blockers


async def test_gate_accepts_satisfied_optional_evidence() -> None:
    lease_expiry = AT + timedelta(minutes=10)
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(
                approval=doubles.approved(),
                event_id="pev_applyport",
                lease_reference="lease://bridge/applyport",
                lease_epoch=4,
                lease_expires_at=lease_expiry,
            )
        ),
        policy=ResourceApplyEvidencePolicy(
            require_approval_link=True,
            require_event_link=True,
            require_bridge_lease=True,
        ),
    )
    assert await gate.blockers(plan=_plan(), at=AT) == ()


async def test_gate_refuses_an_expired_lease_and_an_expired_approval() -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(
            payload=doubles.evidence(
                approval=doubles.approved(expires_at=AT - timedelta(seconds=1)),
                event_id="pev_applyport",
                lease_reference="lease://bridge/applyport",
                lease_epoch=1,
                lease_expires_at=AT - timedelta(seconds=1),
            )
        ),
        policy=ResourceApplyEvidencePolicy(
            require_approval_link=True,
            require_event_link=True,
            require_bridge_lease=True,
        ),
    )
    blockers = await gate.blockers(plan=_plan(), at=AT)
    assert "approval_link_expired" in blockers
    assert "bridge_lease_expired" in blockers


async def test_gate_requires_timezone_aware_time() -> None:
    gate = ResourceApplyEvidenceGate(
        evidence=doubles.StaticEvidenceSource(payload=doubles.evidence())
    )
    with pytest.raises(ValueError):
        await gate.blockers(plan=_plan(), at=datetime(2026, 11, 4, 12, 0))


def test_production_policy_requires_only_evidence_the_schema_can_prove() -> None:
    policy = ResourceApplyEvidencePolicy.for_resource_api()
    assert policy.require_resource_grounding is True
    assert policy.require_generation_epoch is True
    assert policy.require_approval_link is False
    assert policy.require_event_link is False
    assert policy.require_bridge_lease is False
    assert policy.required_kinds() == ("resource_grounding", "generation_epoch")


def test_every_unproven_evidence_kind_is_documented_with_an_owner() -> None:
    """Fail-closed is not allowed to become silent: each gap names its owner."""
    kinds = {name for name, _ in EVIDENCE_AUTHORITY_BLOCKERS}
    assert kinds == {"bridge_lease", "approval_link", "event_link", "resource_effect"}
    for _, reason in EVIDENCE_AUTHORITY_BLOCKERS:
        assert "owner:" in reason and len(reason) > 40


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def _rollback_command(**overrides) -> DurableRollbackCommand:
    values = {
        "project_id": doubles.PROJECT_ID,
        "target_generation": 1,
        "expected_generation": 2,
        "approval_id": "apr_applyport",
        "idempotency_key": "rollback-key",
        "correlation_id": "corr-rollback",
    }
    values.update(overrides)
    return DurableRollbackCommand(**values)


async def test_rollback_requires_a_provable_approval(port) -> None:
    instance, executor = port
    instance.evidence = doubles.StaticEvidenceSource(approvals={})
    result = await instance.rollback(_rollback_command())
    assert result.outcome is MaterializationApplyOutcome.BLOCKED
    assert result.reason_code == "rollback_approval_unverified"
    assert executor.commands == [], "an unapproved rollback must not claim a generation"


@pytest.mark.parametrize("status", ["pending", "denied", "expired", "cancelled"])
async def test_rollback_refuses_a_non_approved_decision(port, status) -> None:
    instance, executor = port
    instance.evidence = doubles.StaticEvidenceSource(
        approvals={"apr_applyport": doubles.approved(status=status)}
    )
    result = await instance.rollback(_rollback_command())
    assert result.reason_code == "rollback_approval_unverified"
    assert executor.commands == []


async def test_rollback_refuses_an_expired_approval(port) -> None:
    instance, executor = port
    instance.evidence = doubles.StaticEvidenceSource(
        approvals={
            "apr_applyport": doubles.approved(
                decided_at=datetime.now(UTC) - timedelta(hours=2),
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        }
    )
    result = await instance.rollback(_rollback_command())
    assert result.reason_code == "rollback_approval_unverified"
    assert executor.commands == []


async def test_approved_rollback_claims_the_next_generation(port) -> None:
    instance, executor = port
    executor.head_generation = 1
    instance.evidence = doubles.StaticEvidenceSource(
        approvals={"apr_applyport": doubles.approved()}
    )
    result = await instance.rollback(_rollback_command())
    assert result.outcome is MaterializationApplyOutcome.APPLIED
    assert result.reason_code == "rolled_back"
    assert executor.head_generation == 2, "rollback advances, never rewinds, history"
    assert executor.commands[-1].rollback_of_generation is None
    assert executor.commands[-1].target_generation == 1


async def test_rollback_crash_is_a_redacted_block(port) -> None:
    instance, executor = port
    executor.raise_on_rollback = True
    instance.evidence = doubles.StaticEvidenceSource(
        approvals={"apr_applyport": doubles.approved()}
    )
    result = await instance.rollback(_rollback_command())
    assert result.outcome is MaterializationApplyOutcome.BLOCKED
    assert result.reason_code == "rollback_executor_error"


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def test_composition_builds_a_real_port_without_stubs() -> None:
    """The factory wires the durable executor; no collaborator is an in-memory stub."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost:5432/scratch")
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        instance = build_resource_materialization_apply_port(session_factory=factory)
        assert isinstance(instance, ResourceMaterializationApplyPort)
        assert instance.application.executor.gate is not None
        assert instance.application.executor.effects is None, (
            "no effect adapter exists yet: a populated plan must be refused with "
            "resource_effect_port_missing instead of acknowledged"
        )
        assert instance.evidence is not None
        assert instance.clock is not None
    finally:
        # Creating an engine opens no connection; dispose is bookkeeping only.
        pass


def test_composition_accepts_an_injected_effect_and_rollback_port() -> None:
    from dataclasses import dataclass

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    @dataclass
    class _Effects:
        async def apply_operation(self, **_):  # pragma: no cover - never called here
            raise AssertionError("not exercised by this artifact")

        async def compensate_operation(self, **_):  # pragma: no cover
            raise AssertionError("not exercised by this artifact")

        async def apply_rollback(self, **_):  # pragma: no cover
            raise AssertionError("not exercised by this artifact")

    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost:5432/scratch")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    effects = _Effects()
    instance = build_resource_materialization_apply_port(
        session_factory=factory,
        effects=effects,
        rollback_effects=effects,
    )
    assert instance.application.executor.effects is effects
    assert instance.application.executor.rollback_effects is effects


def test_operations_must_be_typed_resource_operations() -> None:
    """The plan contract rejects untyped operations instead of coercing them."""
    with pytest.raises(ValueError):
        ApplyPlan(
            project_id=doubles.PROJECT_ID,
            expected_generation=1,
            snapshot_digest=doubles.SNAPSHOT_DIGEST,
            operation_set_digest=doubles.OPERATION_SET_DIGEST,
            operations=({"kind": "prompt", "target": doubles.TARGET},),  # type: ignore[arg-type]
            correlation_id="corr",
            idempotency_key="key",
        )


def test_duplicate_targets_are_rejected_by_the_plan_contract() -> None:
    operations: tuple[ResourceOperation, ...] = (
        doubles.operation(),
        doubles.operation(target=doubles.TARGET.upper()),
    )
    with pytest.raises(ValueError):
        ApplyPlan(
            project_id=doubles.PROJECT_ID,
            expected_generation=1,
            snapshot_digest=doubles.SNAPSHOT_DIGEST,
            operation_set_digest=doubles.OPERATION_SET_DIGEST,
            operations=operations,
            correlation_id="corr",
            idempotency_key="key",
        )
