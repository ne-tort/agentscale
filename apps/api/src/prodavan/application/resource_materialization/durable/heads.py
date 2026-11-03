"""Resource-wide CAS over ``runtime_revision_heads``.

The head is the only writer of a materialization generation. A claim is one
conditional UPDATE::

    UPDATE runtime_revision_heads
       SET generation = generation + 1, phase = 'applying', fence_token = :token
     WHERE resource_key = :resource_key
       AND project_id   = :project_id
       AND generation   = :observed_generation

Zero affected rows means the observed generation is stale and the caller must
fail closed. Because the predicate is on the head row — not on
``materialization_entries`` — a plan with **no** operations is fenced exactly like
a populated plan. A generation is never decremented and never reused.

Every method takes an existing ``AsyncSession``: the caller owns the transaction
so the CAS, the attempt receipt and the redacted audit row commit together.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.persistence.models.runtime_revisions import (
    RuntimeRevisionHeadRow,
)

from .contracts import (
    HeadPhase,
    HeadSnapshot,
    RedactedDiagnostic,
    diagnostic_payload,
    resource_key_for_project,
    safe_reading,
)


def new_fence_token() -> str:
    """Opaque per-claim fence; unique by constraint, so it cannot be reused."""

    return f"fence-{uuid.uuid4().hex}"


@dataclass(slots=True)
class SqlAlchemyRevisionHeadStore:
    """Session-bound head ledger; no transaction of its own."""

    async def ensure(
        self,
        session: AsyncSession,
        *,
        project_id: str,
        resource_key: str,
        resource_id: str | None = None,
    ) -> HeadSnapshot:
        """Create the generation-0 head if it does not exist yet.

        The insert runs inside a SAVEPOINT so a concurrent creator that wins the
        unique constraint does not abort the caller's transaction.
        """
        try:
            async with session.begin_nested():
                session.add(
                    RuntimeRevisionHeadRow(
                        project_id=project_id,
                        resource_key=resource_key,
                        resource_id=resource_id,
                        generation=0,
                        phase=HeadPhase.IDLE.value,
                        fence_token=None,
                        diagnostics=None,
                        uncertain_operations=None,
                    )
                )
                await session.flush()
        except IntegrityError:
            pass
        head = await self.read(session, resource_key=resource_key, lock=True)
        if head is None:
            raise RuntimeError("revision head could not be created")
        return head

    async def read(
        self, session: AsyncSession, *, resource_key: str, lock: bool = False
    ) -> HeadSnapshot | None:
        query = select(RuntimeRevisionHeadRow).where(
            RuntimeRevisionHeadRow.resource_key == resource_key
        )
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query)
        return _to_head(row) if row is not None else None

    async def claim(
        self,
        session: AsyncSession,
        *,
        resource_key: str,
        project_id: str,
        expected_generation: int,
        fence_token: str,
        diagnostics: tuple[RedactedDiagnostic, ...] = (),
    ) -> HeadSnapshot | None:
        """Advance the head by exactly one generation or return ``None``."""
        if expected_generation < 0:
            raise ValueError("expected_generation cannot be negative")
        if not fence_token.strip():
            raise ValueError("fence_token is required")
        result = await session.execute(
            update(RuntimeRevisionHeadRow)
            .where(
                RuntimeRevisionHeadRow.resource_key == resource_key,
                RuntimeRevisionHeadRow.project_id == project_id,
                RuntimeRevisionHeadRow.generation == expected_generation,
            )
            .values(
                generation=RuntimeRevisionHeadRow.generation + 1,
                phase=HeadPhase.APPLYING.value,
                fence_token=fence_token,
                diagnostics=diagnostic_payload(diagnostics) if diagnostics else None,
            )
        )
        if result.rowcount != 1:
            return None
        return await self.read(session, resource_key=resource_key, lock=True)

    async def finish(
        self,
        session: AsyncSession,
        *,
        resource_key: str,
        fence_token: str,
        generation: int,
        phase: HeadPhase,
        diagnostics: tuple[RedactedDiagnostic, ...] = (),
        applied_desired_digest: str | None = None,
        ownership_digest: str | None = None,
        revision_id: str | None = None,
        applied_revision_id: str | None = None,
    ) -> HeadSnapshot | None:
        """Close the claim under the same fence; a stale fence changes nothing."""
        if phase in {HeadPhase.IDLE, HeadPhase.APPLYING}:
            raise ValueError("finish requires a terminal head phase")
        values: dict[str, object] = {
            "phase": phase.value,
            "diagnostics": diagnostic_payload(diagnostics) if diagnostics else None,
        }
        if applied_desired_digest is not None:
            values["applied_desired_digest"] = applied_desired_digest
        if ownership_digest is not None:
            values["ownership_digest"] = ownership_digest
        if revision_id is not None:
            values["revision_id"] = revision_id
        if applied_revision_id is not None:
            values["applied_revision_id"] = applied_revision_id
        result = await session.execute(
            update(RuntimeRevisionHeadRow)
            .where(
                RuntimeRevisionHeadRow.resource_key == resource_key,
                RuntimeRevisionHeadRow.fence_token == fence_token,
                RuntimeRevisionHeadRow.generation == generation,
                RuntimeRevisionHeadRow.phase == HeadPhase.APPLYING.value,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            return None
        return await self.read(session, resource_key=resource_key, lock=True)

    async def project_generation(
        self, session: AsyncSession, *, project_id: str, resource_key: str | None = None
    ) -> int:
        """Highest durable generation observed for a project (0 when unknown).

        This is the durable source for ``ResourceStorePort.project_generation``;
        it must never be derived from a plan or a request payload.
        """
        query = select(func.max(RuntimeRevisionHeadRow.generation)).where(
            RuntimeRevisionHeadRow.project_id == project_id
        )
        if resource_key is not None:
            query = query.where(RuntimeRevisionHeadRow.resource_key == resource_key)
        observed = await session.scalar(query)
        return int(observed or 0)


def _to_head(row: RuntimeRevisionHeadRow) -> HeadSnapshot:
    return HeadSnapshot(
        resource_key=row.resource_key,
        project_id=row.project_id,
        generation=row.generation,
        phase=HeadPhase(row.phase),
        fence_token=row.fence_token,
        revision_id=row.revision_id,
        applied_revision_id=row.applied_revision_id,
        applied_desired_digest=row.applied_desired_digest,
        ownership_digest=row.ownership_digest,
        diagnostics=safe_reading(row.diagnostics),
    )


__all__ = ["SqlAlchemyRevisionHeadStore", "new_fence_token"]
