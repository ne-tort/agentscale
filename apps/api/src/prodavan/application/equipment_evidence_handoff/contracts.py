"""Exclusive, persistence-neutral contracts for equipment evidence handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Mapping


_PROVENANCE_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class HandoffStatus(StrEnum):
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    STALE = "stale"


class AdmissionIntent(StrEnum):
    EVIDENCE_ONLY = "evidence_only"
    MATCHING = "matching"
    SELECTION = "selection"
    PROCUREMENT = "procurement"


class PromotionStatus(StrEnum):
    BLOCKED_UNPERSISTED = "blocked_unpersisted"


@dataclass(frozen=True, slots=True)
class EquipmentOwnerPartition:
    """The owner and visibility boundary for every handoff operation."""

    tenant_id: str
    project_id: str
    owner_id: str
    visibility_key: str
    supplier_key: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "owner_id", "visibility_key", "supplier_key"):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class EquipmentPromotionPartition:
    """Promotion identity; it cannot be inferred from an offer or supplier id."""

    tenant_id: str
    project_id: str
    owner_id: str
    supplier_key: str
    source_snapshot_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "project_id", "owner_id", "supplier_key", "source_snapshot_id", "evidence_id"
        ):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class EquipmentQuarantinePartition:
    """Retryable quarantine identity, separate from the trusted owner partition."""

    tenant_id: str
    project_id: str
    owner_id: str
    supplier_key: str
    evidence_id: str
    reason_code: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "project_id", "owner_id", "supplier_key", "evidence_id", "reason_code"
        ):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class EquipmentEvidenceBundle:
    """Complete source chain linking source, snapshot, offers and downstream rows."""

    evidence_id: str
    owner: EquipmentOwnerPartition
    promotion: EquipmentPromotionPartition
    quarantine: EquipmentQuarantinePartition
    source_chain: tuple[str, ...]
    source_id: str
    run_id: str
    artifact_id: str
    snapshot_id: str
    offer_id: str
    candidate_group_id: str
    selected_offer_id: str | None = None
    procurement_id: str | None = None
    provenance_digest: str = ""
    writer_generation: int = 0
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "source_id", "run_id", "artifact_id", "snapshot_id",
            "offer_id", "candidate_group_id",
        ):
            _required(getattr(self, name), name)
        if not self.source_chain:
            raise ValueError("source_chain must not be empty")
        required_chain = {self.source_id, self.run_id, self.artifact_id, self.owner.supplier_key}
        if not required_chain.issubset(self.source_chain):
            raise ValueError("source_chain must include supplier, source, run and artifact identities")
        if not isinstance(self.provenance_digest, str) or not re.fullmatch(
            _PROVENANCE_DIGEST_PATTERN, self.provenance_digest
        ):
            raise ValueError("provenance_digest must be a lowercase sha256 hex digest")
        if not isinstance(self.writer_generation, int) or isinstance(self.writer_generation, bool) or self.writer_generation <= 0:
            raise ValueError("writer_generation must be positive")
        if self.selected_offer_id is not None:
            _required(self.selected_offer_id, "selected_offer_id")
            if self.selected_offer_id != self.offer_id:
                raise ValueError("selected_offer_id must be the evidenced offer")
        if self.procurement_id is not None:
            _required(self.procurement_id, "procurement_id")
        _same_scope(self.owner, self.promotion, "promotion")
        _same_scope(self.owner, self.quarantine, "quarantine")
        if self.promotion.supplier_key != self.owner.supplier_key:
            raise ValueError("promotion partition supplier does not match owner")
        if self.quarantine.supplier_key != self.owner.supplier_key:
            raise ValueError("quarantine partition supplier does not match owner")
        if self.promotion.source_snapshot_id != self.snapshot_id:
            raise ValueError("promotion partition snapshot does not match evidence")
        if self.promotion.evidence_id != self.evidence_id:
            raise ValueError("promotion partition evidence does not match bundle")
        if self.quarantine.evidence_id != self.evidence_id:
            raise ValueError("quarantine partition evidence does not match bundle")
        object.__setattr__(self, "attributes", dict(self.attributes or {}))

    @property
    def digest(self) -> str:
        """Canonical immutable payload digest used by the durable idempotency key."""
        payload = {
            "evidence_id": self.evidence_id,
            "owner": {name: getattr(self.owner, name) for name in self.owner.__dataclass_fields__},
            "promotion": {name: getattr(self.promotion, name) for name in self.promotion.__dataclass_fields__},
            "quarantine": {name: getattr(self.quarantine, name) for name in self.quarantine.__dataclass_fields__},
            "source_chain": self.source_chain,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "artifact_id": self.artifact_id,
            "snapshot_id": self.snapshot_id,
            "offer_id": self.offer_id,
            "candidate_group_id": self.candidate_group_id,
            "selected_offer_id": self.selected_offer_id,
            "procurement_id": self.procurement_id,
            "provenance_digest": self.provenance_digest,
            "writer_generation": self.writer_generation,
            "attributes": dict(self.attributes),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateOfferMembershipProof:
    """Server-resolved exact membership; caller supplied IDs are not proof."""

    tenant_id: str
    project_id: str
    candidate_group_id: str
    offer_id: str
    snapshot_id: str
    source_id: str
    run_id: str
    artifact_id: str
    supplier_key: str
    provenance_digest: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "project_id", "candidate_group_id", "offer_id", "snapshot_id",
            "source_id", "run_id", "artifact_id", "supplier_key"
        ):
            _required(getattr(self, name), name)
        if not re.fullmatch(_PROVENANCE_DIGEST_PATTERN, self.provenance_digest):
            raise ValueError("provenance_digest must be a lowercase sha256 hex digest")


@dataclass(frozen=True, slots=True)
class EvidenceIdempotencyKey:
    """Append-only identity; a digest mismatch is an immutable conflict."""

    tenant_id: str
    project_id: str
    evidence_id: str
    digest: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "evidence_id"):
            _required(getattr(self, name), name)
        if not re.fullmatch(_PROVENANCE_DIGEST_PATTERN, self.digest):
            raise ValueError("digest must be a lowercase sha256 hex digest")


@dataclass(frozen=True, slots=True)
class EquipmentAdmissionCommand:
    evidence: EquipmentEvidenceBundle
    intent: AdmissionIntent
    operation_id: str

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")

    @property
    def idempotency_key(self) -> EvidenceIdempotencyKey:
        return EvidenceIdempotencyKey(
            tenant_id=self.evidence.owner.tenant_id,
            project_id=self.evidence.owner.project_id,
            evidence_id=self.evidence.evidence_id,
            digest=self.evidence.digest,
        )


@dataclass(frozen=True, slots=True)
class EquipmentAdmissionResult:
    status: HandoffStatus
    evidence_id: str
    evidence_digest: str
    reason_code: str | None = None
    membership: CandidateOfferMembershipProof | None = None
    promotion_status: PromotionStatus = PromotionStatus.BLOCKED_UNPERSISTED

    def __post_init__(self) -> None:
        _required(self.evidence_id, "evidence_id")
        if not re.fullmatch(_PROVENANCE_DIGEST_PATTERN, self.evidence_digest):
            raise ValueError("evidence_digest must be a lowercase sha256 hex digest")
        if self.status is HandoffStatus.VERIFIED:
            if self.reason_code is not None:
                raise ValueError("verified admission cannot carry a failure reason")
            if self.membership is None:
                raise ValueError("verified admission requires exact membership proof")
        elif self.membership is not None:
            raise ValueError("non-verified admission cannot carry membership proof")
        if self.status is not HandoffStatus.VERIFIED:
            _required(self.reason_code, "reason_code")


def _required(value: str | None, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _same_scope(owner: EquipmentOwnerPartition, partition: object, name: str) -> None:
    if any(getattr(partition, key) != getattr(owner, key) for key in ("tenant_id", "project_id", "owner_id")):
        raise ValueError(f"{name} partition does not match owner scope")


__all__ = [
    "AdmissionIntent", "CandidateOfferMembershipProof", "EquipmentAdmissionCommand", "EquipmentAdmissionResult", "EquipmentEvidenceBundle",
    "EvidenceIdempotencyKey",
    "EquipmentOwnerPartition", "EquipmentPromotionPartition", "EquipmentQuarantinePartition",
    "HandoffStatus", "PromotionStatus",
]
