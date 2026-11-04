"""Doubles for the resource-apply-port acceptance artifacts.

These doubles exist **only** in the test tree. The production path never falls back
to them: ``apply_adapter`` refuses with ``BLOCKED`` when a collaborator is missing
rather than accepting a stub (see ``EVIDENCE_AUTHORITY_BLOCKERS``).

Two kinds are provided:

* ``RecordingExecutor`` — a ledger double of ``DurableMaterializationExecutor``'s
  *surface* (``execute``/``rollback``/``replay``/``history``/``project_generation``).
  It records what the published port asked for and answers with the documented
  ledger semantics, so the port's derivation, mapping and refusal behaviour can be
  asserted without a database. It deliberately does **not** re-implement the CAS;
  the real fence is asserted in the DB-gated module.
* ``StaticEvidenceSource`` — a read-only evidence double used to drive the gate
  through its blocker matrix, and to prove that an approval must be provable (not
  merely present) before a rollback claims a generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.resource_materialization.apply_adapter import (
    ApprovalEvidence,
    OperationGrounding,
    ResourceApplyEvidence,
    ResourceApplyEvidenceSource,
)
from prodavan.application.resource_materialization.composition import (
    ResourceMaterializationApplication,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)
from prodavan.application.resource_materialization.durable.contracts import (
    ApplyPlan,
    AttemptSnapshot,
    DurableApplyResult,
    DurableRollbackCommand,
    HeadPhase,
    HeadSnapshot,
    RedactedDiagnostic,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.ports import SystemUtcClock
from prodavan.domain.materialization import OperationKind, SourceProvenance

PROJECT_ID = "prj_applyport"
OTHER_PROJECT_ID = "prj_applyport_other"
RESOURCE_ID = "res_applyport"
VERSION_ID = "rver_applyport_1"
VERSION_NUMBER = 2
TARGET = "prompts/apply-port.md"


def digest(seed: str) -> str:
    """Deterministic sha256-shaped value; the ledger only validates the shape."""

    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


SNAPSHOT_DIGEST = digest("snapshot:1")
OPERATION_SET_DIGEST = digest("operations:1")
OTHER_SNAPSHOT_DIGEST = digest("snapshot:2")


def operation(
    *,
    target: str = TARGET,
    resource_id: str = RESOURCE_ID,
    version_number: int = VERSION_NUMBER,
    version_id: str = VERSION_ID,
    required: bool = True,
    with_provenance: bool = True,
) -> ResourceOperation:
    provenance = (
        SourceProvenance(
            source_type="resource:prompt",
            source_id=resource_id,
            source_generation=1,
            source_path=target,
            source_revision=version_id,
            metadata={"binding_id": "rbind_applyport", "version_number": version_number},
        )
        if with_provenance
        else None
    )
    return ResourceOperation(
        kind=OperationKind.PROMPT,
        target=target,
        payload={"text": "materialized"},
        provenance=provenance,
        required=required,
    )


def grounding(
    *,
    target: str = TARGET,
    resource_exists: bool = True,
    lifecycle: str = "active",
    active_version: int | None = VERSION_NUMBER,
    version_exists: bool = True,
    version_status: str = "published",
    binding_present: bool = True,
    binding_version_matches: bool = True,
    provenance_version_matches: bool = True,
    provenance_target_matches: bool = True,
) -> OperationGrounding:
    return OperationGrounding(
        target=target,
        resource_id=RESOURCE_ID,
        version_number=VERSION_NUMBER,
        resource_exists=resource_exists,
        resource_lifecycle=lifecycle,
        resource_active_version=active_version,
        version_exists=version_exists,
        version_id=VERSION_ID if version_exists else None,
        version_status=version_status,
        binding_present=binding_present,
        binding_version_matches=binding_version_matches,
        provenance_version_matches=provenance_version_matches,
        provenance_target_matches=provenance_target_matches,
    )


def evidence(
    *,
    project_id: str = PROJECT_ID,
    head_generation: int = 0,
    groundings: tuple[OperationGrounding, ...] = (),
    ungrounded_targets: tuple[str, ...] = (),
    approval: ApprovalEvidence | None = None,
    event_id: str | None = None,
    lease_reference: str | None = None,
    lease_epoch: int = 0,
    lease_expires_at: datetime | None = None,
    lease_revoked: bool = False,
) -> ResourceApplyEvidence:
    return ResourceApplyEvidence(
        project_id=project_id,
        resource_key=resource_key_for_project(project_id),
        head_generation=head_generation,
        head_phase="applied" if head_generation else HeadPhase.IDLE.value,
        groundings=groundings,
        ungrounded_targets=ungrounded_targets,
        approval=approval,
        event_id=event_id,
        lease_reference=lease_reference,
        lease_epoch=lease_epoch,
        lease_expires_at=lease_expires_at,
        lease_revoked=lease_revoked,
    )


def approved(
    *,
    approval_id: str = "apr_applyport",
    project_id: str = PROJECT_ID,
    status: str = "approved",
    decided_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ApprovalEvidence:
    return ApprovalEvidence(
        approval_id=approval_id,
        project_id=project_id,
        status=status,
        decided_at=decided_at or datetime.now(UTC) - timedelta(minutes=5),
        expires_at=expires_at,
    )


@dataclass
class StaticEvidenceSource:
    """Read-only evidence double; ``None``/exception models an unreadable store.

    It intentionally does *not* inherit ``ResourceApplyEvidenceSource``: the port is
    a ``Protocol``, so structural conformance is exactly what should be proven here.
    """

    payload: ResourceApplyEvidence | None = None
    approvals: dict[str, ApprovalEvidence] = field(default_factory=dict)
    fail: bool = False
    reads: int = 0

    async def read(self, *, plan: ApplyPlan) -> ResourceApplyEvidence | None:
        self.reads += 1
        if self.fail:
            raise RuntimeError("evidence store is down")
        if self.payload is not None and self.payload.project_id != plan.project_id:
            return self.payload
        return self.payload

    async def approval(
        self, *, project_id: str, approval_id: str
    ) -> ApprovalEvidence | None:
        if self.fail:
            return None
        return self.approvals.get(approval_id)


@dataclass
class RecordingExecutor:
    """Ledger surface double with the documented outcome semantics.

    * the first claim of a generation is ``APPLIED`` and advances the head;
    * repeating the *same* key returns the stored receipt as ``REPLAY``;
    * any other plan whose observed generation was already consumed is a
      ``CONFLICT`` (``stale_generation_cas``) — the real head CAS refuses it;
    * a queued scripted result (``scripted``) short-circuits the above so negative
      paths such as ``BLOCKED``/``FAILED`` can be exercised verbatim.
    """

    head_generation: int = 0
    plans: list[ApplyPlan] = field(default_factory=list)
    commands: list[DurableRollbackCommand] = field(default_factory=list)
    receipts: dict[str, DurableApplyResult] = field(default_factory=dict)
    scripted: list[DurableApplyResult] = field(default_factory=list)
    raise_on_execute: bool = False
    raise_on_rollback: bool = False
    replay_calls: list[tuple[str, str]] = field(default_factory=list)

    async def execute(self, plan: ApplyPlan) -> DurableApplyResult:
        self.plans.append(plan)
        if self.raise_on_execute:
            raise RuntimeError("ledger exploded with a secret=do-not-leak")
        if self.scripted:
            return self.scripted.pop(0)
        stored = self.receipts.get(plan.idempotency_key)
        if stored is not None:
            return DurableApplyResult(
                outcome=stored.outcome,
                reason_code="idempotent_replay",
                attempt=stored.attempt,
                head=self._head(),
                replayed=True,
            )
        if plan.observed_generation != self.head_generation:
            return DurableApplyResult(
                outcome=MaterializationApplyOutcome.CONFLICT,
                reason_code="stale_generation_cas",
                head=self._head(),
                diagnostics=(RedactedDiagnostic("apply", "stale_generation_cas"),),
            )
        self.head_generation += 1
        result = DurableApplyResult(
            outcome=MaterializationApplyOutcome.APPLIED,
            reason_code="applied",
            head=self._head(),
        )
        self.receipts[plan.idempotency_key] = result
        return result

    async def rollback(self, command: DurableRollbackCommand) -> DurableApplyResult:
        self.commands.append(command)
        if self.raise_on_rollback:
            raise RuntimeError("rollback exploded")
        if self.scripted:
            return self.scripted.pop(0)
        if not command.approval_id:
            return DurableApplyResult(
                outcome=MaterializationApplyOutcome.BLOCKED,
                reason_code="rollback_approval_missing",
            )
        if command.expected_generation != self.head_generation:
            return DurableApplyResult(
                outcome=MaterializationApplyOutcome.BLOCKED,
                reason_code="generation_conflict",
            )
        self.head_generation += 1
        return DurableApplyResult(
            outcome=MaterializationApplyOutcome.APPLIED,
            reason_code="rolled_back",
            head=self._head(),
        )

    async def replay(
        self, *, project_id: str, idempotency_key: str
    ) -> DurableApplyResult | None:
        self.replay_calls.append((project_id, idempotency_key))
        return self.receipts.get(idempotency_key)

    async def history(
        self, *, project_id: str, limit: int = 50
    ) -> tuple[AttemptSnapshot, ...]:
        return ()

    async def project_generation(self, *, project_id: str) -> int:
        return self.head_generation

    # -- helpers -------------------------------------------------------

    def _head(self) -> HeadSnapshot:
        return HeadSnapshot(
            resource_key=resource_key_for_project(PROJECT_ID),
            project_id=PROJECT_ID,
            generation=self.head_generation,
            phase=HeadPhase.APPLIED if self.head_generation else HeadPhase.IDLE,
            fence_token=None if not self.head_generation else f"fence-{self.head_generation}",
            revision_id=None,
            applied_revision_id=None,
            applied_desired_digest=None,
            ownership_digest=None,
        )


def application_with(executor: RecordingExecutor) -> ResourceMaterializationApplication:
    """A real ``ResourceMaterializationApplication`` over the recording ledger.

    Using the production facade (not a bespoke double) means the acceptance check
    also covers the composition contract: ``apply_plan`` → ``executor.execute``.
    """

    return ResourceMaterializationApplication(executor=executor, clock=SystemUtcClock())


def blocked(reason: str = "apply_blocked") -> DurableApplyResult:
    return DurableApplyResult(
        outcome=MaterializationApplyOutcome.BLOCKED,
        reason_code=reason,
        diagnostics=(RedactedDiagnostic("apply", reason),),
    )
