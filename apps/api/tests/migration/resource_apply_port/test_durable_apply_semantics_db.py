"""MIG-RES-APPLYPORT-01 acceptance — durable semantics of the published port.

Authored during the verification freeze: **not executed**. These are the claims the
port cannot prove without a real PostgreSQL, so they are gated behind
``PRODAVAN_RESOURCE_APPLY_ACCEPTANCE=1`` *against a scratch database* whose schema
already carries revision ``2026110306``.

Everything runs inside one outer transaction that is rolled back at the end. The
executor owns its own ``session.begin()``; the sessionmaker is therefore bound to the
outer connection with ``join_transaction_mode="create_savepoint"``, so:

* an executor commit releases a savepoint (the seed rows survive);
* an executor rollback — the audit-failure case — unwinds only that savepoint, which
  is exactly the "nothing survives the failure" property under test, and it stays
  observable from the outer connection.

Coverage:

==========================================  ==============================================
Claim                                        Test
==========================================  ==============================================
Empty plan still CASes the head              ``test_empty_plan_advances_*``
Repeat key returns the stored receipt        ``test_repeat_*``
Changed plan/digest → conflict               ``test_changed_plan_*``
Late (stale generation) plan → refused       ``test_late_plan_*``
Populated plan without effects → blocked     ``test_populated_plan_*``
Audit failure rolls back everything          ``test_audit_failure_*``
Rollback claims the next generation          ``test_rollback_*``
==========================================  ==============================================
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.application.resource_materialization.apply_adapter import (
    ResourceApplyEvidencePolicy,
    ResourceMaterializationApplyPort,
    build_resource_materialization_apply_port,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)
from prodavan.application.resource_materialization.durable.audit import (
    SqlAlchemyRuntimeAuditWriter,
)
from prodavan.application.resource_materialization.durable.contracts import (
    AuditWriteRejected,
    DurableRollbackCommand,
    attempt_digests,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    OperationEffectReceipt,
)
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    RuntimeMaterializationAttemptRow,
    RuntimeMaterializationAuditRow,
    RuntimeRevisionHeadRow,
)

from . import doubles

_DB_GATE = "PRODAVAN_RESOURCE_APPLY_ACCEPTANCE"

_COMPANY = f"co_{uuid.uuid4().hex[:20]}"
_CABINET = f"cab_{uuid.uuid4().hex[:20]}"
_PROJECT = f"prj_{uuid.uuid4().hex[:16]}"
_POD = f"pod_{uuid.uuid4().hex[:16]}"


def _database_gate() -> None:
    if os.getenv(_DB_GATE) != "1":
        pytest.skip(
            f"set {_DB_GATE}=1 against a scratch database (revision 2026110306 applied) "
            "to run the durable resource apply acceptance"
        )
    from tests.conftest import _postgres_available

    if not _postgres_available():
        pytest.skip("PostgreSQL not available at DATABASE_URL")


@dataclass(frozen=True, slots=True)
class Harness:
    """One project runtime scope plus the session factory the executor uses."""

    project_id: str
    port: ResourceMaterializationApplyPort
    session_factory: async_sessionmaker
    connection: object

    def plan(self, **overrides):
        values = {
            "project_id": self.project_id,
            "expected_generation": 1,
            "snapshot_digest": doubles.SNAPSHOT_DIGEST,
            "operation_set_digest": doubles.OPERATION_SET_DIGEST,
            "operations": (),
        }
        values.update(overrides)
        plan = self.port.build_plan(**values)
        assert plan is not None, "the harness must build an admissible plan"
        return plan


@dataclass(frozen=True, slots=True)
class HarnessOptions:
    """Per-test composition switches (``None`` = the default production policy)."""

    rollback_effects: object | None = None
    require_grounding: bool = True


@pytest.fixture()
async def harness(request):
    """Seed a runtime scope and hand the port a savepoint-bound session factory."""
    _database_gate()
    from tests.conftest import DATABASE_URL

    options: HarnessOptions = getattr(request, "param", None) or HarnessOptions()
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        if await connection.scalar(
            text("SELECT to_regclass('public.runtime_materialization_attempts')")
        ) is None:
            pytest.skip("revision 2026110306 is not applied to this database")
        await connection.execute(
            text(
                "INSERT INTO companies (id, name, login_slug, status, subscription_lifetime) "
                "VALUES (:id, :name, :slug, 'active', false) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _COMPANY, "name": _PROJECT, "slug": _COMPANY},
        )
        await connection.execute(
            text(
                "INSERT INTO cabinet_instances (id, name, schema_name, owner_scope, base_template, "
                "company_grant_scope, status) VALUES (:id, :name, :schema, 'platform', 'base', "
                "'selected', 'active') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _CABINET, "name": _PROJECT, "schema": _CABINET},
        )
        await connection.execute(
            text(
                "INSERT INTO projects (id, company_id, cabinet_id, name, slug, status, "
                "visibility_mode, workspace_key, container_ref, resume_generation) "
                "VALUES (:id, :company, :cabinet, :name, :slug, 'active', 'cabinet_shared', "
                ":workspace, :ref, 0) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": _PROJECT,
                "company": _COMPANY,
                "cabinet": _CABINET,
                "name": _PROJECT,
                "slug": _PROJECT,
                "workspace": _PROJECT,
                "ref": f"ws/{_PROJECT}",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO project_pods (id, project_id, workspace_key, status, desired_state) "
                "VALUES (:id, :project, :workspace, 'running', 'present') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _POD, "project": _PROJECT, "workspace": _PROJECT},
        )
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        port = build_resource_materialization_apply_port(
            session_factory=factory,
            # These claims target the *executor*: the head CAS, the attempt receipt,
            # the audit transaction and the monotonic rollback. The gate is driven by a
            # static evidence double here, which cannot track a moving head, so its
            # generation-gap pre-check is disabled; the gate's own matrix is covered in
            # ``test_apply_port_contract.py``. Resource grounding is switched off for the
            # one claim that must reach the executor's effect-port refusal instead of
            # stopping at the gate.
            policy=ResourceApplyEvidencePolicy(
                require_resource_grounding=options.require_grounding,
                require_generation_epoch=False,
            ),
            rollback_effects=options.rollback_effects,  # type: ignore[arg-type]
        )
        port.evidence = doubles.StaticEvidenceSource(
            payload=doubles.evidence(project_id=_PROJECT),
            approvals={"apr_applyport": doubles.approved(project_id=_PROJECT)},
        )
        yield Harness(
            project_id=_PROJECT,
            port=port,
            session_factory=factory,
            connection=connection,
        )
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def _head_generation(harness: Harness) -> int | None:
    return await harness.connection.scalar(
        text(
            "SELECT generation FROM runtime_revision_heads WHERE resource_key = :key"
        ),
        {"key": resource_key_for_project(harness.project_id)},
    )


async def _attempt_rows(harness: Harness) -> list[RuntimeMaterializationAttemptRow]:
    async with harness.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(RuntimeMaterializationAttemptRow)
                    .where(
                        RuntimeMaterializationAttemptRow.project_id == harness.project_id
                    )
                    .order_by(RuntimeMaterializationAttemptRow.generation)
                )
            ).all()
        )


async def _audit_rows(harness: Harness) -> list[RuntimeMaterializationAuditRow]:
    async with harness.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(RuntimeMaterializationAuditRow).where(
                        RuntimeMaterializationAuditRow.project_id == harness.project_id
                    )
                )
            ).all()
        )


# --------------------------------------------------------------------------- #
# The empty plan still owns a generation
# --------------------------------------------------------------------------- #


async def test_empty_plan_advances_the_head_and_writes_attempt_and_audit(harness) -> None:
    outcome = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert outcome is MaterializationApplyOutcome.APPLIED
    assert await _head_generation(harness) == 1
    attempts = await _attempt_rows(harness)
    assert len(attempts) == 1
    assert attempts[0].generation == 1 and attempts[0].phase == "applied"
    assert attempts[0].applied_revision_id is not None
    events = [row.event for row in await _audit_rows(harness)]
    # started and finished are written in the same transaction as the attempt.
    assert events.count("runtime.materialization.started") == 1
    assert events.count("runtime.materialization.finished") == 1


async def test_repeat_with_the_same_key_returns_the_stored_receipt(harness) -> None:
    first = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    second = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert first is MaterializationApplyOutcome.APPLIED
    assert second is MaterializationApplyOutcome.REPLAY
    assert await _head_generation(harness) == 1
    attempts = await _attempt_rows(harness)
    assert len(attempts) == 1, "a replay must not claim a second generation"
    assert attempts[0].outcome == "applied"


async def test_changed_plan_at_a_consumed_generation_conflicts(harness) -> None:
    await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    changed = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.OTHER_SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert changed is MaterializationApplyOutcome.CONFLICT
    assert await _head_generation(harness) == 1
    attempts = await _attempt_rows(harness)
    assert len(attempts) == 1
    # The stored receipt was never rewritten by the changed plan: its persisted
    # digest is still the one that was applied.
    assert attempt_digests(attempts[0].diagnostics) is not None
    assert attempt_digests(attempts[0].diagnostics).snapshot_digest == (
        doubles.SNAPSHOT_DIGEST
    )


async def test_late_plan_with_an_older_generation_is_refused(harness) -> None:
    await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    # A second, legitimately newer plan advances the head again.
    third = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=2,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert third is MaterializationApplyOutcome.APPLIED
    assert await _head_generation(harness) == 2
    # The "late" caller still believes generation 2 is next; it is not.
    late = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=2,
        snapshot_digest=doubles.digest("snapshot:late"),
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert late is MaterializationApplyOutcome.CONFLICT
    assert await _head_generation(harness) == 2


# --------------------------------------------------------------------------- #
# A populated plan cannot be acknowledged without an effect boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "harness",
    [HarnessOptions(require_grounding=False)],
    indirect=True,
)
async def test_populated_plan_without_an_effect_port_is_blocked(harness) -> None:
    outcome = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(doubles.operation(),),
    )
    assert outcome is MaterializationApplyOutcome.BLOCKED
    assert await _head_generation(harness) in (None, 0)
    assert await _attempt_rows(harness) == []
    assert await _audit_rows(harness) == []


# --------------------------------------------------------------------------- #
# Audit failure rolls the whole apply back
# --------------------------------------------------------------------------- #


async def test_audit_failure_rolls_back_generation_attempt_and_head(
    harness, monkeypatch
) -> None:
    async def _explode(self, *, session, transition):  # noqa: ANN001 - protocol shape
        raise AuditWriteRejected("audit_write_failed")

    monkeypatch.setattr(SqlAlchemyRuntimeAuditWriter, "append_transition", _explode)
    outcome = await harness.port.apply_cas(
        project_id=harness.project_id,
        expected_generation=1,
        snapshot_digest=doubles.SNAPSHOT_DIGEST,
        operation_set_digest=doubles.OPERATION_SET_DIGEST,
        operations=(),
    )
    assert outcome is MaterializationApplyOutcome.BLOCKED
    # The head row itself was created inside the aborted transaction, so no
    # generation, attempt, receipt or audit row survives the failure.
    assert await _head_generation(harness) in (None, 0)
    assert await _attempt_rows(harness) == []
    assert await _audit_rows(harness) == []


# --------------------------------------------------------------------------- #
# Rollback is a forward generation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _MatchingRollbackEffects:
    async def apply_rollback(
        self, *, project_id: str, target_generation: int, target_revision_id: str, fence
    ) -> OperationEffectReceipt:  # noqa: ANN001 - protocol shape
        return OperationEffectReceipt(
            operation_digest=canonical_digest(
                {"rollback_of_generation": target_generation}
            ),
            fence_token=fence.fence_token,
            fence_generation=fence.generation,
            succeeded=True,
            reason_code="rolled_back",
        )


@pytest.mark.parametrize(
    "harness",
    [HarnessOptions(require_grounding=False, rollback_effects=_MatchingRollbackEffects())],
    indirect=True,
)
async def test_rollback_claims_the_next_generation_and_links_its_target(harness) -> None:
    # Two applied generations, so a rollback target strictly precedes the head —
    # the command contract forbids "rolling back" to the current generation.
    for generation in (1, 2):
        assert (
            await harness.port.apply_cas(
                project_id=harness.project_id,
                expected_generation=generation,
                snapshot_digest=doubles.SNAPSHOT_DIGEST,
                operation_set_digest=doubles.OPERATION_SET_DIGEST,
                operations=(),
            )
            is MaterializationApplyOutcome.APPLIED
        )

    result = await harness.port.rollback(
        DurableRollbackCommand(
            project_id=harness.project_id,
            target_generation=1,
            expected_generation=2,
            approval_id="apr_applyport",
            idempotency_key="rollback-key",
            correlation_id="corr-rollback",
        )
    )
    assert result.outcome is MaterializationApplyOutcome.APPLIED
    assert result.reason_code == "rolled_back"
    assert await _head_generation(harness) == 3, "rollback advances, never rewinds"
    attempts = await _attempt_rows(harness)
    assert [row.generation for row in attempts] == [1, 2, 3]
    assert attempts[2].rollback_of_generation == 1
    assert attempts[2].outcome == "rolled_back"
    # History is intact: the rolled-back-from generations are untouched.
    assert [row.outcome for row in attempts[:2]] == ["applied", "applied"]


@pytest.mark.parametrize(
    "harness",
    [HarnessOptions(require_grounding=False, rollback_effects=_MatchingRollbackEffects())],
    indirect=True,
)
async def test_rollback_without_a_provable_approval_claims_nothing(harness) -> None:
    for generation in (1, 2):
        assert (
            await harness.port.apply_cas(
                project_id=harness.project_id,
                expected_generation=generation,
                snapshot_digest=doubles.SNAPSHOT_DIGEST,
                operation_set_digest=doubles.OPERATION_SET_DIGEST,
                operations=(),
            )
            is MaterializationApplyOutcome.APPLIED
        )
    harness.port.evidence = doubles.StaticEvidenceSource(approvals={})
    # The command shape is valid (target strictly precedes the head); only the
    # approval is missing, so the refusal comes from the port, before any claim.
    result = await harness.port.rollback(
        DurableRollbackCommand(
            project_id=harness.project_id,
            target_generation=1,
            expected_generation=2,
            approval_id="apr_unknown",
            idempotency_key="rollback-unapproved",
            correlation_id="corr-rollback",
        )
    )
    assert result.outcome is MaterializationApplyOutcome.BLOCKED
    assert result.reason_code == "rollback_approval_unverified"
    assert await _head_generation(harness) == 2
    assert [row.generation for row in await _attempt_rows(harness)] == [1, 2]


async def test_attempt_count_matches_generations(harness) -> None:
    """History is per generation, never one overwritten record per resource."""
    for generation in (1, 2, 3):
        outcome = await harness.port.apply_cas(
            project_id=harness.project_id,
            expected_generation=generation,
            snapshot_digest=doubles.SNAPSHOT_DIGEST,
            operation_set_digest=doubles.OPERATION_SET_DIGEST,
            operations=(),
        )
        assert outcome is MaterializationApplyOutcome.APPLIED
    attempts = await _attempt_rows(harness)
    assert [row.generation for row in attempts] == [1, 2, 3]
    async with harness.session_factory() as session:
        head = await session.scalar(
            select(RuntimeRevisionHeadRow).where(
                RuntimeRevisionHeadRow.resource_key
                == resource_key_for_project(harness.project_id)
            )
        )
    assert head is not None and head.generation == 3
    assert (
        await harness.connection.scalar(
            text(
                "SELECT count(*) FROM materialization_entries WHERE revision_id IN "
                "(SELECT id FROM project_runtime_revisions WHERE project_id = :project)"
            ),
            {"project": harness.project_id},
        )
    ) == 0, "an empty plan writes no materialization entries"
