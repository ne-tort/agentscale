"""Immutable contracts for the durable resource materialization attempt ledger.

MIG-RES-ATTEMPT-02. Everything in this module is a pure value object: no session,
no ORM row, no clock. Persistence lives in ``heads``/``attempts``/``revisions``;
the transaction owner is ``executor``.

Two invariants define the ledger and are re-stated here because every adapter
depends on them:

* the authoritative generation belongs to the *resource* head
  (``runtime_revision_heads``), never to an individual materialization entry, so a
  plan with zero operations is fenced by the same compare-and-set as a populated
  plan;
* an attempt is identified by (``resource_key``, ``idempotency_key``) and by
  (``resource_key``, ``generation``). A replay of the same key returns the stored
  attempt; the same key with a different plan digest is a conflict and the stored
  row is never rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "credential", "private_key")
_OPEN_PHASE = "applying"


def _require_text(value: object, name: str, *, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or any(ch.isspace() for ch in value):
        raise ValueError(f"{name} must be non-empty and whitespace-free")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")


def _require_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a sha256 digest")


def _require_code(value: object, name: str) -> None:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise ValueError(f"{name} must be a redacted lower-case code")
    if any(marker in value.lower() for marker in _SECRET_MARKERS):
        raise ValueError(f"{name} must not name secret material")


def resource_key_for_project(project_id: str) -> str:
    """Deterministic resource key of a project's canonical resource set.

    The key is derived, never supplied by a client, so a caller cannot fence a
    different resource than the one it plans. It is whitespace-free and bounded by
    ``runtime_revision_heads.resource_key`` (200 characters).
    """
    _require_text(project_id, "project_id", limit=120)
    return f"project/{project_id}/resource-materialization"


class AttemptPhase(StrEnum):
    """Mirrors ``ck_runtime_attempt_phase``."""

    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class AttemptOutcome(StrEnum):
    """Mirrors ``ck_runtime_attempt_outcome``; NULL only while applying."""

    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class HeadPhase(StrEnum):
    """Mirrors ``ck_runtime_revision_head_phase``."""

    IDLE = "idle"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class RedactedDiagnostic:
    """Phase plus reason code; the only diagnostic shape written to durable rows."""

    phase: str
    reason_code: str

    def __post_init__(self) -> None:
        _require_code(self.phase, "diagnostic phase")
        _require_code(self.reason_code, "diagnostic reason_code")

    def canonical(self) -> dict[str, str]:
        return {"phase": self.phase, "reason_code": self.reason_code}


def diagnostic_payload(diagnostics: tuple[RedactedDiagnostic, ...]) -> list[dict[str, str]]:
    return [item.canonical() for item in diagnostics]


def attempt_receipt_digest(
    *,
    resource_key: str,
    idempotency_key: str,
    plan_digest: str,
    generation: int,
    outcome: AttemptOutcome | str,
) -> str:
    """Content digest of the terminal attempt receipt.

    Bound to the exact attempt identity and the observed outcome, so a rewritten
    receipt is detectable even if the row is read outside the writer.
    """
    _require_digest(plan_digest, "plan_digest")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("generation must be a positive integer")
    return canonical_digest(
        {
            "resource_key": resource_key,
            "idempotency_key": idempotency_key,
            "plan_digest": plan_digest,
            "generation": generation,
            "outcome": AttemptOutcome(outcome).value,
        }
    )


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    """One fenced apply request.

    ``expected_generation`` is the generation this plan *advances to*, i.e. the
    post-CAS value. The caller that built the plan has already re-read the store
    and must pass ``observed_generation + 1``; the executor compares it against
    the head and records ``observed_generation = expected_generation - 1`` on the
    attempt row (``ck_runtime_attempt_single_generation_increment``).
    """

    project_id: str
    expected_generation: int
    snapshot_digest: str
    operation_set_digest: str
    operations: tuple[ResourceOperation, ...]
    correlation_id: str
    idempotency_key: str
    revision_id: str | None = None
    rollback_of_generation: int | None = None
    source_event: str | None = None
    created_by: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id", limit=120)
        if not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool):
            raise ValueError("expected_generation must be an integer")
        if self.expected_generation < 1:
            raise ValueError("expected_generation must be positive")
        _require_digest(self.snapshot_digest, "snapshot_digest")
        _require_digest(self.operation_set_digest, "operation_set_digest")
        _require_text(self.correlation_id, "correlation_id", limit=128)
        _require_text(self.idempotency_key, "idempotency_key", limit=128)
        if self.revision_id is not None:
            _require_text(self.revision_id, "revision_id", limit=64)
        if self.source_event is not None:
            _require_text(self.source_event, "source_event", limit=128)
        if self.created_by is not None:
            _require_text(self.created_by, "created_by", limit=128)
        operations = tuple(self.operations)
        if any(not isinstance(item, ResourceOperation) for item in operations):
            raise ValueError("operations must be typed resource operations")
        targets = [item.target.casefold() for item in operations]
        if len(set(targets)) != len(targets):
            raise ValueError("operation targets must be unique within one plan")
        object.__setattr__(self, "operations", operations)
        if self.rollback_of_generation is not None:
            if (not isinstance(self.rollback_of_generation, int)
                    or isinstance(self.rollback_of_generation, bool)
                    or not 1 <= self.rollback_of_generation < self.expected_generation):
                raise ValueError("rollback target must precede the advanced generation")

    @property
    def resource_key(self) -> str:
        return resource_key_for_project(self.project_id)

    @property
    def observed_generation(self) -> int:
        return self.expected_generation - 1

    @property
    def is_empty(self) -> bool:
        """An empty plan has no entries to fence; only the head can fence it."""

        return not self.operations

    @property
    def plan_digest(self) -> str:
        return canonical_digest(
            {
                "project_id": self.project_id,
                "resource_key": self.resource_key,
                "expected_generation": self.expected_generation,
                "snapshot_digest": self.snapshot_digest,
                "operation_set_digest": self.operation_set_digest,
                "operation_count": len(self.operations),
                "rollback_of_generation": self.rollback_of_generation,
            }
        )


def apply_idempotency_key(
    *,
    project_id: str,
    expected_generation: int,
    snapshot_digest: str,
    operation_set_digest: str,
    rollback_of_generation: int | None = None,
) -> str:
    """Deterministic key for the port-level ``apply_cas`` entry point.

    ``AtomicMaterializationApplyPort`` carries no key, so the key is derived from
    the exact plan inputs. The same plan replays the stored attempt; any changed
    input produces another key *and* a stale generation, which the head CAS
    rejects, so a changed plan can never overwrite a receipt.
    """
    return canonical_digest(
        {
            "project_id": project_id,
            "expected_generation": expected_generation,
            "snapshot_digest": snapshot_digest,
            "operation_set_digest": operation_set_digest,
            "rollback_of_generation": rollback_of_generation,
        }
    )


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """Durable attempt row, materialization-agnostic."""

    attempt_id: str
    project_id: str
    resource_key: str
    revision_id: str | None
    applied_revision_id: str | None
    idempotency_key: str
    plan_digest: str
    generation: int
    observed_generation: int
    phase: AttemptPhase
    outcome: AttemptOutcome | None
    receipt_digest: str | None
    fence_token: str
    rollback_of_generation: int | None
    reason_code: str
    snapshot_digest: str | None = None
    operation_set_digest: str | None = None
    diagnostics: tuple[RedactedDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id", limit=64)
        _require_text(self.resource_key, "resource_key", limit=200)
        _require_text(self.idempotency_key, "idempotency_key", limit=128)
        _require_digest(self.plan_digest, "plan_digest")
        if self.generation < 1:
            raise ValueError("attempt generation must be positive")
        if self.observed_generation < 0:
            raise ValueError("observed generation cannot be negative")
        if self.generation != self.observed_generation + 1:
            raise ValueError("an attempt advances exactly one generation")
        object.__setattr__(self, "phase", AttemptPhase(self.phase))
        outcome = AttemptOutcome(self.outcome) if self.outcome is not None else None
        object.__setattr__(self, "outcome", outcome)
        if (self.phase is AttemptPhase.APPLYING) != (outcome is None):
            raise ValueError("only an applying attempt has no outcome")
        if (self.phase is AttemptPhase.APPLYING) != (self.receipt_digest is None):
            raise ValueError("only an applying attempt has no receipt digest")
        if outcome is not None and self.receipt_digest is not None:
            expected = attempt_receipt_digest(
                resource_key=self.resource_key,
                idempotency_key=self.idempotency_key,
                plan_digest=self.plan_digest,
                generation=self.generation,
                outcome=outcome,
            )
            if self.receipt_digest != expected:
                raise ValueError("attempt receipt digest does not match its identity")
        if (outcome in {AttemptOutcome.APPLIED, AttemptOutcome.ROLLED_BACK}) != (
            self.applied_revision_id is not None
        ):
            raise ValueError("a positive attempt must link its applied revision")
        if self.phase is AttemptPhase.APPLYING and self.applied_revision_id is not None:
            raise ValueError("an applying attempt cannot link an applied revision")
        _require_code(self.reason_code, "reason_code")
        for value, name in (
            (self.snapshot_digest, "snapshot_digest"),
            (self.operation_set_digest, "operation_set_digest"),
        ):
            if value is not None:
                _require_digest(value, name)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def is_terminal(self) -> bool:
        return self.phase is not AttemptPhase.APPLYING

    @property
    def granted_authority(self) -> bool:
        """Only a committed positive receipt grants runtime authority."""

        return self.phase in {AttemptPhase.APPLIED, AttemptPhase.ROLLED_BACK}


@dataclass(frozen=True, slots=True)
class HeadSnapshot:
    """Resource-wide compare-and-set head."""

    resource_key: str
    project_id: str
    generation: int
    phase: HeadPhase
    fence_token: str | None
    revision_id: str | None
    applied_revision_id: str | None
    applied_desired_digest: str | None
    ownership_digest: str | None
    diagnostics: tuple[RedactedDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.resource_key, "resource_key", limit=200)
        _require_text(self.project_id, "project_id", limit=120)
        if self.generation < 0:
            raise ValueError("head generation cannot be negative")
        object.__setattr__(self, "phase", HeadPhase(self.phase))
        if self.generation == 0 and self.phase is not HeadPhase.IDLE:
            raise ValueError("a head without a generation must be idle")
        for value, name in (
            (self.applied_desired_digest, "applied_desired_digest"),
            (self.ownership_digest, "ownership_digest"),
        ):
            if value is not None:
                _require_digest(value, name)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


@dataclass(frozen=True, slots=True)
class DurableApplyResult:
    """Outcome of one durable apply attempt plus its exact durable evidence."""

    outcome: str
    reason_code: str
    attempt: AttemptSnapshot | None = None
    head: HeadSnapshot | None = None
    replayed: bool = False
    diagnostics: tuple[RedactedDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", MaterializationApplyOutcome(self.outcome))
        _require_code(self.reason_code, "reason_code")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


@dataclass(frozen=True, slots=True)
class DurableRollbackCommand:
    """Separately approved rollback to an immutable prior applied generation."""

    project_id: str
    target_generation: int
    expected_generation: int
    approval_id: str | None
    idempotency_key: str
    correlation_id: str
    created_by: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id", limit=120)
        for value, name in ((self.target_generation, "target_generation"), (self.expected_generation, "expected_generation")):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        _require_text(self.idempotency_key, "idempotency_key", limit=128)
        _require_text(self.correlation_id, "correlation_id", limit=128)
        if self.target_generation >= self.expected_generation:
            raise ValueError("rollback target must precede the expected generation")

    @property
    def resource_key(self) -> str:
        return resource_key_for_project(self.project_id)

    @property
    def plan_digest(self) -> str:
        return canonical_digest(
            {
                "project_id": self.project_id,
                "rollback_of_generation": self.target_generation,
                "expected_generation": self.expected_generation,
                "approval_id": self.approval_id,
            }
        )


class DurableLedgerError(RuntimeError):
    """Base class for deliberate ledger refusals."""


class AttemptLedgerConflict(DurableLedgerError):
    """A unique key or generation was already claimed."""


class RevisionScopeUnavailable(DurableLedgerError):
    """The exact project runtime scope could not be proven."""


class AuditWriteRejected(DurableLedgerError):
    """A redacted audit record was refused before it reached the database."""


def safe_reading(row_diagnostics: object) -> tuple[RedactedDiagnostic, ...]:
    """Read diagnostics written by this package; anything else is dropped."""

    if not isinstance(row_diagnostics, (list, tuple)):
        return ()
    out: list[RedactedDiagnostic] = []
    for item in row_diagnostics:
        if isinstance(item, Mapping):
            try:
                out.append(RedactedDiagnostic(str(item.get("phase", "")), str(item.get("reason_code", ""))))
            except ValueError:
                continue
    return tuple(out)


def attempt_diagnostics_payload(
    *,
    snapshot_digest: str,
    operation_set_digest: str,
    operation_count: int,
    rollback_of_generation: int | None,
    diagnostics: tuple[RedactedDiagnostic, ...] = (),
) -> dict[str, object]:
    """Redacted, digest-only attempt metadata.

    The attempt table has no desired/ownership digest columns, but a rollback and
    an audit review both need the applied digests *per generation*. Only
    immutable digests, counts and reason codes are stored; payloads never are.
    """
    _require_digest(snapshot_digest, "snapshot_digest")
    _require_digest(operation_set_digest, "operation_set_digest")
    if not isinstance(operation_count, int) or isinstance(operation_count, bool) or operation_count < 0:
        raise ValueError("operation_count cannot be negative")
    return {
        "snapshot_digest": snapshot_digest,
        "operation_set_digest": operation_set_digest,
        "operation_count": operation_count,
        "rollback_of_generation": rollback_of_generation,
        "diagnostics": diagnostic_payload(diagnostics),
    }


@dataclass(frozen=True, slots=True)
class AttemptDigests:
    """Digests recovered from a terminal attempt receipt."""

    snapshot_digest: str
    operation_set_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.snapshot_digest, "snapshot_digest")
        _require_digest(self.operation_set_digest, "operation_set_digest")


def attempt_digests(row_diagnostics: object) -> AttemptDigests | None:
    if not isinstance(row_diagnostics, Mapping):
        return None
    try:
        return AttemptDigests(
            str(row_diagnostics.get("snapshot_digest", "")),
            str(row_diagnostics.get("operation_set_digest", "")),
        )
    except ValueError:
        return None


__all__ = [
    "ApplyPlan",
    "AttemptDigests",
    "AttemptLedgerConflict",
    "AttemptOutcome",
    "AttemptPhase",
    "AttemptSnapshot",
    "AuditWriteRejected",
    "DurableApplyResult",
    "DurableLedgerError",
    "DurableRollbackCommand",
    "HeadPhase",
    "HeadSnapshot",
    "RedactedDiagnostic",
    "RevisionScopeUnavailable",
    "apply_idempotency_key",
    "attempt_diagnostics_payload",
    "attempt_digests",
    "attempt_receipt_digest",
    "diagnostic_payload",
    "resource_key_for_project",
    "safe_reading",
]
