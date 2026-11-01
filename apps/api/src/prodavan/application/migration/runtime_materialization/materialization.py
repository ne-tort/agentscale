"""Deterministic, immutable desired/applied runtime materialization contract.

Adapters are intentionally absent: promotion requires durable CAS, bridge lease,
projected-secret reference, image provenance, and agent-run/approval/event linkage.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


class PlanAction(StrEnum):
    CREATE = "create"
    NOOP = "noop"
    REPLACE = "replace"
    BLOCKED = "blocked"


class ApplyOutcome(StrEnum):
    APPLIED = "applied"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_kind: str
    source_id: str
    source_revision: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.source_kind, self.source_id, self.source_revision, self.correlation_id)):
            raise ValueError("complete source provenance and correlation are required")


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    bridge_lease_ref: str | None = None
    projected_secret_ref: str | None = None
    image_digest: str | None = None
    agent_run_id: str | None = None
    approval_id: str | None = None
    event_id: str | None = None
    contracts_ready: bool = False

    @property
    def blockers(self) -> tuple[str, ...]:
        checks = (
            (self.contracts_ready, "upstream_resource_contract_missing"),
            (bool(self.bridge_lease_ref), "bridge_lease_missing"),
            (bool(self.projected_secret_ref), "projected_secret_reference_missing"),
            (bool(self.image_digest and self.image_digest.startswith("sha256:") and len(self.image_digest) == 71), "immutable_image_digest_missing"),
            (bool(self.agent_run_id), "agent_run_link_missing"),
            (bool(self.approval_id), "approval_link_missing"),
            (bool(self.event_id), "event_link_missing"),
        )
        return tuple(reason for ok, reason in checks if not ok)


@dataclass(frozen=True, slots=True)
class DesiredRevision:
    resource_key: str
    revision: int
    spec: Mapping[str, object]
    provenance: Provenance
    evidence: DependencyEvidence
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.resource_key.strip() or self.revision < 1:
            raise ValueError("resource key and positive desired revision are required")
        computed = _digest({"resource_key": self.resource_key, "revision": self.revision,
                            "spec": dict(self.spec), "provenance": {
                                "source_kind": self.provenance.source_kind,
                                "source_id": self.provenance.source_id,
                                "source_revision": self.provenance.source_revision,
                                "correlation_id": self.provenance.correlation_id}})
        if self.digest and self.digest != computed:
            raise ValueError("desired revision digest mismatch")
        object.__setattr__(self, "digest", computed)


@dataclass(frozen=True, slots=True)
class AppliedRevision:
    resource_key: str
    desired_revision: int
    desired_digest: str
    generation: int
    image_digest: str
    plan_digest: str
    idempotency_key: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.desired_revision < 1 or self.generation < 1 or not self.idempotency_key.strip():
            raise ValueError("invalid applied revision")


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    resource_key: str
    action: PlanAction
    expected_generation: int
    next_generation: int
    desired_revision: int
    desired_digest: str
    blockers: tuple[str, ...]
    plan_digest: str
    idempotency_key: str


class AppliedRevisionStore(Protocol):
    async def get(self, resource_key: str) -> AppliedRevision | None: ...
    async def apply_cas(self, *, expected_generation: int, revision: AppliedRevision) -> bool: ...


def plan_materialization(desired: DesiredRevision, current: AppliedRevision | None,
                         *, idempotency_key: str) -> MaterializationPlan:
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    generation = current.generation if current else 0
    blockers = desired.evidence.blockers
    if blockers:
        action = PlanAction.BLOCKED
    elif current is None:
        action = PlanAction.CREATE
    elif current.desired_digest == desired.digest:
        action = PlanAction.NOOP
    else:
        action = PlanAction.REPLACE
    next_generation = generation + (1 if action in {PlanAction.CREATE, PlanAction.REPLACE} else 0)
    canonical = {"resource_key": desired.resource_key, "action": action.value,
                 "expected_generation": generation, "next_generation": next_generation,
                 "desired_revision": desired.revision, "desired_digest": desired.digest,
                 "blockers": blockers, "idempotency_key": idempotency_key}
    return MaterializationPlan(desired.resource_key, action, generation, next_generation,
                               desired.revision, desired.digest, blockers, _digest(canonical), idempotency_key)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    outcome: ApplyOutcome
    applied: AppliedRevision | None
    reason_code: str


async def apply_materialization(desired: DesiredRevision, plan: MaterializationPlan,
                                store: AppliedRevisionStore) -> ApplyResult:
    if plan.resource_key != desired.resource_key or plan.desired_digest != desired.digest:
        return ApplyResult(ApplyOutcome.CONFLICT, None, "plan_binding_mismatch")
    if plan.action is PlanAction.BLOCKED or plan.blockers:
        return ApplyResult(ApplyOutcome.BLOCKED, None, plan.blockers[0] if plan.blockers else "dependency_gate_missing")
    current = await store.get(desired.resource_key)
    if current and current.idempotency_key == plan.idempotency_key:
        if current.plan_digest == plan.plan_digest:
            return ApplyResult(ApplyOutcome.REPLAY, current, "idempotent_replay")
        return ApplyResult(ApplyOutcome.CONFLICT, None, "idempotency_key_reused")
    observed = current.generation if current else 0
    if observed != plan.expected_generation:
        return ApplyResult(ApplyOutcome.CONFLICT, None, "generation_conflict")
    if plan.action is PlanAction.NOOP:
        if current and current.desired_digest == desired.digest:
            return ApplyResult(ApplyOutcome.REPLAY, current, "already_materialized")
        return ApplyResult(ApplyOutcome.CONFLICT, None, "noop_state_mismatch")
    evidence = desired.evidence
    candidate = AppliedRevision(desired.resource_key, desired.revision, desired.digest,
                                plan.next_generation, evidence.image_digest or "", plan.plan_digest,
                                plan.idempotency_key, desired.provenance)
    if not await store.apply_cas(expected_generation=plan.expected_generation, revision=candidate):
        return ApplyResult(ApplyOutcome.CONFLICT, None, "generation_cas_conflict")
    return ApplyResult(ApplyOutcome.APPLIED, candidate, "applied")


@dataclass(frozen=True, slots=True)
class RollbackCommand:
    resource_key: str
    target_applied_revision: int
    expected_generation: int
    approval_id: str | None
    idempotency_key: str

    def blockers(self, evidence: DependencyEvidence) -> tuple[str, ...]:
        missing = list(evidence.blockers)
        if not self.approval_id:
            missing.append("rollback_approval_missing")
        if self.target_applied_revision < 1 or self.expected_generation < 1:
            missing.append("rollback_target_or_generation_invalid")
        if not self.idempotency_key.strip():
            missing.append("rollback_idempotency_key_missing")
        return tuple(missing)
