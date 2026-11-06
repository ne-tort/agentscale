"""Fail-closed equipment provenance admission with mandatory durable CAS."""

from __future__ import annotations

from .contracts import (
    AdmissionIntent,
    CandidateOfferMembershipProof,
    EquipmentAdmissionCommand,
    EquipmentAdmissionResult,
    EquipmentEvidenceBundle,
    HandoffStatus,
    PromotionStatus,
)
from .ports import TrustedEvidenceAdmissionPort


class EquipmentEvidenceHandoffService:
    """Expose only a trusted transaction admission boundary.

    This service never treats an in-memory verified decision as a capability.
    Every intent must be re-resolved and committed by the durable adapter in the
    same transaction as evidence append/replay and its tenant/project CAS.
    """

    def __init__(self, *, admission: TrustedEvidenceAdmissionPort) -> None:
        self._admission = admission

    async def admit(
        self,
        bundle: EquipmentEvidenceBundle,
        *,
        intent: AdmissionIntent,
        operation_id: str,
    ) -> EquipmentAdmissionResult:
        command = EquipmentAdmissionCommand(
            evidence=bundle,
            intent=intent,
            operation_id=operation_id,
        )
        result = await self._admission.admit(command)
        if result.promotion_status is not PromotionStatus.BLOCKED_UNPERSISTED:
            raise RuntimeError("equipment promotion must remain blocked without durable promotion evidence")
        if result.status is HandoffStatus.VERIFIED:
            proof = result.membership
            if proof is None or not _membership_matches(bundle, proof):
                raise EvidenceHandoffError("MEMBERSHIP_PROOF_MISMATCH")
            if result.evidence_id != bundle.evidence_id or result.evidence_digest != bundle.digest:
                raise EvidenceHandoffError("EVIDENCE_DIGEST_MISMATCH")
        return result

    async def admit_evidence(self, bundle: EquipmentEvidenceBundle, *, operation_id: str) -> EquipmentAdmissionResult:
        return await self.admit(bundle, intent=AdmissionIntent.EVIDENCE_ONLY, operation_id=operation_id)

    async def admit_matching(self, bundle: EquipmentEvidenceBundle, *, operation_id: str) -> EquipmentAdmissionResult:
        return await self.admit(bundle, intent=AdmissionIntent.MATCHING, operation_id=operation_id)

    async def admit_selection(self, bundle: EquipmentEvidenceBundle, *, operation_id: str) -> EquipmentAdmissionResult:
        return await self.admit(bundle, intent=AdmissionIntent.SELECTION, operation_id=operation_id)

    async def admit_procurement(self, bundle: EquipmentEvidenceBundle, *, operation_id: str) -> EquipmentAdmissionResult:
        return await self.admit(bundle, intent=AdmissionIntent.PROCUREMENT, operation_id=operation_id)

    async def verify(self, bundle: EquipmentEvidenceBundle) -> EquipmentAdmissionResult:
        """Compatibility read surface that is always blocked from authorization.

        Callers must use an ``admit_*`` method; no verified decision is returned
        from a standalone read that could be reused after the source changes.
        """
        return EquipmentAdmissionResult(
            status=HandoffStatus.STALE,
            evidence_id=bundle.evidence_id,
            evidence_digest=bundle.digest,
            reason_code="ADMISSION_REQUIRES_DURABLE_TRANSACTION",
        )


def _membership_matches(bundle: EquipmentEvidenceBundle, proof: CandidateOfferMembershipProof) -> bool:
    return all(
        getattr(proof, field, None) == expected
        for field, expected in (
            ("tenant_id", bundle.owner.tenant_id),
            ("project_id", bundle.owner.project_id),
            ("candidate_group_id", bundle.candidate_group_id),
            ("offer_id", bundle.offer_id),
            ("snapshot_id", bundle.snapshot_id),
            ("source_id", bundle.source_id),
            ("run_id", bundle.run_id),
            ("artifact_id", bundle.artifact_id),
            ("supplier_key", bundle.owner.supplier_key),
            ("provenance_digest", bundle.provenance_digest),
        )
    )


class EvidenceHandoffError(ValueError):
    """Stable fail-closed error raised before any downstream operation."""


__all__ = ["EquipmentEvidenceHandoffService", "EvidenceHandoffError"]
