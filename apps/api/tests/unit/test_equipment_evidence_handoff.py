"""Acceptance tests for the exclusive equipment evidence admission.

Tests are intentionally not executed during the migration verification freeze.
"""

import pytest

from prodavan.application.equipment_evidence_handoff import (
    AdmissionIntent,
    EquipmentAdmissionResult,
    EquipmentEvidenceBundle,
    EvidenceIdempotencyKey,
    EquipmentEvidenceHandoffService,
    EquipmentOwnerPartition,
    EquipmentPromotionPartition,
    CandidateOfferMembershipProof,
    EquipmentQuarantinePartition,
    HandoffStatus,
    PromotionStatus,
)


class TrustedAdmission:
    def __init__(self, *, status=HandoffStatus.VERIFIED, reason=None):
        self.calls = []
        self.status = status
        self.reason = reason

    async def admit(self, command):
        self.calls.append(command)
        return EquipmentAdmissionResult(
            status=self.status,
            evidence_id=command.evidence.evidence_id,
            evidence_digest=command.evidence.digest,
            reason_code=self.reason,
            membership=(CandidateOfferMembershipProof(
                tenant_id=command.evidence.owner.tenant_id,
                project_id=command.evidence.owner.project_id,
                candidate_group_id=command.evidence.candidate_group_id,
                offer_id=command.evidence.offer_id,
                snapshot_id=command.evidence.snapshot_id,
                source_id=command.evidence.source_id,
                run_id=command.evidence.run_id,
                artifact_id=command.evidence.artifact_id,
                supplier_key=command.evidence.owner.supplier_key,
                provenance_digest=command.evidence.provenance_digest,
            ) if self.status is HandoffStatus.VERIFIED else None),
        )


def bundle(**changes):
    owner = EquipmentOwnerPartition(
        tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
        visibility_key="project-1", supplier_key="supplier-a",
    )
    values = dict(
        evidence_id="evidence-1", owner=owner,
        promotion=EquipmentPromotionPartition(
            tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
            supplier_key="supplier-a", source_snapshot_id="snapshot-1", evidence_id="evidence-1",
        ),
        quarantine=EquipmentQuarantinePartition(
            tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
            supplier_key="supplier-a", evidence_id="evidence-1", reason_code="UNVERIFIED",
        ),
        source_chain=("supplier-a", "source-1", "run-1", "artifact-1"), source_id="source-1",
        run_id="run-1", artifact_id="artifact-1", snapshot_id="snapshot-1",
        offer_id="offer-1", candidate_group_id="group-1", selected_offer_id="offer-1",
        procurement_id="procurement-1", provenance_digest="a" * 64, writer_generation=1,
    )
    values.update(changes)
    return EquipmentEvidenceBundle(**values)


@pytest.mark.asyncio
async def test_every_intent_uses_one_durable_transaction_and_exact_membership_resolution():
    admission = TrustedAdmission()
    handoff = EquipmentEvidenceHandoffService(admission=admission)

    result = await handoff.admit_selection(bundle(), operation_id="op-1")

    assert result.status is HandoffStatus.VERIFIED
    assert result.promotion_status is PromotionStatus.BLOCKED_UNPERSISTED
    assert len(admission.calls) == 1
    command = admission.calls[0]
    assert command.intent is AdmissionIntent.SELECTION
    assert command.evidence.owner.tenant_id == "tenant-1"
    assert command.evidence.owner.project_id == "project-1"
    assert command.evidence.candidate_group_id == "group-1"
    assert command.evidence.offer_id == "offer-1"
    assert command.evidence.digest == result.evidence_digest
    assert result.membership.tenant_id == "tenant-1"
    assert result.membership.project_id == "project-1"
    assert result.membership.candidate_group_id == "group-1"
    assert result.membership.offer_id == "offer-1"
    assert result.membership.snapshot_id == "snapshot-1"
    assert result.membership.source_id == "source-1"
    assert result.membership.run_id == "run-1"
    assert result.membership.artifact_id == "artifact-1"
    assert result.membership.provenance_digest == "a" * 64
    assert command.idempotency_key == EvidenceIdempotencyKey(
        tenant_id="tenant-1", project_id="project-1", evidence_id="evidence-1", digest=result.evidence_digest
    )


