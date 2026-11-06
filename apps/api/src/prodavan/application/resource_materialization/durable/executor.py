"""Atomic durable executor for the resource materialization apply path.

One call = one database transaction, in this order:

1. resolve the exact project runtime scope from the database;
2. open the transaction and lock the scope row;
3. resolve replay by idempotency key *before* any CAS — a replay returns the
   stored attempt, and the same key with a different plan digest is a conflict;
4. read the head and compare the observed generation (cheap, informative refusal);
5. compare-and-set the head: ``generation -> generation + 1`` with a fresh fence
   token. This is the resource-wide fence, so a plan with zero operations is
   fenced exactly like a populated one;
6. persist the immutable desired snapshot (revision) the generation advanced to;
7. insert the open attempt of that generation;
8. append the redacted ``started`` audit row;
9. run the effects under the fence;
10. close the attempt with its outcome and receipt digest, close the head with the
    applied digests, append the redacted ``finished`` audit row;
11. commit.

Any failure after step 5 rolls the transaction back, so no generation, attempt,
receipt or audit survives the failure. An audit failure in particular raises out
of the transaction and the caller receives ``BLOCKED`` / ``audit_write_failed``;
the same is true for an unexpected effect outcome (``FAILED`` with an unknown
effect, or ``CONFLICT`` for a stale fence).

The executor implements ``AtomicMaterializationApplyPort`` verbatim, so the
existing resolver path can use it without knowing about attempts, heads or audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)
from prodavan.application.resource_materialization.durable.audit import (
    AuditTransition,
    RuntimeAuditBinding,
    SqlAlchemyRuntimeAuditWriter,
)
from prodavan.application.resource_materialization.durable.contracts import (
    ApplyPlan,
    AttemptLedgerConflict,
    AttemptOutcome,
    AttemptPhase,
    AttemptSnapshot,
    AuditWriteRejected,
    DurableApplyResult,
    DurableRollbackCommand,
    HeadPhase,
    HeadSnapshot,
    RedactedDiagnostic,
    RevisionScopeUnavailable,
    apply_idempotency_key,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.heads import (
    SqlAlchemyRevisionHeadStore,
    new_fence_token,
)
from prodavan.application.resource_materialization.durable.ports import (
    DurableApplyGate,
    DurableClockPort,
    SystemUtcClock,
)
from prodavan.application.resource_materialization.durable.attempts import (
    SqlAlchemyAttemptStore,
)
from prodavan.application.resource_materialization.durable.effects import (
    OperationEffectReceipt,
    OperationFence,
    ResourceOperationEffectPort,
    RollbackEffectPort,
    resource_operation_digest,
)
from prodavan.application.resource_materialization.durable.revisions import (
    RevisionApplicationScope,
    SqlAlchemyRevisionSnapshotStore,
)
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

_STARTED = "runtime.materialization.started"
_FINISHED = "runtime.materialization.finished"
_REPLAYED = "runtime.materialization.replay"
_EVENT_ROLLBACK = "runtime.materialization.rolled_back"


@dataclass
class DurableMaterializationExecutor:
    """Atomic executor shared by the resolver path and the runtime facade."""

    session_factory: async_sessionmaker[AsyncSession]
    gate: DurableApplyGate
    effects: ResourceOperationEffectPort | None = None
    rollback_effects: RollbackEffectPort | None = None
    clock: DurableClockPort | None = None

    def __post_init__(self) -> None:
        self._heads = SqlAlchemyRevisionHeadStore()
        self._attempts = SqlAlchemyAttemptStore()
        self._revisions = SqlAlchemyRevisionSnapshotStore()
        if self.clock is None:
            self.clock = SystemUtcClock()

    # ------------------------------------------------------------------ ports

    async def apply_cas(
        self,
        *,
        project_id: str,
        expected_generation: int,
        snapshot_digest: str,
        operation_set_digest: str,
        operations: tuple[ResourceOperation, ...],
        correlation_id: str | None = None,
        created_by: str | None = None,
    ) -> MaterializationApplyOutcome:
        """``AtomicMaterializationApplyPort`` implementation."""
        result = await self.execute(
            ApplyPlan(
                project_id=project_id,
                expected_generation=expected_generation,
                snapshot_digest=snapshot_digest,
                operation_set_digest=operation_set_digest,
                operations=tuple(operations),
                correlation_id=correlation_id or _default_correlation(project_id, expected_generation),
                idempotency_key=apply_idempotency_key(
                    project_id=project_id,
                    expected_generation=expected_generation,
                    snapshot_digest=snapshot_digest,
                    operation_set_digest=operation_set_digest,
                ),
                created_by=created_by,
            )
        )
        return result.outcome

    # --------------------------------------------------------------- commands

    async def execute(self, plan: ApplyPlan) -> DurableApplyResult:
        """Run one fenced apply. Never raises for a refusal; only for programming errors."""
        if not isinstance(plan, ApplyPlan):
            raise TypeError("a typed ApplyPlan is required")
        if self.gate is None:
            return _blocked("apply_gate_missing")
        now = await self._now()
        if isinstance(now, DurableApplyResult):
            return now
        try:
            blockers = await self.gate.blockers(plan=plan, at=now)
        except Exception:
            return _blocked("apply_gate_error")
        blockers = _sanitize(blockers)
        if blockers:
            return _blocked(list(blockers))
        if plan.operations and self.effects is None:
            # Fail closed: a populated plan without an effect boundary would
            # otherwise be acknowledged as applied with nothing written.
            return _blocked("resource_effect_port_missing")
        try:
            async with self.session_factory() as session, session.begin():
                return await self._execute_in_transaction(session, plan=plan, at=now)
        except RevisionScopeUnavailable as exc:
            return _blocked(str(exc) or "project_runtime_scope_unavailable")
        except _StaleFence:
            return _result(MaterializationApplyOutcome.CONFLICT, "stale_generation_fence")
        except AttemptLedgerConflict as exc:
            return _result(MaterializationApplyOutcome.CONFLICT, str(exc) or "attempt_conflict")
        except AuditWriteRejected:
            return _result(MaterializationApplyOutcome.BLOCKED, "audit_write_failed")
        except IntegrityError:
            return _result(MaterializationApplyOutcome.CONFLICT, "materialization_unique_conflict")
        except SQLAlchemyError:
            return _result(MaterializationApplyOutcome.BLOCKED, "persistence_error")

    async def rollback(self, command: DurableRollbackCommand) -> DurableApplyResult:
        """Approved rollback to an immutable prior generation.

        A rollback never rewrites history and never decrements a generation: it
        claims the next generation on the head, links ``rollback_of_generation``
        and asks the effect adapter to reinstate the target revision.
        """
        if not isinstance(command, DurableRollbackCommand):
            raise TypeError("a typed DurableRollbackCommand is required")
        if not command.approval_id:
            return _result(MaterializationApplyOutcome.BLOCKED, "rollback_approval_missing")
        if self.rollback_effects is None:
            return _result(MaterializationApplyOutcome.BLOCKED, "rollback_effect_port_missing")
        now = await self._now()
        if isinstance(now, DurableApplyResult):
            return now
        try:
            async with self.session_factory() as session, session.begin():
                return await self._rollback_in_transaction(session, command=command, at=now)
        except RevisionScopeUnavailable as exc:
            return _result(MaterializationApplyOutcome.BLOCKED, str(exc) or "project_runtime_scope_unavailable")
        except _StaleFence:
            return _result(MaterializationApplyOutcome.CONFLICT, "stale_generation_fence")
        except AttemptLedgerConflict as exc:
            return _result(MaterializationApplyOutcome.CONFLICT, str(exc) or "attempt_conflict")
        except AuditWriteRejected:
            return _result(MaterializationApplyOutcome.BLOCKED, "audit_write_failed")
        except IntegrityError:
            return _result(MaterializationApplyOutcome.CONFLICT, "materialization_unique_conflict")
        except SQLAlchemyError:
            return _result(MaterializationApplyOutcome.BLOCKED, "persistence_error")

    async def replay(self, *, project_id: str, idempotency_key: str) -> DurableApplyResult | None:
        """Return the stored attempt for a key without claiming anything."""
        resource_key = resource_key_for_project(project_id)
        try:
            async with self.session_factory() as session:
                return await self._replay_in_session(
                    session, resource_key=resource_key, idempotency_key=idempotency_key
                )
        except SQLAlchemyError:
            return _result(MaterializationApplyOutcome.BLOCKED, "replay_read_failed")

    async def history(self, *, project_id: str, limit: int = 50) -> tuple[AttemptSnapshot, ...]:
        async with self.session_factory() as session:
            return await self._attempts.history(
                session, resource_key=resource_key_for_project(project_id), limit=limit
            )

    async def project_generation(self, *, project_id: str) -> int:
        async with self.session_factory() as session:
            return await self._heads.project_generation(session, project_id=project_id)

    # ------------------------------------------------------------ transaction

    async def _execute_in_transaction(
        self, session: AsyncSession, *, plan: ApplyPlan, at: datetime
    ) -> DurableApplyResult:
        scope = await self._lock_scope(session, plan.project_id)
        head = await self._heads.ensure(
            session, project_id=scope.project_id, resource_key=plan.resource_key
        )

        replayed = await self._replay_locked(session, plan=plan, head=head)
        if replayed is not None:
            return replayed

        if head.generation != plan.observed_generation:
            return _result(
                MaterializationApplyOutcome.CONFLICT,
                "stale_generation_cas",
                head=head,
                diagnostics=(RedactedDiagnostic("apply", "stale_generation_cas"),),
            )

        fence_token = new_fence_token()
        claimed = await self._heads.claim(
            session,
            resource_key=plan.resource_key,
            project_id=plan.project_id,
            expected_generation=head.generation,
            fence_token=fence_token,
        )
        if claimed is None or claimed.generation != plan.expected_generation:
            raise _StaleFence()

        revision_id = await self._revisions.persist_desired(
            session,
            scope=scope,
            plan=plan,
            revision_number=plan.expected_generation,
        )

        try:
            attempt = await self._attempts.open(
                session, plan=plan, fence_token=fence_token, revision_id=revision_id
            )
        except AttemptLedgerConflict:
            # A concurrent claim of the same generation/key already exists; the
            # transaction rolls back the head advance too.
            raise _StaleFence() from None

        binding = RuntimeAuditBinding(
            project_id=scope.project_id,
            resource_key=plan.resource_key,
            correlation_id=plan.correlation_id,
            attempt_id=attempt.attempt_id,
        )
        audit = SqlAlchemyRuntimeAuditWriter(binding=binding)
        try:
            await audit.append_transition(
                session=session,
                transition=audit.transition(
                    event=_STARTED,
                    outcome="started",
                    reason_code="apply_claim_requested",
                    plan_digest=plan.plan_digest,
                    generation=attempt.generation,
                ),
            )
        except AuditWriteRejected:
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise AuditWriteRejected("audit_write_failed") from exc

        fence = OperationFence(
            resource_key=plan.resource_key,
            generation=attempt.generation,
            fence_token=fence_token,
        )
        completed: list[ResourceOperation] = []
        diagnostics: list[RedactedDiagnostic] = []
        fatal = False
        for operation in plan.operations:
            try:
                receipt = await self.effects.apply_operation(
                    project_id=plan.project_id, operation=operation, fence=fence
                )
            except Exception:
                diagnostics.append(RedactedDiagnostic("apply", "runtime_effect_unknown"))
                fatal = True
                break
            if not _receipt_matches(receipt, operation=operation, fence=fence):
                diagnostics.append(RedactedDiagnostic("apply", "operation_receipt_or_fence_mismatch"))
                fatal = True
                break
            if receipt.succeeded:
                completed.append(operation)
                continue
            diagnostics.append(RedactedDiagnostic("apply", _safe_code(receipt.reason_code)))
            if receipt.effect_unknown or operation.required:
                fatal = True
                break
            # A known optional failure stays a warning and does not stop the plan.

        if fatal:
            await self._compensate(
                session, plan=plan, fence=fence, completed=completed, diagnostics=diagnostics
            )
            attempt = await self._attempts.finish(
                session,
                attempt=attempt,
                outcome=AttemptOutcome.FAILED,
                fence_token=fence_token,
                applied_revision_id=None,
                reason_code=_first_reason(diagnostics, "runtime_apply_failed"),
                diagnostics=tuple(diagnostics),
            )
            if attempt is None:
                raise _StaleFence()
            head = await self._heads.finish(
                session,
                resource_key=plan.resource_key,
                fence_token=fence_token,
                generation=attempt.generation,
                phase=HeadPhase.FAILED,
                diagnostics=tuple(diagnostics),
            )
            if head is None:
                raise _StaleFence()
            await self._audit(
                session,
                binding=binding,
                transition=audit.transition(
                    event=_FINISHED,
                    outcome="failed",
                    reason_code=_first_reason(diagnostics, "runtime_apply_failed"),
                    plan_digest=plan.plan_digest,
                    generation=attempt.generation,
                    diagnostics=tuple(item.reason_code for item in diagnostics),
                ),
            )
            return _result(
                MaterializationApplyOutcome.FAILED,
                "required_operation_failed",
                attempt=attempt,
                head=head,
                diagnostics=tuple(diagnostics),
            )

        outcome = (
            AttemptOutcome.ROLLED_BACK if plan.rollback_of_generation is not None else AttemptOutcome.APPLIED
        )
        attempt = await self._attempts.finish(
            session,
            attempt=attempt,
            outcome=outcome,
            fence_token=fence_token,
            applied_revision_id=revision_id,
            reason_code="rolled_back" if outcome is AttemptOutcome.ROLLED_BACK else "applied",
            diagnostics=tuple(diagnostics),
        )
        if attempt is None:
            raise _StaleFence()
        head = await self._heads.finish(
            session,
            resource_key=plan.resource_key,
            fence_token=fence_token,
            generation=attempt.generation,
            phase=HeadPhase.ROLLED_BACK if outcome is AttemptOutcome.ROLLED_BACK else HeadPhase.APPLIED,
            diagnostics=tuple(diagnostics),
            applied_desired_digest=plan.snapshot_digest,
            ownership_digest=plan.operation_set_digest,
            revision_id=revision_id,
            applied_revision_id=revision_id,
        )
        if head is None:
            raise _StaleFence()
        await self._audit(
            session,
            binding=binding,
            transition=audit.transition(
                event=_FINISHED,
                outcome=outcome.value,
                reason_code="rolled_back" if outcome is AttemptOutcome.ROLLED_BACK else "applied",
                plan_digest=plan.plan_digest,
                generation=attempt.generation,
                diagnostics=tuple(item.reason_code for item in diagnostics),
            ),
        )
        return _result(
            MaterializationApplyOutcome.APPLIED,
            "rolled_back" if outcome is AttemptOutcome.ROLLED_BACK else "applied",
            attempt=attempt,
            head=head,
        )

    async def _rollback_in_transaction(
        self, session: AsyncSession, *, command: DurableRollbackCommand, at: datetime
    ) -> DurableApplyResult:
        scope = await self._lock_scope(session, command.project_id)
        head = await self._heads.ensure(
            session, project_id=scope.project_id, resource_key=command.resource_key
        )
        if head.generation != command.expected_generation:
            return _result(
                MaterializationApplyOutcome.BLOCKED,
                "generation_conflict",
                head=head,
                diagnostics=(RedactedDiagnostic("rollback", "generation_conflict"),),
            )
        target = await self._attempts.find_generation(
            session, resource_key=command.resource_key, generation=command.target_generation
        )
        if (
            target is None
            or target.phase not in {AttemptPhase.APPLIED, AttemptPhase.ROLLED_BACK}
            or target.applied_revision_id is None
        ):
            return _result(
                MaterializationApplyOutcome.BLOCKED,
                "rollback_target_not_found",
                head=head,
                diagnostics=(RedactedDiagnostic("rollback", "rollback_target_not_found"),),
            )
        plan = ApplyPlan(
            project_id=command.project_id,
            expected_generation=command.expected_generation + 1,
            snapshot_digest=(
                target.snapshot_digest
                or canonical_digest({"rollback_target": command.target_generation})
            ),
            operation_set_digest=(
                target.operation_set_digest
                or canonical_digest({"rollback_target_operations": command.target_generation})
            ),
            operations=(),
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            rollback_of_generation=command.target_generation,
            created_by=command.created_by,
            source_event="runtime-materialization.rollback",
        )
        fence_token = new_fence_token()
        claimed = await self._heads.claim(
            session,
            resource_key=command.resource_key,
            project_id=command.project_id,
            expected_generation=head.generation,
            fence_token=fence_token,
        )
        if claimed is None:
            raise _StaleFence()
        revision_id = await self._revisions.persist_desired(
            session, scope=scope, plan=plan, revision_number=plan.expected_generation
        )
        try:
            attempt = await self._attempts.open(
                session, plan=plan, fence_token=fence_token, revision_id=revision_id
            )
        except AttemptLedgerConflict:
            raise _StaleFence() from None
        binding = RuntimeAuditBinding(
            project_id=scope.project_id,
            resource_key=command.resource_key,
            correlation_id=command.correlation_id,
            attempt_id=attempt.attempt_id,
        )
        audit = SqlAlchemyRuntimeAuditWriter(binding=binding)
        try:
            await audit.append_transition(
                session=session,
                transition=audit.transition(
                    event=_EVENT_ROLLBACK,
                    outcome="started",
                    reason_code="rollback_claim_requested",
                    plan_digest=plan.plan_digest,
                    generation=attempt.generation,
                ),
            )
        except (IntegrityError, SQLAlchemyError) as exc:
            raise AuditWriteRejected("audit_write_failed") from exc

        fence = OperationFence(
            resource_key=command.resource_key,
            generation=attempt.generation,
            fence_token=fence_token,
        )
        try:
            receipt = await self.rollback_effects.apply_rollback(
                project_id=command.project_id,
                target_generation=command.target_generation,
                target_revision_id=target.applied_revision_id,
                fence=fence,
            )
        except Exception:
            receipt = None
        # A rollback receipt proves *shape*, not *success*: an adapter is allowed
        # to answer with a well-formed receipt whose ``succeeded`` is False and a
        # reason code.  Checking only the digest would record a refused rollback as
        # an applied generation, which is the worst possible failure for this path.
        if (
            receipt is None
            or not receipt.succeeded
            or not _receipt_matches(
                receipt,
                operation_digest=canonical_digest(
                    {"rollback_of_generation": command.target_generation}
                ),
                fence=fence,
            )
        ):
            attempt = await self._attempts.finish(
                session,
                attempt=attempt,
                outcome=AttemptOutcome.FAILED,
                fence_token=fence_token,
                applied_revision_id=None,
                reason_code="rollback_effect_unknown",
                diagnostics=(RedactedDiagnostic("rollback", "rollback_effect_unknown"),),
            )
            if attempt is None:
                raise _StaleFence()
            head = await self._heads.finish(
                session,
                resource_key=command.resource_key,
                fence_token=fence_token,
                generation=attempt.generation,
                phase=HeadPhase.FAILED,
                diagnostics=(RedactedDiagnostic("rollback", "rollback_effect_unknown"),),
            )
            if head is None:
                raise _StaleFence()
            await self._audit(
                session,
                binding=binding,
                transition=audit.transition(
                    event=_EVENT_ROLLBACK,
                    outcome="failed",
                    reason_code="rollback_effect_unknown",
                    plan_digest=plan.plan_digest,
                    generation=attempt.generation,
                ),
            )
            return _result(
                MaterializationApplyOutcome.FAILED,
                "rollback_effect_unknown",
                attempt=attempt,
                head=head,
            )

        attempt = await self._attempts.finish(
            session,
            attempt=attempt,
            outcome=AttemptOutcome.ROLLED_BACK,
            fence_token=fence_token,
            applied_revision_id=target.applied_revision_id,
            reason_code="rolled_back",
        )
        if attempt is None:
            raise _StaleFence()
        head = await self._heads.finish(
            session,
            resource_key=command.resource_key,
            fence_token=fence_token,
            generation=attempt.generation,
            phase=HeadPhase.ROLLED_BACK,
            applied_desired_digest=plan.snapshot_digest,
            ownership_digest=plan.operation_set_digest,
            revision_id=revision_id,
            applied_revision_id=target.applied_revision_id,
        )
        if head is None:
            raise _StaleFence()
        await self._audit(
            session,
            binding=binding,
            transition=audit.transition(
                event=_EVENT_ROLLBACK,
                outcome="rolled_back",
                reason_code="rolled_back",
                plan_digest=plan.plan_digest,
                generation=attempt.generation,
            ),
        )
        return _result(
            MaterializationApplyOutcome.APPLIED,
            "rolled_back",
            attempt=attempt,
            head=head,
        )

    # ---------------------------------------------------------------- helpers

    async def _replay_locked(
        self, session: AsyncSession, *, plan: ApplyPlan, head: HeadSnapshot
    ) -> DurableApplyResult | None:
        stored = await self._attempts.find(
            session, resource_key=plan.resource_key, idempotency_key=plan.idempotency_key
        )
        if stored is None:
            return None
        if stored.plan_digest != plan.plan_digest:
            await self._audit_existing(session, stored=stored, plan=plan, outcome="conflict",
                                       reason_code="idempotency_key_reused")
            return _result(
                MaterializationApplyOutcome.CONFLICT,
                "idempotency_key_reused",
                attempt=stored,
                head=head,
                diagnostics=(RedactedDiagnostic("apply", "idempotency_key_reused"),),
            )
        outcome = (
            MaterializationApplyOutcome.REPLAY
            if stored.granted_authority
            else MaterializationApplyOutcome.FAILED
        )
        await self._audit_existing(
            session,
            stored=stored,
            plan=plan,
            outcome=outcome.value,
            reason_code="idempotent_replay",
        )
        return _result(
            outcome,
            "idempotent_replay" if outcome is MaterializationApplyOutcome.REPLAY else "replayed_failure",
            attempt=stored,
            head=head,
            replayed=True,
        )

    async def _replay_in_session(
        self, session: AsyncSession, *, resource_key: str, idempotency_key: str
    ) -> DurableApplyResult | None:
        stored = await self._attempts.find(
            session, resource_key=resource_key, idempotency_key=idempotency_key, lock=False
        )
        if stored is None:
            return None
        outcome = (
            MaterializationApplyOutcome.REPLAY
            if stored.granted_authority
            else MaterializationApplyOutcome.FAILED
        )
        head = await self._heads.read(session, resource_key=resource_key)
        return _result(
            outcome,
            "idempotent_replay" if outcome is MaterializationApplyOutcome.REPLAY else "replayed_failure",
            attempt=stored,
            head=head,
            replayed=True,
        )

    async def _audit_existing(
        self,
        session: AsyncSession,
        *,
        stored: AttemptSnapshot,
        plan: ApplyPlan,
        outcome: str,
        reason_code: str,
    ) -> None:
        binding = RuntimeAuditBinding(
            project_id=stored.project_id,
            resource_key=stored.resource_key,
            correlation_id=plan.correlation_id,
            attempt_id=stored.attempt_id,
        )
        writer = SqlAlchemyRuntimeAuditWriter(binding=binding)
        await self._audit(
            session,
            binding=binding,
            transition=writer.transition(
                event=_REPLAYED,
                outcome=outcome,
                reason_code=reason_code,
                plan_digest=stored.plan_digest,
                generation=stored.generation,
            ),
        )

    @staticmethod
    async def _audit(
        session: AsyncSession, *, binding: RuntimeAuditBinding, transition: AuditTransition
    ) -> None:
        writer = SqlAlchemyRuntimeAuditWriter(binding=binding)
        try:
            await writer.append_transition(session=session, transition=transition)
        except AuditWriteRejected:
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise AuditWriteRejected("audit_write_failed") from exc

    async def _compensate(
        self,
        session: AsyncSession,
        *,
        plan: ApplyPlan,
        fence: OperationFence,
        completed: list[ResourceOperation],
        diagnostics: list[RedactedDiagnostic],
    ) -> None:
        if self.effects is None:
            return
        for operation in reversed(completed):
            try:
                receipt = await self.effects.compensate_operation(
                    project_id=plan.project_id, operation=operation, fence=fence
                )
            except Exception:
                diagnostics.append(RedactedDiagnostic("compensate", "compensation_outcome_unknown"))
                continue
            if not _receipt_matches(receipt, operation=operation, fence=fence) or not receipt.succeeded:
                diagnostics.append(RedactedDiagnostic("compensate", "compensation_rejected"))

    async def _lock_scope(self, session: AsyncSession, project_id: str) -> RevisionApplicationScope:
        if not isinstance(project_id, str) or not project_id.strip() or any(ch.isspace() for ch in project_id):
            raise RevisionScopeUnavailable("project_scope_missing")
        row = await session.scalar(
            select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()
        )
        if row is None:
            raise RevisionScopeUnavailable("project_not_found")
        if not row.company_id or not row.cabinet_id:
            raise RevisionScopeUnavailable("project_scope_incomplete")
        pod_ids = (
            await session.scalars(
                select(ProjectPodRow.id).where(ProjectPodRow.project_id == project_id)
            )
        ).all()
        if len(set(pod_ids)) != 1:
            raise RevisionScopeUnavailable(
                "project_runtime_pod_missing" if not pod_ids else "project_runtime_pod_ambiguous"
            )
        return RevisionApplicationScope(
            project_id=row.id,
            company_id=row.company_id,
            cabinet_id=row.cabinet_id,
            pod_id=pod_ids[0],
        )

    async def _now(self) -> datetime | DurableApplyResult:
        if self.clock is None:
            return _blocked("apply_clock_port_missing")
        try:
            now = await self.clock.now()
        except Exception:
            return _blocked("apply_clock_port_error")
        if now.tzinfo is None or now.utcoffset() is None:
            return _blocked("apply_clock_not_timezone_aware")
        return now


class _StaleFence(Exception):
    """Internal signal: the head/attempt fence no longer matches."""


def _receipt_matches(
    receipt: object, *, fence: OperationFence, operation: ResourceOperation | None = None,
    operation_digest: str | None = None,
) -> bool:
    if not isinstance(receipt, OperationEffectReceipt):
        return False
    expected = operation_digest if operation_digest is not None else (
        resource_operation_digest(operation) if operation is not None else ""
    )
    return receipt.matches(operation_digest=expected, fence=fence)


def _first_reason(diagnostics: list[RedactedDiagnostic], fallback: str) -> str:
    for item in diagnostics:
        if item.reason_code:
            return item.reason_code
    return fallback


def _safe_code(value: object, fallback: str = "operation_rejected") -> str:
    import re

    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", value):
        return value
    return fallback


def _sanitize(blockers: object) -> tuple[str, ...]:
    if not isinstance(blockers, (list, tuple)):
        return ("apply_gate_response_invalid",)
    return tuple(_safe_code(item, "apply_blocked") for item in blockers)


def _default_correlation(project_id: str, expected_generation: int) -> str:
    return f"resource-apply:{project_id}:{expected_generation}"


def _result(
    outcome: MaterializationApplyOutcome,
    reason_code: str,
    *,
    attempt: AttemptSnapshot | None = None,
    head: HeadSnapshot | None = None,
    replayed: bool = False,
    diagnostics: tuple[RedactedDiagnostic, ...] = (),
) -> DurableApplyResult:
    return DurableApplyResult(
        outcome=outcome,
        reason_code=reason_code,
        attempt=attempt,
        head=head,
        replayed=replayed,
        diagnostics=diagnostics,
    )


def _blocked(reason: str | list[str]) -> DurableApplyResult:
    codes = (reason,) if isinstance(reason, str) else tuple(reason)
    safe = tuple(_safe_code(code) for code in codes) or ("apply_blocked",)
    return DurableApplyResult(
        outcome=MaterializationApplyOutcome.BLOCKED,
        reason_code=safe[0],
        diagnostics=tuple(RedactedDiagnostic("preflight", code) for code in safe),
    )


__all__ = ["DurableMaterializationExecutor"]
