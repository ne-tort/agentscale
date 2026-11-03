"""Public runtime revision state-machine application contracts.

Importing this module has no live-system side effects; all persistence, runtime,
Pod/bridge validation and audit access must be supplied through ports.
"""
from .state_machine import (
    AppliedRevision,
    ApplyClaim,
    ApplyOutcome,
    ApplyResult,
    AttemptRecord,
    ClaimResult,
    ClaimStatus,
    DependencyEvidence,
    DesiredRevision,
    MaterializationOperation,
    MaterializationPlan,
    OperationAction,
    OperationReceipt,
    OperationValidation,
    OwnershipEntry,
    OwnershipManifest,
    PlanAction,
    PlannedOperation,
    PreparedRevision,
    Provenance,
    RedactedRuntimeAuditPort,
    RevisionPhase,
    RollbackCommand,
    RuntimeAuditRecord,
    RuntimeClockPort,
    RuntimeDiagnostic,
    RuntimeEvidenceValidationPort,
    RuntimeMaterializationService,
    RuntimeOperationPort,
    RuntimeOperationValidationPort,
    RuntimeRevisionState,
    RuntimeRevisionStorePort,
    ValidationReport,
    canonical_digest,
    plan_materialization,
)
from .sqlalchemy_repository import (
    PersistenceResult,
    PersistenceStatus,
    SqlAlchemyRuntimeRevisionRepository,
    TransactionalRuntimeAuditPort,
)

RuntimeRevisionService = RuntimeMaterializationService

__all__ = [
    "AppliedRevision", "ApplyClaim", "ApplyOutcome", "ApplyResult", "AttemptRecord",
    "ClaimResult", "ClaimStatus", "DependencyEvidence", "DesiredRevision",
    "MaterializationOperation", "MaterializationPlan", "OperationAction", "OperationReceipt",
    "OperationValidation", "OwnershipEntry", "OwnershipManifest", "PlanAction",
    "PlannedOperation", "PreparedRevision", "Provenance", "RedactedRuntimeAuditPort",
    "RevisionPhase", "RollbackCommand", "RuntimeAuditRecord", "RuntimeDiagnostic",
    "RuntimeMaterializationService", "RuntimeRevisionService", "RuntimeClockPort", "RuntimeEvidenceValidationPort", "RuntimeOperationPort",
    "RuntimeOperationValidationPort", "RuntimeRevisionState", "RuntimeRevisionStorePort",
    "ValidationReport", "canonical_digest", "plan_materialization",
    "PersistenceResult", "PersistenceStatus", "SqlAlchemyRuntimeRevisionRepository",
    "TransactionalRuntimeAuditPort",
]
