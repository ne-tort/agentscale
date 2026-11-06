"""MIG-EQ-02 typed equipment-domain migration contracts.

Import order matters only for readability: :mod:`domain` has no dependencies,
:mod:`transactions` depends on it, :mod:`durable_export` depends on both, and
:mod:`service` composes them.  The concrete SQLAlchemy repository lives in
:mod:`durable` and is imported explicitly (``from ... import durable``) so that
importing the contracts never touches a database session.
"""

from .domain import (
    AssociationBackfill,
    BudgetProjection,
    CandidateGroup,
    DomainEvidence,
    DomainScope,
    EquipmentDomainStatus,
    EquipmentProjection,
    EquipmentTender,
    HumanSelection,
    JsonbAssociation,
    OfferFreshness,
    ProcurementExport,
    ProcurementExportLine,
    ProjectionContract,
    RequestLine,
    TypedRequest,
    active_human_selections,
    build_projection,
    prepare_procurement_export,
    select_tender,
)
from .durable_export import (
    BudgetCoverage,
    BudgetCoverageStatus,
    BudgetExportLine,
    DurableExportBundle,
    DurableExportStatus,
    ExportDiagnosticNote,
    prepare_durable_export,
)
from .service import (
    AssociationBackfillResult,
    AssociationBatch,
    DecisionActor,
    DecisionActorKind,
    EquipmentActorPort,
    EquipmentBudgetPort,
    EquipmentDomainReadPort,
    EquipmentDomainService,
    EquipmentDomainSnapshot,
    HumanDecisionPort,
    OrderedAssociationPort,
)
from .transactions import (
    EquipmentDomainTransactionPort,
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
    ExportTransaction,
    HumanSelectionTransactionPort,
    ProcurementExportTransactionPort,
    SelectionTransaction,
    WIRING_BLOCKER,
    blocked_transaction_result,
    validate_transaction_result,
)

__all__ = [
    "AssociationBackfill", "AssociationBackfillResult", "AssociationBatch", "BudgetAllocation",
    "BudgetAllocationStatus", "BudgetCoverage", "BudgetCoverageStatus", "BudgetExportLine",
    "BudgetProjection", "CandidateGroup", "DecisionActor", "DecisionActorKind", "DomainEvidence",
    "DomainScope", "DurableExportBundle", "DurableExportStatus", "EquipmentActorPort",
    "EquipmentBudgetPort", "EquipmentDomainReadPort", "EquipmentDomainService",
    "EquipmentDomainSnapshot", "EquipmentDomainStatus", "EquipmentProjection", "EquipmentTender",
    "ExportDiagnosticNote", "HumanDecisionPort", "HumanSelection", "JsonbAssociation",
    "OfferFreshness", "OrderedAssociationPort", "ProcurementExport", "ProcurementExportLine",
    "ProjectionContract", "RequestLine", "TypedRequest", "active_human_selections",
    "build_projection", "prepare_durable_export", "prepare_procurement_export",
    "select_tender", "selection_evidence_digest",
    "EquipmentDomainTransactionPort", "EquipmentTransactionResult", "EquipmentTransactionStatus",
    "ExportTransaction", "HumanSelectionTransactionPort", "ProcurementExportTransactionPort",
    "SelectionTransaction", "WIRING_BLOCKER", "blocked_transaction_result",
    "validate_transaction_result",
]
