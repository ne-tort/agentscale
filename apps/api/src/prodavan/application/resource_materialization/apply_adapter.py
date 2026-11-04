"""Durable production adapter for the ``AtomicMaterializationApplyPort`` seam.

MIG-RES-APPLYPORT-01. Before this module the Resource API had a working CRUD and
planning surface but no apply: ``application/resources/adapters/factory.py`` built
``apply_port=materialization_apply_port(session)``, which returns ``None`` until
somebody calls ``install_materialization_apply_port(provider)``, and nobody did.
Every ``POST .../materialization/apply`` therefore answered
``503 RESOURCE_APPLY_UNAVAILABLE``.

This module closes that gap with exactly three pieces:

1. :class:`ResourceMaterializationApplyPort` — the ``AtomicMaterializationApplyPort``
   implementation. It converts the port call into the ledger's ``ApplyPlan`` (a
   deterministic idempotency key derived from the plan inputs, see
   ``durable.contracts.apply_idempotency_key``) and delegates to
   ``ResourceMaterializationApplication`` / ``DurableMaterializationExecutor``. It
   owns no ledger logic of its own: attempts, head CAS, snapshot persistence,
   redacted audit and monotonic rollback stay in ``durable/``.
2. :class:`ResourceApplyEvidenceGate` (+ :class:`SqlAlchemyResourceApplyEvidenceReader`)
   — the ``DurableApplyGate`` used in production. It answers "may this plan run?"
   from **durable** state only and refuses fail-closed; it never invents a lease, an
   approval or an event that the database cannot prove.
3. :func:`build_resource_materialization_apply_port` — the single composition point
   the lifespan resource calls.

Guarantees preserved by delegation (they are properties of the executor and are
re-asserted here so the seam cannot quietly weaken them):

* **idempotency by key** — the same plan (project, expected generation, snapshot
  digest, operation-set digest) derives the same key, so a repeat returns the
  stored receipt (``REPLAY``) instead of claiming a second generation;
* **changed plan is a conflict** — a changed digest derives another key *and*
  presents a stale observed generation, so the head CAS refuses it instead of
  overwriting the stored receipt;
* **resource-wide CAS, empty plans included** — the fence lives on
  ``runtime_revision_heads``, so a plan with zero operations is fenced exactly like
  a populated one;
* **audit in the same transaction** — the ``started``/``finished`` rows are written
  with the attempt, the head and the snapshot; an audit failure aborts the whole
  transaction (``BLOCKED`` / ``audit_write_failed``) and grants nothing;
* **BLOCKED/CONFLICT grant no authority** — only ``APPLIED``/``REPLAY`` leave the
  port as a positive outcome;
* **rollback is monotonic** — it claims ``generation + 1`` on the head and links
  ``rollback_of_generation``; it never rewrites or decrements history.

Deliberately *not* provided here (documented blockers, never stubs):

* a Pod/workspace effect adapter (``ResourceOperationEffectPort``). With no effect
  boundary the executor refuses a populated plan with
  ``resource_effect_port_missing`` — it never acknowledges an apply that wrote
  nothing. Empty plans still exercise the full fence/audit/receipt path.
* a bridge-lease authority (``lease://`` reference, expiry, revocation). No durable
  publisher exists, so no lease is fabricated; ``require_bridge_lease`` stays off
  until the owner publishes one and the gate refuses the moment it is switched on
  without a resolvable lease.
* an approval/event linkage requirement for ordinary resource applies. The
  ``ApplyPlan`` carries no approval/event identity and a CRUD-triggered apply has no
  agent-run approval to point at, so demanding one would block every legitimate
  apply. The check is implemented and policy-driven, and approval *is* verified for
  the one command that claims an approval: rollback.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.resources.lifecycle_contracts import (
    ResourceLifecycle,
    VersionStatus,
)
from prodavan.application.resource_materialization.composition import (
    ResourceMaterializationApplication,
    RuntimeMaterializationFacade,
    build_resource_materialization_application,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
)
from prodavan.application.resource_materialization.durable.contracts import (
    ApplyPlan,
    DurableApplyResult,
    DurableRollbackCommand,
    HeadPhase,
    RedactedDiagnostic,
    apply_idempotency_key,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    ResourceOperationEffectPort,
    RollbackEffectPort,
)
from prodavan.application.resource_materialization.durable.heads import (
    SqlAlchemyRevisionHeadStore,
)
from prodavan.application.resource_materialization.durable.ports import (
    DurableApplyGate,
    DurableClockPort,
    SystemUtcClock,
)
from prodavan.application.resources.api.contracts import METADATA_LIFECYCLE
from prodavan.infrastructure.persistence.models.agent_control_plane import (
    AgentRunRow,
    ApprovalRequestRow,
)
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow
from prodavan.infrastructure.persistence.models.resources import (
    ResourceBindingRow,
    ResourceRow,
    ResourceVersionRow,
)

logger = logging.getLogger(__name__)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_RESOURCE_SOURCE_PREFIX = "resource:"
_PROJECT_SCOPE_KIND = "project"
_APPROVED = "approved"
_SOURCE_EVENT = "resource-materialization.apply"

#: Evidence kinds the durable schema cannot prove yet, with the reason. They are
#: policy-switchable but off in the production composition: switching one on
#: without an authority that resolves it makes every apply refuse, which is the
#: correct failure mode but not a working deployment.
EVIDENCE_AUTHORITY_BLOCKERS: tuple[tuple[str, str], ...] = (
    (
        "bridge_lease",
        "no durable bridge-lease publisher: nothing writes an opaque lease:// reference "
        "with an expiry/epoch for a project, so the gate cannot check one without "
        "inventing it (owner: runtime materialization / pod bridge zone)",
    ),
    (
        "approval_link",
        "ApplyPlan carries no approval identity and a CRUD-triggered resource apply has "
        "no agent-run approval to point at; requiring it would refuse every legitimate "
        "apply (owner: resource API command layer, needs an explicit approval column)",
    ),
    (
        "event_link",
        "platform_events rows are optional for resource commands, so an event cannot be "
        "required as apply authority without changing the resource command contract "
        "(owner: application/resources/api)",
    ),
    (
        "resource_effect",
        "no Pod/workspace effect adapter implements ResourceOperationEffectPort; a "
        "populated plan is refused by the executor with resource_effect_port_missing "
        "rather than acknowledged as written (owner: workspace/pod runtime zone)",
    ),
)


# --------------------------------------------------------------------------- #
# Evidence contracts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OperationGrounding:
    """Durable trace of one planned operation back to resource state.

    ``target`` is the operation target; everything else is read from the database,
    never from the plan. The gate refuses when the plan's provenance and the stored
    rows disagree, so a plan cannot name a version, target or binding it does not
    actually have.
    """

    target: str
    resource_id: str
    version_number: int | None
    resource_exists: bool
    resource_lifecycle: str
    resource_active_version: int | None
    version_exists: bool
    version_id: str | None
    version_status: str
    binding_present: bool
    binding_version_matches: bool
    provenance_version_matches: bool
    provenance_target_matches: bool

    @property
    def grounded(self) -> bool:
        return bool(
            self.resource_exists
            and self.resource_lifecycle == ResourceLifecycle.ACTIVE.value
            and self.resource_active_version is not None
            and self.version_number is not None
            and self.version_exists
            and self.version_status == VersionStatus.PUBLISHED.value
            and self.binding_present
            and self.binding_version_matches
            and self.provenance_version_matches
            and self.provenance_target_matches
        )


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    """One durable human decision, as stored in ``approval_requests``."""

    approval_id: str
    project_id: str
    status: str
    decided_at: datetime | None
    expires_at: datetime | None

    def usable(self, *, at: datetime) -> bool:
        if self.status != _APPROVED or self.decided_at is None:
            return False
        if self.expires_at is None:
            return True
        if at.tzinfo is None or at.utcoffset() is None:
            return False
        return self.expires_at > at


@dataclass(frozen=True, slots=True)
class ResourceApplyEvidence:
    """Durable, read-only authority snapshot for one plan.

    Every field is either read from a table or absent. There is no "assumed" value:
    an absent field is what the gate reports as missing evidence.
    """

    project_id: str
    resource_key: str
    head_generation: int = 0
    head_phase: str = HeadPhase.IDLE.value
    groundings: tuple[OperationGrounding, ...] = ()
    ungrounded_targets: tuple[str, ...] = ()
    approval: ApprovalEvidence | None = None
    event_id: str | None = None
    lease_reference: str | None = None
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    lease_revoked: bool = False


class ResourceApplyEvidenceSource(Protocol):
    """Read-only resolver of durable apply evidence for one plan."""

    async def read(self, *, plan: ApplyPlan) -> ResourceApplyEvidence | None: ...

    async def approval(
        self, *, project_id: str, approval_id: str
    ) -> ApprovalEvidence | None: ...


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResourceApplyEvidencePolicy:
    """Which evidence kinds are mandatory for this composition.

    The policy is explicit instead of implied: switching a requirement on is a
    deliberate decision that a durable authority exists to satisfy it. Fail-closed
    is the default for every requirement that is on.
    """

    require_resource_grounding: bool = True
    require_generation_epoch: bool = True
    require_approval_link: bool = False
    require_event_link: bool = False
    require_bridge_lease: bool = False

    @classmethod
    def for_resource_api(cls) -> "ResourceApplyEvidencePolicy":
        """The production policy for the Resource API apply path.

        Only evidence the durable schema can prove is required. The disabled
        requirements are listed with their owners in
        :data:`EVIDENCE_AUTHORITY_BLOCKERS`; none of them is silently satisfied.
        """

        return cls(
            require_resource_grounding=True,
            require_generation_epoch=True,
            require_approval_link=False,
            require_event_link=False,
            require_bridge_lease=False,
        )

    def required_kinds(self) -> tuple[str, ...]:
        mapping = (
            ("resource_grounding", self.require_resource_grounding),
            ("generation_epoch", self.require_generation_epoch),
            ("approval_link", self.require_approval_link),
            ("event_link", self.require_event_link),
            ("bridge_lease", self.require_bridge_lease),
        )
        return tuple(name for name, enabled in mapping if enabled)


# --------------------------------------------------------------------------- #
# Durable evidence reader
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class SqlAlchemyResourceApplyEvidenceReader:
    """Reads apply evidence from real tables through the application's own session.

    Every read is a plain ``SELECT``: no transaction is committed, nothing is
    written, and no Pod/HTTP call is made. A read failure is reported as ``None`` so
    the gate refuses instead of guessing.
    """

    session_factory: async_sessionmaker[AsyncSession]

    async def read(self, *, plan: ApplyPlan) -> ResourceApplyEvidence | None:
        try:
            async with self.session_factory() as session:
                head = await SqlAlchemyRevisionHeadStore().read(
                    session, resource_key=plan.resource_key
                )
                groundings: list[OperationGrounding] = []
                ungrounded: list[str] = []
                for operation in plan.operations:
                    grounding = await self._ground_operation(
                        session, plan=plan, operation=operation
                    )
                    if grounding is None:
                        ungrounded.append(operation.target)
                    else:
                        groundings.append(grounding)
                approval = await self._approved_for_project(
                    session, project_id=plan.project_id
                )
                event_id = await self._latest_event_id(
                    session, project_id=plan.project_id
                )
        except SQLAlchemyError:
            logger.warning("resource apply evidence read failed", extra={"reason": "apply_evidence_read_failed"})
            return None
        return ResourceApplyEvidence(
            project_id=plan.project_id,
            resource_key=plan.resource_key,
            head_generation=0 if head is None else head.generation,
            head_phase=HeadPhase.IDLE.value if head is None else head.phase.value,
            groundings=tuple(groundings),
            ungrounded_targets=tuple(ungrounded),
            approval=approval,
            event_id=event_id,
        )

    async def approval(
        self, *, project_id: str, approval_id: str
    ) -> ApprovalEvidence | None:
        if not isinstance(approval_id, str) or not approval_id.strip():
            return None
        try:
            async with self.session_factory() as session:
                row = await session.scalar(
                    select(ApprovalRequestRow)
                    .join(AgentRunRow, ApprovalRequestRow.run_id == AgentRunRow.id)
                    .where(
                        ApprovalRequestRow.id == approval_id,
                        AgentRunRow.project_id == project_id,
                    )
                )
        except SQLAlchemyError:
            return None
        if row is None:
            return None
        return ApprovalEvidence(
            approval_id=row.id,
            project_id=project_id,
            status=row.status,
            decided_at=row.decided_at,
            expires_at=row.expires_at,
        )

    # -- internals -----------------------------------------------------

    async def _ground_operation(
        self, session: AsyncSession, *, plan: ApplyPlan, operation: ResourceOperation
    ) -> OperationGrounding | None:
        provenance = operation.provenance
        if provenance is None or not str(provenance.source_type or "").startswith(
            _RESOURCE_SOURCE_PREFIX
        ):
            return None
        resource_id = str(provenance.source_id or "")
        if not resource_id:
            return None
        metadata: Mapping[str, object] = (
            provenance.metadata if isinstance(provenance.metadata, Mapping) else {}
        )
        version_number = metadata.get("version_number")
        version_number = version_number if isinstance(version_number, int) else None

        resource = await session.scalar(
            select(ResourceRow).where(ResourceRow.id == resource_id)
        )
        version = None
        if version_number is not None:
            version = await session.scalar(
                select(ResourceVersionRow).where(
                    ResourceVersionRow.resource_id == resource_id,
                    ResourceVersionRow.number == version_number,
                )
            )
        bindings = (
            await session.scalars(
                select(ResourceBindingRow).where(
                    ResourceBindingRow.resource_id == resource_id,
                    ResourceBindingRow.target_scope_kind == _PROJECT_SCOPE_KIND,
                    ResourceBindingRow.target_scope_id == plan.project_id,
                )
            )
        ).all()
        binding_version_matches = any(
            binding.version_number is not None
            and version_number is not None
            and binding.version_number == version_number
            for binding in bindings
        )
        lifecycle = ""
        active_version = None
        if resource is not None:
            raw_lifecycle = (resource.metadata_ or {}).get(METADATA_LIFECYCLE)
            lifecycle = (
                str(raw_lifecycle) if raw_lifecycle is not None else ResourceLifecycle.DRAFT.value
            )
            active_version = resource.active_version
        return OperationGrounding(
            target=operation.target,
            resource_id=resource_id,
            version_number=version_number,
            resource_exists=resource is not None,
            resource_lifecycle=lifecycle,
            resource_active_version=active_version,
            version_exists=version is not None,
            version_id=None if version is None else version.id,
            version_status="" if version is None else version.status,
            binding_present=bool(bindings),
            binding_version_matches=binding_version_matches,
            provenance_version_matches=bool(
                version is not None
                and provenance.source_revision
                and str(provenance.source_revision) == version.id
            ),
            provenance_target_matches=str(provenance.source_path or "") == operation.target,
        )

    @staticmethod
    async def _approved_for_project(
        session: AsyncSession, *, project_id: str
    ) -> ApprovalEvidence | None:
        row = await session.scalar(
            select(ApprovalRequestRow)
            .join(AgentRunRow, ApprovalRequestRow.run_id == AgentRunRow.id)
            .where(
                AgentRunRow.project_id == project_id,
                ApprovalRequestRow.status == _APPROVED,
            )
            .order_by(ApprovalRequestRow.decided_at.desc().nullslast())
            .limit(1)
        )
        if row is None:
            return None
        return ApprovalEvidence(
            approval_id=row.id,
            project_id=project_id,
            status=row.status,
            decided_at=row.decided_at,
            expires_at=row.expires_at,
        )

    @staticmethod
    async def _latest_event_id(session: AsyncSession, *, project_id: str) -> str | None:
        return await session.scalar(
            select(PlatformEventRow.id)
            .where(PlatformEventRow.project_id == project_id)
            .order_by(PlatformEventRow.created_at.desc())
            .limit(1)
        )


# --------------------------------------------------------------------------- #
# Evidence gate
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ResourceApplyEvidenceGate:
    """``DurableApplyGate`` over durable evidence, fail-closed by construction.

    The gate deliberately does **not** re-implement the generation CAS. A stale
    generation is the executor's conflict to report (``stale_generation_cas``), so
    pre-empting it here would turn a required ``CONFLICT`` into a generic block. What
    the gate does check about generations is the one thing a CAS cannot: that the
    plan advances the resource by exactly the next generation instead of skipping
    over unclaimed ones.
    """

    evidence: ResourceApplyEvidenceSource
    policy: ResourceApplyEvidencePolicy = ResourceApplyEvidencePolicy.for_resource_api()

    async def blockers(self, *, plan: ApplyPlan, at: datetime) -> tuple[str, ...]:
        if not isinstance(plan, ApplyPlan):
            return ("apply_plan_invalid",)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("apply time must be timezone-aware")
        expected_key = resource_key_for_project(plan.project_id)
        if plan.resource_key != expected_key:
            # The key is derived, never supplied; a mismatch means the plan was not
            # built by this resource's planner and cannot be fenced.
            return ("resource_key_mismatch",)
        try:
            evidence = await self.evidence.read(plan=plan)
        except Exception:
            return ("apply_evidence_port_error",)
        if evidence is None:
            return ("apply_evidence_unavailable",)
        if not isinstance(evidence, ResourceApplyEvidence):
            return ("apply_evidence_invalid",)
        if evidence.project_id != plan.project_id or evidence.resource_key != plan.resource_key:
            return ("apply_evidence_scope_mismatch",)

        out: list[str] = []
        policy = self.policy
        if policy.require_generation_epoch:
            out.extend(self._generation_blockers(plan=plan, evidence=evidence))
        if policy.require_resource_grounding:
            out.extend(self._grounding_blockers(plan=plan, evidence=evidence))
        if policy.require_approval_link:
            out.extend(self._approval_blockers(evidence=evidence, at=at))
        if policy.require_event_link and not evidence.event_id:
            out.append("event_link_missing")
        if policy.require_bridge_lease:
            out.extend(self._lease_blockers(evidence=evidence, at=at))
        return tuple(dict.fromkeys(out))

    # -- checks --------------------------------------------------------

    @staticmethod
    def _generation_blockers(
        *, plan: ApplyPlan, evidence: ResourceApplyEvidence
    ) -> tuple[str, ...]:
        out: list[str] = []
        if plan.expected_generation != plan.observed_generation + 1:
            # Guaranteed by ``ApplyPlan``; re-asserted so a future contract change
            # cannot silently produce a multi-generation plan.
            out.append("generation_not_single_step")
        if evidence.head_generation < 0 or evidence.head_generation > plan.expected_generation:
            # The head is at or past the generation this plan wants to claim: the
            # plan is late. It is not a "gap", so let the executor answer with its
            # CAS conflict unless the plan really skips generations.
            return tuple(out)
        if evidence.head_generation + 1 < plan.expected_generation:
            out.append("generation_gap")
        return tuple(out)

    @staticmethod
    def _grounding_blockers(
        *, plan: ApplyPlan, evidence: ResourceApplyEvidence
    ) -> tuple[str, ...]:
        if not plan.operations:
            # An empty plan still needs no resource evidence; the head fences it.
            return ()
        if evidence.ungrounded_targets:
            return ("operation_resource_provenance_missing",)
        out: list[str] = []
        for grounding in evidence.groundings:
            if grounding.grounded:
                continue
            if not grounding.resource_exists:
                out.append("resource_evidence_missing")
                continue
            if grounding.resource_lifecycle != ResourceLifecycle.ACTIVE.value:
                out.append("resource_not_active")
            if grounding.resource_active_version is None:
                out.append("resource_active_version_missing")
            if grounding.version_number is None:
                out.append("resource_version_evidence_missing")
            elif not grounding.version_exists:
                out.append("resource_version_missing")
            elif grounding.version_status != VersionStatus.PUBLISHED.value:
                out.append("resource_version_not_published")
            if not grounding.binding_present:
                out.append("resource_binding_missing")
            elif not grounding.binding_version_matches:
                out.append("resource_binding_version_mismatch")
            if not grounding.provenance_version_matches:
                out.append("operation_provenance_version_mismatch")
            if not grounding.provenance_target_matches:
                out.append("operation_provenance_target_mismatch")
        if len(evidence.groundings) + len(evidence.ungrounded_targets) != len(plan.operations):
            # Grounding count must match the plan exactly; anything else means the
            # evidence was not resolved for this plan.
            out.append("apply_evidence_incomplete")
        return tuple(dict.fromkeys(out))

    @staticmethod
    def _approval_blockers(
        *, evidence: ResourceApplyEvidence, at: datetime
    ) -> tuple[str, ...]:
        approval = evidence.approval
        if approval is None:
            return ("approval_link_missing",)
        if approval.status != _APPROVED:
            return ("approval_link_not_approved",)
        if approval.decided_at is None:
            return ("approval_link_undecided",)
        if not approval.usable(at=at):
            return ("approval_link_expired",)
        return ()

    @staticmethod
    def _lease_blockers(
        *, evidence: ResourceApplyEvidence, at: datetime
    ) -> tuple[str, ...]:
        reference = (evidence.lease_reference or "").strip()
        if not reference.startswith(("lease://", "bridge-lease://")):
            return ("bridge_lease_missing",)
        out: list[str] = []
        if evidence.lease_epoch < 1:
            out.append("bridge_lease_epoch_missing")
        if evidence.lease_expires_at is None:
            out.append("bridge_lease_expiry_missing")
        elif evidence.lease_expires_at <= at:
            out.append("bridge_lease_expired")
        if evidence.lease_revoked:
            out.append("bridge_lease_revoked")
        return tuple(out)


# --------------------------------------------------------------------------- #
# The published port
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ResourceMaterializationApplyPort:
    """``AtomicMaterializationApplyPort`` over the durable executor.

    The port is intentionally thin: it validates the request shape, derives the
    deterministic plan identity, delegates one fenced apply, and maps the ledger
    outcome onto the port's four-value contract. It never raises for a refusal, so
    the HTTP layer always has a typed outcome to translate.
    """

    application: ResourceMaterializationApplication
    evidence: ResourceApplyEvidenceSource | None = None
    clock: DurableClockPort | None = None

    async def apply_cas(
        self,
        *,
        project_id: str,
        expected_generation: int,
        snapshot_digest: str,
        operation_set_digest: str,
        operations: tuple[ResourceOperation, ...],
    ) -> MaterializationApplyOutcome:
        plan = self.build_plan(
            project_id=project_id,
            expected_generation=expected_generation,
            snapshot_digest=snapshot_digest,
            operation_set_digest=operation_set_digest,
            operations=operations,
        )
        if plan is None:
            return MaterializationApplyOutcome.BLOCKED
        result = await self.apply_plan(plan)
        return MaterializationApplyOutcome(result.outcome)

    def build_plan(
        self,
        *,
        project_id: str,
        expected_generation: int,
        snapshot_digest: str,
        operation_set_digest: str,
        operations: Sequence[ResourceOperation],
        correlation_id: str | None = None,
        rollback_of_generation: int | None = None,
        created_by: str | None = None,
    ) -> ApplyPlan | None:
        """Derive the fenced plan, or ``None`` when the request is not admissible.

        ``None`` is the fail-closed answer for a malformed request: the port contract
        has no error channel, and raising would surface as a 500 instead of a typed
        refusal.
        """
        if not isinstance(project_id, str) or not project_id.strip() or any(
            character.isspace() for character in project_id
        ):
            _log_refusal("apply_request_invalid", "project_id")
            return None
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 1
        ):
            _log_refusal("apply_request_invalid", "expected_generation")
            return None
        for value, name in (
            (snapshot_digest, "snapshot_digest"),
            (operation_set_digest, "operation_set_digest"),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                _log_refusal("apply_request_invalid", name)
                return None
        if not isinstance(operations, (tuple, list)) or any(
            not isinstance(item, ResourceOperation) for item in operations
        ):
            _log_refusal("apply_request_invalid", "operations")
            return None
        try:
            return ApplyPlan(
                project_id=project_id,
                expected_generation=expected_generation,
                snapshot_digest=snapshot_digest,
                operation_set_digest=operation_set_digest,
                operations=tuple(operations),
                correlation_id=correlation_id
                or f"resource-apply:{project_id}:{expected_generation}",
                idempotency_key=apply_idempotency_key(
                    project_id=project_id,
                    expected_generation=expected_generation,
                    snapshot_digest=snapshot_digest,
                    operation_set_digest=operation_set_digest,
                    rollback_of_generation=rollback_of_generation,
                ),
                rollback_of_generation=rollback_of_generation,
                source_event=_SOURCE_EVENT,
                created_by=created_by,
            )
        except ValueError:
            _log_refusal("apply_plan_rejected", "plan_contract")
            return None

    async def apply_plan(self, plan: ApplyPlan) -> DurableApplyResult:
        """Run exactly one fenced apply; a refusal is a result, never an exception."""
        if not isinstance(plan, ApplyPlan):
            return _blocked_result("apply_plan_invalid", phase="apply")
        try:
            result = await self.application.apply_plan(plan)
        except Exception:  # noqa: BLE001 - fail closed, never grant authority
            logger.exception("resource materialization apply crashed")
            return _blocked_result("apply_executor_error", phase="apply")
        if result.outcome in {
            MaterializationApplyOutcome.APPLIED,
            MaterializationApplyOutcome.REPLAY,
        }:
            logger.info(
                "resource materialization apply settled",
                extra={
                    "project_id": plan.project_id,
                    "generation": plan.expected_generation,
                    "outcome": str(result.outcome),
                    "reason": result.reason_code,
                },
            )
        else:
            _log_refusal(result.reason_code, "executor")
        return result

    async def rollback(self, command: DurableRollbackCommand) -> DurableApplyResult:
        """Approved rollback; the approval must be provable, not just present.

        ``DurableRollbackCommand.approval_id`` is only checked for being non-empty by
        the executor. Whenever an evidence source is wired, this port verifies that
        the id names an approved, non-expired decision of *this* project before any
        generation is claimed.
        """
        if not isinstance(command, DurableRollbackCommand):
            return _blocked_result("rollback_command_invalid", phase="rollback")
        if self.evidence is not None:
            now = await self._now()
            approval = None
            if command.approval_id:
                try:
                    approval = await self.evidence.approval(
                        project_id=command.project_id,
                        approval_id=command.approval_id,
                    )
                except Exception:  # noqa: BLE001
                    return _blocked_result("rollback_approval_unverified", phase="rollback")
            if approval is None or not approval.usable(at=now):
                return _blocked_result("rollback_approval_unverified", phase="rollback")
        try:
            result = await self.application.rollback(command)
        except Exception:  # noqa: BLE001
            logger.exception("resource materialization rollback crashed")
            return _blocked_result("rollback_executor_error", phase="rollback")
        return result

    async def replay(
        self, *, project_id: str, idempotency_key: str
    ) -> DurableApplyResult | None:
        """Read a stored receipt without claiming anything."""
        if not isinstance(project_id, str) or not isinstance(idempotency_key, str):
            return None
        return await self.application.executor.replay(
            project_id=project_id, idempotency_key=idempotency_key
        )

    async def history(
        self, *, project_id: str, limit: int = 50
    ) -> tuple:
        return await self.application.history(project_id=project_id, limit=limit)

    async def generation(self, *, project_id: str) -> int:
        return await self.application.generation(project_id=project_id)

    def for_session(
        self, session: AsyncSession | None = None
    ) -> "ResourceMaterializationApplyPort | None":
        """``ApplyPortProvider`` form: the durable executor owns its own sessions.

        The Resource API hands its request session in; the ledger cannot borrow it
        because the apply must run in its own transaction (head CAS, attempt, audit).
        An unexpected session type therefore refuses with ``None`` (the documented
        ``503 RESOURCE_APPLY_UNAVAILABLE`` answer) instead of silently serving
        another store.
        """
        if session is not None and not isinstance(session, AsyncSession):
            return None
        return self

    async def _now(self) -> datetime:
        clock = self.clock or self.application.clock
        now = await clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("apply clock must be timezone-aware")
        return now


def build_resource_materialization_apply_port(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    policy: ResourceApplyEvidencePolicy | None = None,
    gate: DurableApplyGate | None = None,
    evidence: ResourceApplyEvidenceSource | None = None,
    effects: ResourceOperationEffectPort | None = None,
    rollback_effects: RollbackEffectPort | None = None,
    clock: DurableClockPort | None = None,
    runtime_service: RuntimeMaterializationFacade | None = None,
) -> ResourceMaterializationApplyPort:
    """Compose the production apply port; nothing here defaults to a stub.

    ``effects``/``rollback_effects`` stay ``None`` until a Pod/workspace adapter
    exists: the executor then refuses a populated plan with
    ``resource_effect_port_missing`` instead of acknowledging an empty write. Pass a
    real adapter here to enable effects — the port itself does not change.
    """
    resolved_clock = clock or SystemUtcClock()
    resolved_evidence = evidence or SqlAlchemyResourceApplyEvidenceReader(
        session_factory=session_factory
    )
    resolved_gate = gate or ResourceApplyEvidenceGate(
        evidence=resolved_evidence,
        policy=policy or ResourceApplyEvidencePolicy.for_resource_api(),
    )
    application = build_resource_materialization_application(
        session_factory=session_factory,
        gate=resolved_gate,
        effects=effects,
        rollback_effects=rollback_effects,
        clock=resolved_clock,
        runtime_service=runtime_service,
    )
    return ResourceMaterializationApplyPort(
        application=application,
        evidence=resolved_evidence,
        clock=resolved_clock,
    )


def _blocked_result(reason_code: str, *, phase: str = "apply") -> DurableApplyResult:
    safe_code = reason_code if _CODE.fullmatch(reason_code) else "apply_refused"
    return DurableApplyResult(
        outcome=MaterializationApplyOutcome.BLOCKED,
        reason_code=safe_code,
        diagnostics=(RedactedDiagnostic(phase, safe_code),),
    )


def _log_refusal(reason_code: str, phase: str) -> None:
    """Log a refusal with a redacted code only; never payloads, never digests."""

    safe_code = reason_code if _CODE.fullmatch(reason_code) else "apply_refused"
    logger.warning(
        "resource materialization apply refused",
        extra={"phase": phase, "reason": safe_code},
    )


__all__ = [
    "EVIDENCE_AUTHORITY_BLOCKERS",
    "ApprovalEvidence",
    "OperationGrounding",
    "ResourceApplyEvidence",
    "ResourceApplyEvidenceGate",
    "ResourceApplyEvidencePolicy",
    "ResourceApplyEvidenceSource",
    "ResourceMaterializationApplyPort",
    "SqlAlchemyResourceApplyEvidenceReader",
    "build_resource_materialization_apply_port",
]
