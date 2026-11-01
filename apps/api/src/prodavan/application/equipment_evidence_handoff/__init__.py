"""Exclusive equipment provenance admission application boundary.

The package has no ORM or route dependencies. Positive downstream intent is
available only through a durable adapter-owned transaction/CAS operation.
"""

from .contracts import (
    AdmissionIntent,
    CandidateOfferMembershipProof,
    EquipmentAdmissionCommand,
    EquipmentAdmissionResult,
    EquipmentEvidenceBundle,
    EvidenceIdempotencyKey,
    EquipmentOwnerPartition,
    EquipmentPromotionPartition,
    EquipmentQuarantinePartition,
    HandoffStatus,
    PromotionStatus,
)
from .service import EquipmentEvidenceHandoffService, EvidenceHandoffError

__all__ = [
    "AdmissionIntent",
    "CandidateOfferMembershipProof",
    "EquipmentAdmissionCommand",
    "EquipmentAdmissionResult",
    "EquipmentEvidenceBundle",
    "EvidenceIdempotencyKey",
    "EquipmentEvidenceHandoffService",
    "EquipmentOwnerPartition",
    "EquipmentPromotionPartition",
    "EquipmentQuarantinePartition",
    "EvidenceHandoffError",
    "HandoffStatus",
    "PromotionStatus",
]