@pytest.mark.asyncio
async def test_standalone_verify_is_stale_and_cannot_authorize_side_effect_or_persist():
    admission = TrustedAdmission()
    handoff = EquipmentEvidenceHandoffService(admission=admission)

    result = await handoff.verify(bundle())

    assert result.status is HandoffStatus.STALE
    assert result.reason_code == "ADMISSION_REQUIRES_DURABLE_TRANSACTION"
    assert result.promotion_status is PromotionStatus.BLOCKED_UNPERSISTED
    assert admission.calls == []


@pytest.mark.asyncio
async def test_quarantine_is_returned_by_trusted_boundary_and_never_authorizes_intent():
    admission = TrustedAdmission(status=HandoffStatus.QUARANTINED, reason="CANDIDATE_MEMBERSHIP_UNPROVEN")
    handoff = EquipmentEvidenceHandoffService(admission=admission)

    result = await handoff.admit_procurement(bundle(), operation_id="op-2")

    assert result.status is HandoffStatus.QUARANTINED
    assert result.reason_code == "CANDIDATE_MEMBERSHIP_UNPROVEN"
    assert result.promotion_status is PromotionStatus.BLOCKED_UNPERSISTED
    assert admission.calls[0].intent is AdmissionIntent.PROCUREMENT


def test_evidence_requires_full_chain_digest_and_positive_writer_generation():
    with pytest.raises(ValueError, match="source, run and artifact"):
        bundle(source_chain=("supplier-a",))
    with pytest.raises(ValueError, match="sha256"):
        bundle(provenance_digest="bad")
    with pytest.raises(ValueError, match="positive"):
        bundle(writer_generation=0)
    with pytest.raises(ValueError, match="positive"):
        bundle(writer_generation=True)


@pytest.mark.asyncio
async def test_verified_result_with_mismatched_membership_is_rejected():
    class WrongMembership(TrustedAdmission):
        async def admit(self, command):
            result = await super().admit(command)
            return EquipmentAdmissionResult(
                status=HandoffStatus.VERIFIED,
                evidence_id=result.evidence_id,
                evidence_digest=result.evidence_digest,
                membership=CandidateOfferMembershipProof(
                    tenant_id="other-tenant",
                    project_id="project-1",
                    candidate_group_id="group-1",
                    offer_id="offer-1",
                    snapshot_id="snapshot-1",
                    source_id="source-1",
                    run_id="run-1",
                    artifact_id="artifact-1",
                    supplier_key="supplier-a",
                    provenance_digest="a" * 64,
                ),
            )

    handoff = EquipmentEvidenceHandoffService(admission=WrongMembership())
    with pytest.raises(ValueError, match="MEMBERSHIP_PROOF_MISMATCH"):
        await handoff.admit_selection(bundle(), operation_id="op-wrong")


def test_verified_result_without_membership_proof_is_rejected():
    with pytest.raises(ValueError, match="exact membership proof"):
        EquipmentAdmissionResult(
            status=HandoffStatus.VERIFIED,
            evidence_id="evidence-1",
            evidence_digest="a" * 64,
        )


def test_digest_changes_for_reused_identity_with_different_source_or_membership():
    original = bundle()
    changed = bundle(offer_id="offer-2")
    assert original.evidence_id == changed.evidence_id
    assert original.digest != changed.digest


def test_supplier_partition_identity_is_required_and_matches_owner():
    with pytest.raises(ValueError, match="supplier_key"):
        EquipmentPromotionPartition(
            tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
            supplier_key="", source_snapshot_id="snapshot-1", evidence_id="evidence-1",
        )
    with pytest.raises(ValueError, match="supplier"):
        bundle(
            promotion=EquipmentPromotionPartition(
                tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
                supplier_key="supplier-b", source_snapshot_id="snapshot-1", evidence_id="evidence-1",
            )
        )


def test_partitions_cannot_cross_owner_or_promotion_snapshot():
    with pytest.raises(ValueError, match="promotion partition does not match"):
        bundle(
            promotion=EquipmentPromotionPartition(
                tenant_id="tenant-1", project_id="project-1", owner_id="owner-1",
                supplier_key="supplier-a", source_snapshot_id="other-snapshot", evidence_id="evidence-1",
            )
        )
