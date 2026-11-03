"""Durable resource-materialization ledger: attempts, head CAS, audit, executor.

Public surface of MIG-RES-ATTEMPT-02. Importing this package has no side effects
and no database access; every adapter takes an explicit session or session
factory from the composition point.
"""

from .attempts import SqlAlchemyAttemptStore
from .audit import (
    AuditTransition,
    RuntimeAuditBinding,
    SqlAlchemyRuntimeAuditWriter,
)
from .contracts import (
    ApplyPlan,
    AttemptDigests,
    AttemptLedgerConflict,
    AttemptOutcome,
    AttemptPhase,
    AttemptSnapshot,
    AuditWriteRejected,
    DurableApplyResult,
    DurableLedgerError,
    DurableRollbackCommand,
    HeadPhase,
    HeadSnapshot,
    RedactedDiagnostic,
    RevisionScopeUnavailable,
    apply_idempotency_key,
    attempt_diagnostics_payload,
    attempt_digests,
    attempt_receipt_digest,
    diagnostic_payload,
    resource_key_for_project,
    safe_reading,
)
from .effects import (
    OperationEffectReceipt,
    OperationFence,
    ResourceOperationEffectPort,
    RollbackEffectPort,
    resource_operation_digest,
)
from .executor import DurableMaterializationExecutor
from .heads import SqlAlchemyRevisionHeadStore, new_fence_token
from .ports import DurableApplyGate, DurableClockPort, LinkAndLeaseGate, SystemUtcClock
from .revisions import RevisionApplicationScope, SqlAlchemyRevisionSnapshotStore

__all__ = [
    "ApplyPlan",
    "AttemptDigests",
    "AttemptLedgerConflict",
    "AttemptOutcome",
    "AttemptPhase",
    "AttemptSnapshot",
    "AuditTransition",
    "AuditWriteRejected",
    "DurableApplyGate",
    "DurableApplyResult",
    "DurableClockPort",
    "DurableLedgerError",
    "DurableMaterializationExecutor",
    "DurableRollbackCommand",
    "HeadPhase",
    "HeadSnapshot",
    "LinkAndLeaseGate",
    "OperationEffectReceipt",
    "OperationFence",
    "RedactedDiagnostic",
    "ResourceOperationEffectPort",
    "RevisionApplicationScope",
    "RevisionScopeUnavailable",
    "RollbackEffectPort",
    "RuntimeAuditBinding",
    "SqlAlchemyAttemptStore",
    "SqlAlchemyRevisionHeadStore",
    "SqlAlchemyRevisionSnapshotStore",
    "SqlAlchemyRuntimeAuditWriter",
    "SystemUtcClock",
    "apply_idempotency_key",
    "attempt_diagnostics_payload",
    "attempt_digests",
    "attempt_receipt_digest",
    "diagnostic_payload",
    "new_fence_token",
    "resource_key_for_project",
    "resource_operation_digest",
    "safe_reading",
]
