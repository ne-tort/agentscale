"""Production redacted audit writer over ``runtime_materialization_audit``.

The table is append-only (a database trigger rejects UPDATE/DELETE/TRUNCATE) and
carries only a redacted reason code, an opaque correlation id, digests and numeric
generations. This writer is the production implementation of the state machine's
``RedactedRuntimeAuditPort`` *and* of the session-bound
``TransactionalRuntimeAuditPort`` consumed by the revision repository, so the same
INSERT is committed in the same transaction as the state transition it describes.

If the INSERT fails — validation, constraint, trigger or connection — the
exception propagates out of the caller's transaction, the transition rolls back and
the caller must not acknowledge the apply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.runtime_materialization.state_machine import (
    RuntimeAuditRecord,
    canonical_digest,
)
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    RuntimeMaterializationAuditRow,
)

from .contracts import AuditWriteRejected

_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "credential", "private_key")
_MAX_ID = 128

FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class RuntimeAuditBinding:
    """Opaque, non-secret identity a single apply writes its audit rows for."""

    project_id: str
    resource_key: str
    correlation_id: str
    resource_id: str | None = None
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.project_id, "project_id"),
            (self.resource_key, "resource_key"),
            (self.correlation_id, "correlation_id"),
        ):
            _require_identifier(value, name)
        for value, name in ((self.resource_id, "resource_id"), (self.attempt_id, "attempt_id")):
            if value is not None:
                _require_identifier(value, name)


@dataclass(frozen=True, slots=True)
class AuditTransition:
    """One append-only audit row for a materialization transition."""

    event: str
    outcome: str
    reason_code: str
    plan_digest: str
    generation: int
    attempt_id: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_code(self.event, "event")
        _require_code(self.outcome, "outcome")
        _require_code(self.reason_code, "reason_code")
        _require_digest(self.plan_digest, "plan_digest")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("audit generation must be a non-negative integer")
        if self.attempt_id is not None:
            _require_identifier(self.attempt_id, "attempt_id")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


class SqlAlchemyRuntimeAuditWriter:
    """Append-only, transaction-bound writer for one materialization binding.

    The class exposes no update or delete path. ``append`` matches the
    ``TransactionalRuntimeAuditPort`` protocol used by the revision repository, and
    ``append_transition`` carries the attempt id and the exact transition codes.
    """

    def __init__(self, *, binding: RuntimeAuditBinding) -> None:
        if not isinstance(binding, RuntimeAuditBinding):
            raise ValueError("audit binding is required")
        self._binding = binding

    @property
    def binding(self) -> RuntimeAuditBinding:
        return self._binding

    async def append(self, *, session: AsyncSession, record: RuntimeAuditRecord) -> None:
        """Protocol form: derive the transition from the state-machine record."""
        if not isinstance(record, RuntimeAuditRecord):
            raise AuditWriteRejected("audit record must be a RuntimeAuditRecord")
        transition = AuditTransition(
            event=record.event,
            outcome=record.outcome,
            reason_code=record.reason_code,
            plan_digest=record.plan_digest,
            generation=record.generation,
            attempt_id=self._binding.attempt_id,
        )
        self._validate_binding(record)
        await self.append_transition(session=session, transition=transition)

    async def append_transition(
        self, *, session: AsyncSession, transition: AuditTransition
    ) -> None:
        if not isinstance(transition, AuditTransition):
            raise AuditWriteRejected("audit transition must be typed")
        binding = self._binding
        self._reject_secret_values(transition)
        session.add(
            RuntimeMaterializationAuditRow(
                project_id=binding.project_id,
                resource_id=binding.resource_id,
                resource_key=binding.resource_key,
                attempt_id=transition.attempt_id,
                event=transition.event,
                outcome=transition.outcome,
                reason_code=transition.reason_code,
                resource_digest=canonical_digest(binding.resource_key),
                correlation_id=binding.correlation_id,
                correlation_digest=canonical_digest(binding.correlation_id),
                plan_digest=transition.plan_digest,
                generation=transition.generation,
            )
        )
        await session.flush()

    def transition(
        self,
        *,
        event: str,
        outcome: str,
        reason_code: str,
        plan_digest: str,
        generation: int,
        attempt_id: str | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> AuditTransition:
        return AuditTransition(
            event=event,
            outcome=outcome,
            reason_code=reason_code,
            plan_digest=plan_digest,
            generation=generation,
            attempt_id=attempt_id if attempt_id is not None else self._binding.attempt_id,
            diagnostics=diagnostics,
        )

    def _validate_binding(self, record: RuntimeAuditRecord) -> None:
        if not isinstance(record.resource_digest, str) or not _SHA256.fullmatch(record.resource_digest):
            raise AuditWriteRejected("audit resource digest must be sha256")
        if not isinstance(record.correlation_digest, str) or not _SHA256.fullmatch(record.correlation_digest):
            raise AuditWriteRejected("audit correlation digest must be sha256")
        if record.resource_digest != canonical_digest(self._binding.resource_key):
            raise AuditWriteRejected("audit record belongs to another resource")
        if record.correlation_digest != canonical_digest(self._binding.correlation_id):
            raise AuditWriteRejected("audit record belongs to another correlation")

    @staticmethod
    def _reject_secret_values(transition: AuditTransition) -> None:
        for value in (transition.event, transition.outcome, transition.reason_code):
            if any(marker in value.lower() for marker in _SECRET_MARKERS):
                raise AuditWriteRejected("audit codes must not name secret material")
        for code in transition.diagnostics:
            _require_code(code, "audit diagnostic")


def _require_identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(ch.isspace() for ch in value):
        raise AuditWriteRejected(f"{name} must be non-empty and whitespace-free")
    if len(value) > _MAX_ID:
        raise AuditWriteRejected(f"{name} exceeds {_MAX_ID} characters")
    if any(marker in value.lower() for marker in _SECRET_MARKERS):
        raise AuditWriteRejected(f"{name} must not name secret material")


def _require_code(value: object, name: str) -> None:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise AuditWriteRejected(f"{name} must be a redacted lower-case code")


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise AuditWriteRejected(f"{name} must be a sha256 digest")


__all__ = [
    "AuditTransition",
    "RuntimeAuditBinding",
    "SqlAlchemyRuntimeAuditWriter",
]
