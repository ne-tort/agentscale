"""Durable attempt history over ``runtime_materialization_attempts``.

One row per claimed generation, so a resource keeps its whole history instead of
overwriting a single record:

* ``UNIQUE (resource_key, idempotency_key)`` — a replay returns the stored attempt;
  the same key with another ``plan_digest`` is a conflict and the row is untouched;
* ``UNIQUE (resource_key, generation)`` — one claim per generation, never reused.

Rows are inserted as ``phase = 'applying'`` with a NULL outcome/receipt and become
terminal exactly once, under the same fence token, with a receipt digest over
(resource key, idempotency key, plan digest, generation, outcome). Update
statements are therefore always conditional on ``phase = 'applying'``: a finished
receipt is append-only by construction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.persistence.models.runtime_revisions import (
    RuntimeMaterializationAttemptRow,
)

from .contracts import (
    ApplyPlan,
    AttemptLedgerConflict,
    AttemptOutcome,
    AttemptPhase,
    AttemptSnapshot,
    RedactedDiagnostic,
    attempt_diagnostics_payload,
    attempt_digests,
    attempt_receipt_digest,
    diagnostic_payload,
    safe_reading,
)


class SqlAlchemyAttemptStore:
    """Session-bound attempt ledger; the caller owns the transaction."""

    async def find(
        self,
        session: AsyncSession,
        *,
        resource_key: str,
        idempotency_key: str,
        lock: bool = True,
    ) -> AttemptSnapshot | None:
        query = select(RuntimeMaterializationAttemptRow).where(
            RuntimeMaterializationAttemptRow.resource_key == resource_key,
            RuntimeMaterializationAttemptRow.idempotency_key == idempotency_key,
        )
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query)
        return _to_attempt(row) if row is not None else None

    async def find_generation(
        self, session: AsyncSession, *, resource_key: str, generation: int
    ) -> AttemptSnapshot | None:
        row = await session.scalar(
            select(RuntimeMaterializationAttemptRow).where(
                RuntimeMaterializationAttemptRow.resource_key == resource_key,
                RuntimeMaterializationAttemptRow.generation == generation,
            )
        )
        return _to_attempt(row) if row is not None else None

    async def history(
        self, session: AsyncSession, *, resource_key: str, limit: int = 50
    ) -> tuple[AttemptSnapshot, ...]:
        """Newest-first attempt history of one resource (never a single record)."""

        if limit < 1 or limit > 500:
            raise ValueError("history limit must be between 1 and 500")
        rows = (
            await session.scalars(
                select(RuntimeMaterializationAttemptRow)
                .where(RuntimeMaterializationAttemptRow.resource_key == resource_key)
                .order_by(RuntimeMaterializationAttemptRow.generation.desc())
                .limit(limit)
            )
        ).all()
        return tuple(_to_attempt(row) for row in rows)

    async def history_for_revision(
        self, session: AsyncSession, *, revision_id: str
    ) -> tuple[AttemptSnapshot, ...]:
        rows = (
            await session.scalars(
                select(RuntimeMaterializationAttemptRow)
                .where(RuntimeMaterializationAttemptRow.revision_id == revision_id)
                .order_by(RuntimeMaterializationAttemptRow.generation)
            )
        ).all()
        return tuple(_to_attempt(row) for row in rows)

    async def open(
        self,
        session: AsyncSession,
        *,
        plan: ApplyPlan,
        fence_token: str,
        revision_id: str | None,
        diagnostics: tuple[RedactedDiagnostic, ...] = (),
    ) -> AttemptSnapshot:
        """Insert the open attempt of the generation the head already allocated."""
        if not fence_token.strip():
            raise ValueError("fence_token is required")
        payload = attempt_diagnostics_payload(
            snapshot_digest=plan.snapshot_digest,
            operation_set_digest=plan.operation_set_digest,
            operation_count=len(plan.operations),
            rollback_of_generation=plan.rollback_of_generation,
            diagnostics=diagnostics,
        )
        try:
            async with session.begin_nested():
                row = RuntimeMaterializationAttemptRow(
                    project_id=plan.project_id,
                    revision_id=revision_id or plan.revision_id,
                    applied_revision_id=None,
                    resource_key=plan.resource_key,
                    idempotency_key=plan.idempotency_key,
                    plan_digest=plan.plan_digest,
                    generation=plan.expected_generation,
                    observed_generation=plan.observed_generation,
                    phase=AttemptPhase.APPLYING.value,
                    outcome=None,
                    receipt_digest=None,
                    fence_token=fence_token,
                    rollback_of_generation=plan.rollback_of_generation,
                    reason_code="apply_claimed",
                    diagnostics=payload,
                )
                session.add(row)
                await session.flush()
        except IntegrityError as exc:
            raise AttemptLedgerConflict("attempt_key_or_generation_conflict") from exc
        stored = await session.scalar(
            select(RuntimeMaterializationAttemptRow).where(
                RuntimeMaterializationAttemptRow.id == row.id
            )
        )
        if stored is None:
            raise AttemptLedgerConflict("attempt_row_missing_after_insert")
        return _to_attempt(stored)

    async def finish(
        self,
        session: AsyncSession,
        *,
        attempt: AttemptSnapshot,
        outcome: AttemptOutcome,
        fence_token: str,
        applied_revision_id: str | None,
        reason_code: str,
        diagnostics: tuple[RedactedDiagnostic, ...] = (),
    ) -> AttemptSnapshot | None:
        """Close one open attempt exactly once; a stale row changes nothing."""
        if attempt.is_terminal:
            raise ValueError("only an open attempt can be finished")
        if outcome in {AttemptOutcome.APPLIED, AttemptOutcome.ROLLED_BACK}:
            if applied_revision_id is None:
                raise ValueError("a positive outcome requires its applied revision link")
        elif applied_revision_id is not None:
            raise ValueError("a failed attempt cannot link an applied revision")
        receipt = attempt_receipt_digest(
            resource_key=attempt.resource_key,
            idempotency_key=attempt.idempotency_key,
            plan_digest=attempt.plan_digest,
            generation=attempt.generation,
            outcome=outcome,
        )
        row = await session.scalar(
            select(RuntimeMaterializationAttemptRow)
            .where(RuntimeMaterializationAttemptRow.id == attempt.attempt_id)
            .with_for_update()
        )
        if row is None or row.phase != AttemptPhase.APPLYING.value:
            return None
        if row.fence_token != fence_token or row.generation != attempt.generation:
            return None
        stored_diagnostics = _diagnostics_with_reason(row.diagnostics, diagnostics)
        result = await session.execute(
            update(RuntimeMaterializationAttemptRow)
            .where(
                RuntimeMaterializationAttemptRow.id == attempt.attempt_id,
                RuntimeMaterializationAttemptRow.phase == AttemptPhase.APPLYING.value,
                RuntimeMaterializationAttemptRow.fence_token == fence_token,
                RuntimeMaterializationAttemptRow.generation == attempt.generation,
            )
            .values(
                phase=_phase_for(outcome).value,
                outcome=outcome.value,
                receipt_digest=receipt,
                applied_revision_id=applied_revision_id,
                reason_code=reason_code,
                diagnostics=stored_diagnostics,
                finished_at=func.now(),
            )
        )
        if result.rowcount != 1:
            return None
        stored = await session.scalar(
            select(RuntimeMaterializationAttemptRow).where(
                RuntimeMaterializationAttemptRow.id == attempt.attempt_id
            )
        )
        return _to_attempt(stored) if stored is not None else None

    async def record_uncertain(
        self,
        session: AsyncSession,
        *,
        attempt: AttemptSnapshot,
        fence_token: str,
        diagnostics: tuple[RedactedDiagnostic, ...],
    ) -> bool:
        """Attach diagnostics while the attempt is still open (same transaction)."""
        result = await session.execute(
            update(RuntimeMaterializationAttemptRow)
            .where(
                RuntimeMaterializationAttemptRow.id == attempt.attempt_id,
                RuntimeMaterializationAttemptRow.phase == AttemptPhase.APPLYING.value,
                RuntimeMaterializationAttemptRow.fence_token == fence_token,
            )
            .values(diagnostics=_diagnostics_with_reason(None, diagnostics))
        )
        return result.rowcount == 1

    async def applied_count(self, session: AsyncSession, *, resource_key: str) -> int:
        observed = await session.scalar(
            select(func.count())
            .select_from(RuntimeMaterializationAttemptRow)
            .where(
                RuntimeMaterializationAttemptRow.resource_key == resource_key,
                RuntimeMaterializationAttemptRow.outcome == AttemptOutcome.APPLIED.value,
            )
        )
        return int(observed or 0)


def _phase_for(outcome: AttemptOutcome) -> AttemptPhase:
    return {
        AttemptOutcome.APPLIED: AttemptPhase.APPLIED,
        AttemptOutcome.ROLLED_BACK: AttemptPhase.ROLLED_BACK,
        AttemptOutcome.FAILED: AttemptPhase.FAILED,
    }[outcome]


def _diagnostics_with_reason(
    existing: object, diagnostics: tuple[RedactedDiagnostic, ...]
) -> dict[str, object]:
    base: dict[str, object] = dict(existing) if isinstance(existing, Mapping) else {}
    base["diagnostics"] = diagnostic_payload(diagnostics)
    return base


def _to_attempt(row: RuntimeMaterializationAttemptRow) -> AttemptSnapshot:
    digests = attempt_digests(row.diagnostics)
    return AttemptSnapshot(
        attempt_id=row.id,
        project_id=row.project_id,
        resource_key=row.resource_key,
        revision_id=row.revision_id,
        applied_revision_id=row.applied_revision_id,
        idempotency_key=row.idempotency_key,
        plan_digest=row.plan_digest,
        generation=row.generation,
        observed_generation=row.observed_generation,
        phase=AttemptPhase(row.phase),
        outcome=AttemptOutcome(row.outcome) if row.outcome is not None else None,
        receipt_digest=row.receipt_digest,
        fence_token=row.fence_token,
        rollback_of_generation=row.rollback_of_generation,
        reason_code=row.reason_code,
        snapshot_digest=digests.snapshot_digest if digests is not None else None,
        operation_set_digest=digests.operation_set_digest if digests is not None else None,
        diagnostics=safe_reading((row.diagnostics or {}).get("diagnostics")),
    )


__all__ = ["SqlAlchemyAttemptStore", "attempt_digests"]
