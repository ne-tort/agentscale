"""Read-only legacy resolvers and atomic ingress persistence foundation.

The adapter owns one SQLAlchemy unit of work for claim, task intent, AgentRun,
ordered event, target outbox state/enqueue, and audit evidence. It does not
register models or workers and never mutates legacy queue rows.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.automation_ingress.ingress_service import (
    ClaimResult,
    ClaimStatus,
    DefinitionIdentity,
    DefinitionProof,
    EndpointMapping,
    EndpointResolution,
    EventLink,
    GateEvidence,
    IngressClaim,
    ProjectAssertion,
    ProjectTrigger,
    Quarantine,
    QuarantineReceipt,
    ResolutionStatus,
    RunLink,
    ScopeProof,
    SourceKind,
    SubscriptionIdentity,
    TriggerResolution,
)
from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow, ProjectTriggerRow
from prodavan.infrastructure.persistence.automation_migration.models import (
    AutomationDefinitionRow,
    AutomationDefinitionVersionRow,
    AutomationGateEvidenceRow,
    AutomationIngressAuditRow,
    AutomationIngressClaimRow,
    AutomationIngressEventRow,
    AutomationIngressMappingRow,
    AutomationOutboxEnqueueIntentRow,
    AutomationOutboxEventRow,
    AutomationOutboxStateRow,
    AutomationProjectScopeEvidenceRow,
    AutomationProjectTriggerEvidenceRow,
    AutomationTaskIntentRow,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _digest(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8", errors="strict")).hexdigest()


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class SqlAlchemyAutomationIngressRepository:
    """Concrete MIG-AUTO-01 persistence adapter; no live binding is supplied."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, **kwargs: Any) -> Any:
        """Structural resolver adapter for the four distinct application ports."""
        if "trigger_id" in kwargs:
            return await self.resolve_trigger(trigger_id=kwargs["trigger_id"])
        if "assertion" in kwargs:
            return await self.resolve_scope(assertion=kwargs["assertion"])
        if "tenant_id" in kwargs:
            return await self.resolve_definition(
                tenant_id=kwargs["tenant_id"], project_id=kwargs["project_id"],
                identity=kwargs["identity"], snapshot_id=kwargs["snapshot_id"],
            )
        if "identity" in kwargs and "route" in kwargs:
            return await self.resolve_endpoint(identity=kwargs["identity"], route=kwargs["route"])
        raise TypeError("unsupported automation migration resolver request")

    async def resolve_endpoint(self, *, identity: SubscriptionIdentity, route: str) -> EndpointResolution:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(AutomationIngressMappingRow).where(
                AutomationIngressMappingRow.source_id == identity.source_id,
                AutomationIngressMappingRow.subscription_id == identity.subscription_id,
                AutomationIngressMappingRow.enabled.is_(True),
                AutomationIngressMappingRow.inbound_enabled.is_(True),
            ))).all()
        matches = [row for row in rows if route in (row.routes or [])]
        if not matches:
            return EndpointResolution(ResolutionStatus.MISSING)
        if len(matches) != 1:
            return EndpointResolution(ResolutionStatus.AMBIGUOUS)
        row = matches[0]
        mapping = EndpointMapping(
            mapping_id=row.mapping_id,
            subscription=identity,
            owner=ProjectAssertion(row.project_id, row.tenant_id),
            endpoint_id=row.endpoint_id,
            automation_id=row.automation_id,
            actor_id=row.actor_id,
            definition=DefinitionIdentity(row.definition_id, row.version_id, row.artifact_digest),
            secret_ref=row.secret_ref,
            schema_version=row.schema_version,
            routes=tuple(row.routes or ()),
            event_types=tuple(row.event_types or ()),
            enabled=row.enabled,
            inbound_enabled=row.inbound_enabled,
        )
        return EndpointResolution(ResolutionStatus.RESOLVED, mapping, f"mapping:{row.mapping_id}")

    async def resolve_scope(self, *, assertion: ProjectAssertion) -> ScopeProof | None:
        async with self._session_factory() as session:
            project = await session.scalar(select(ProjectRow).where(ProjectRow.id == assertion.project_id))
            if project is None or (assertion.tenant_id is not None and project.company_id != assertion.tenant_id):
                return None
            rows = (await session.scalars(select(AutomationProjectScopeEvidenceRow).where(
                AutomationProjectScopeEvidenceRow.project_id == assertion.project_id,
                AutomationProjectScopeEvidenceRow.tenant_id == project.company_id,
                AutomationProjectScopeEvidenceRow.active.is_(True),
                AutomationProjectScopeEvidenceRow.parent_compatible.is_(True),
            ))).all()
        if len(rows) != 1:
            return None
        row = rows[0]
        return ScopeProof(
            row.tenant_id, row.project_id, row.scope_id, row.parent_proof_id,
            row.snapshot_id, row.gate_record_id, row.active, row.parent_compatible,
        )

    async def resolve_definition(
        self, *, tenant_id: str, project_id: str, identity: DefinitionIdentity, snapshot_id: str,
    ) -> DefinitionProof | None:
        async with self._session_factory() as session:
            definition = await session.scalar(select(AutomationDefinitionRow).where(
                AutomationDefinitionRow.tenant_id == tenant_id,
                AutomationDefinitionRow.project_id == project_id,
                AutomationDefinitionRow.id == identity.definition_id,
                AutomationDefinitionRow.lifecycle == "active",
            ))
            if definition is None or definition.source_snapshot_id != snapshot_id:
                return None
            version = await session.scalar(select(AutomationDefinitionVersionRow).where(
                AutomationDefinitionVersionRow.tenant_id == tenant_id,
                AutomationDefinitionVersionRow.project_id == project_id,
                AutomationDefinitionVersionRow.automation_id == definition.automation_id,
                AutomationDefinitionVersionRow.definition_id == identity.definition_id,
                AutomationDefinitionVersionRow.version_id == identity.version_id,
                AutomationDefinitionVersionRow.artifact_digest == identity.artifact_digest,
                AutomationDefinitionVersionRow.lifecycle == "published",
                AutomationDefinitionVersionRow.snapshot_id == snapshot_id,
            ))
            if version is None or definition.current_version_id != version.version_id:
                return None
            return DefinitionProof(
                tenant_id=tenant_id,
                project_id=project_id,
                automation_id=definition.automation_id,
                actor_id=definition.actor_id,
                identity=identity,
                task_name=version.task_name,
                command=version.command,
                events=tuple(version.events or ()),
                evidence_id=version.evidence_id,
                gate_record_id=version.gate_record_id,
                snapshot_id=version.snapshot_id,
                active=True,
            )

    async def verify_m04(self, *, scope: ScopeProof) -> GateEvidence | None:
        return await self._gate(scope=scope, kind="M04", record_id=scope.gate_record_id)

    async def verify_m04e(self, *, scope: ScopeProof, definition: DefinitionProof) -> GateEvidence | None:
        return await self._gate(scope=scope, kind="M04.e", record_id=definition.gate_record_id)

    async def _gate(self, *, scope: ScopeProof, kind: str, record_id: str) -> GateEvidence | None:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(AutomationGateEvidenceRow).where(
                AutomationGateEvidenceRow.tenant_id == scope.tenant_id,
                AutomationGateEvidenceRow.project_id == scope.project_id,
                AutomationGateEvidenceRow.kind == kind,
                AutomationGateEvidenceRow.record_id == record_id,
                AutomationGateEvidenceRow.verified.is_(True),
                AutomationGateEvidenceRow.revoked.is_(False),
            ))).all()
        if len(rows) != 1:
            return None
        row = rows[0]
        return GateEvidence(
            kind=row.kind, record_id=row.record_id, partition_digest=row.partition_digest,
            evidence_digest=row.evidence_digest, signature_ref=row.signature_ref,
            decision_owner=row.decision_owner, dependencies=tuple(row.dependencies or ()),
            verified=row.verified and not row.revoked,
        )

    async def resolve_trigger(self, *, trigger_id: str) -> TriggerResolution:
        async with self._session_factory() as session:
            legacy = await session.scalar(select(ProjectTriggerRow).where(ProjectTriggerRow.id == trigger_id))
            evidence = await session.get(AutomationProjectTriggerEvidenceRow, trigger_id)
            if legacy is None or evidence is None:
                return TriggerResolution(ResolutionStatus.MISSING)
            project = await session.scalar(select(ProjectRow).where(
                ProjectRow.id == legacy.project_id,
                ProjectRow.id == evidence.project_id,
                ProjectRow.company_id == evidence.tenant_id,
            ))
            if project is None or legacy.status not in {"queued", "claimed"}:
                return TriggerResolution(ResolutionStatus.BLOCKED)
            payload_digest = _digest(legacy.payload or {})
            if payload_digest != evidence.payload_digest:
                return TriggerResolution(ResolutionStatus.QUARANTINED)
            trigger = ProjectTrigger(
                trigger_id=legacy.id, project_id=legacy.project_id,
                schema_version=evidence.schema_version, event_type=evidence.event_type,
                payload=legacy.payload or {}, payload_digest=evidence.payload_digest,
                mapping_id=evidence.mapping_id, automation_id=evidence.automation_id,
                actor_id=evidence.actor_id,
                definition=DefinitionIdentity(evidence.definition_id, evidence.version_id, evidence.artifact_digest),
                source_evidence_id=evidence.source_evidence_id, eligible=evidence.eligible,
                eligibility_evidence_id=evidence.eligibility_evidence_id, state=legacy.status,
                legacy_fence_id=evidence.legacy_fence_id,
                legacy_fence_generation=evidence.legacy_fence_generation,
                target_claim_evidence_id=evidence.target_claim_evidence_id,
            )
            return TriggerResolution(ResolutionStatus.RESOLVED, trigger)

    async def verify(self, *, trigger: ProjectTrigger) -> bool:
        async with self._session_factory() as session:
            legacy = await session.scalar(select(ProjectTriggerRow).where(
                ProjectTriggerRow.id == trigger.trigger_id,
                ProjectTriggerRow.project_id == trigger.project_id,
                ProjectTriggerRow.status == trigger.state,
            ))
            evidence = await session.get(AutomationProjectTriggerEvidenceRow, trigger.trigger_id)
            project = await session.scalar(select(ProjectRow).where(ProjectRow.id == trigger.project_id))
        return bool(
            legacy is not None and evidence is not None and project is not None
            and project.company_id == evidence.tenant_id and trigger.eligible and evidence.eligible
            and trigger.payload_digest == evidence.payload_digest == _digest(legacy.payload or {})
            and trigger.legacy_fence_id == evidence.legacy_fence_id
            and trigger.legacy_fence_generation == evidence.legacy_fence_generation
            and trigger.source_evidence_id == evidence.source_evidence_id
            and trigger.target_claim_evidence_id == evidence.target_claim_evidence_id
            and evidence.legacy_fence_generation >= 1
        )

    async def _validate_claim(self, session: AsyncSession, claim: IngressClaim) -> bool:
        project = await session.scalar(select(ProjectRow).where(
            ProjectRow.id == claim.project_id,
            ProjectRow.company_id == claim.tenant_id,
        ).with_for_update())
        if project is None:
            return False
        scope = await session.scalar(select(AutomationProjectScopeEvidenceRow).where(
            AutomationProjectScopeEvidenceRow.scope_id == claim.scope_id,
            AutomationProjectScopeEvidenceRow.tenant_id == claim.tenant_id,
            AutomationProjectScopeEvidenceRow.project_id == claim.project_id,
            AutomationProjectScopeEvidenceRow.parent_proof_id == claim.parent_proof_id,
            AutomationProjectScopeEvidenceRow.snapshot_id == claim.snapshot_id,
            AutomationProjectScopeEvidenceRow.active.is_(True),
            AutomationProjectScopeEvidenceRow.parent_compatible.is_(True),
        ).with_for_update())
        if scope is None:
            return False
        gates = (await session.scalars(select(AutomationGateEvidenceRow).where(
            AutomationGateEvidenceRow.tenant_id == claim.tenant_id,
            AutomationGateEvidenceRow.project_id == claim.project_id,
            AutomationGateEvidenceRow.verified.is_(True),
            AutomationGateEvidenceRow.revoked.is_(False),
        ).with_for_update())).all()
        m04 = [row for row in gates if row.kind == "M04" and row.record_id == claim.m04_gate_id]
        m04e = [row for row in gates if row.kind == "M04.e" and row.record_id == claim.m04e_gate_id]
        if (len(m04) != 1 or len(m04e) != 1 or m04[0].evidence_digest != claim.m04_gate_digest
                or m04e[0].evidence_digest != claim.m04e_gate_digest):
            return False
        version = await session.scalar(select(AutomationDefinitionVersionRow).where(
            AutomationDefinitionVersionRow.tenant_id == claim.tenant_id,
            AutomationDefinitionVersionRow.project_id == claim.project_id,
            AutomationDefinitionVersionRow.automation_id == claim.automation_id,
            AutomationDefinitionVersionRow.definition_id == claim.definition.definition_id,
            AutomationDefinitionVersionRow.version_id == claim.definition.version_id,
            AutomationDefinitionVersionRow.artifact_digest == claim.definition.artifact_digest,
            AutomationDefinitionVersionRow.evidence_id == claim.definition_evidence_id,
            AutomationDefinitionVersionRow.snapshot_id == claim.snapshot_id,
            AutomationDefinitionVersionRow.lifecycle == "published",
        ).with_for_update())
        definition = await session.scalar(select(AutomationDefinitionRow).where(
            AutomationDefinitionRow.id == claim.definition.definition_id,
            AutomationDefinitionRow.tenant_id == claim.tenant_id,
            AutomationDefinitionRow.project_id == claim.project_id,
            AutomationDefinitionRow.automation_id == claim.automation_id,
            AutomationDefinitionRow.actor_id == claim.actor_id,
            AutomationDefinitionRow.lifecycle == "active",
        ).with_for_update())
        if version is None or definition is None or definition.current_version_id != version.version_id:
            return False
        if claim.source.kind is SourceKind.WEBHOOK:
            mapping = await session.scalar(select(AutomationIngressMappingRow).where(
                AutomationIngressMappingRow.mapping_id == claim.mapping_id,
                AutomationIngressMappingRow.source_id == claim.source.source_id,
                AutomationIngressMappingRow.subscription_id == (claim.subscription_id or ""),
                AutomationIngressMappingRow.tenant_id == claim.tenant_id,
                AutomationIngressMappingRow.project_id == claim.project_id,
                AutomationIngressMappingRow.automation_id == claim.automation_id,
                AutomationIngressMappingRow.endpoint_id == claim.endpoint_id,
                AutomationIngressMappingRow.actor_id == claim.actor_id,
                AutomationIngressMappingRow.definition_id == claim.definition.definition_id,
                AutomationIngressMappingRow.version_id == claim.definition.version_id,
                AutomationIngressMappingRow.artifact_digest == claim.definition.artifact_digest,
                AutomationIngressMappingRow.enabled.is_(True),
                AutomationIngressMappingRow.inbound_enabled.is_(True),
            ).with_for_update())
            if mapping is None:
                return False
        else:
            legacy = await session.scalar(select(ProjectTriggerRow).where(
                ProjectTriggerRow.id == claim.source.event_id,
                ProjectTriggerRow.project_id == claim.project_id,
                ProjectTriggerRow.status.in_(("queued", "claimed")),
            ).with_for_update())
            trigger = await session.get(AutomationProjectTriggerEvidenceRow, claim.source.event_id)
            if (legacy is None or trigger is None or trigger.tenant_id != claim.tenant_id
                    or trigger.payload_digest != claim.raw_payload_digest
                    or trigger.legacy_fence_id != claim.legacy_fence_id
                    or trigger.legacy_fence_generation != claim.legacy_fence_generation
                    or not trigger.eligible or trigger.source_evidence_id != claim.source_evidence_id):
                return False
        return True

    async def claim_or_resume(self, *, claim: IngressClaim) -> ClaimResult:
        if claim.source_claim_marker is not None:
            return ClaimResult(ClaimStatus.BLOCKED, blocker="trigger_claim_marker_requires_lookup_only_resume")
        async with self._session_factory() as session, session.begin():
            if not await self._validate_claim(session, claim):
                return ClaimResult(ClaimStatus.BLOCKED, blocker="durable_scope_definition_or_source_fence_unresolved")
            existing = await self._find_claim(session, claim)
            if existing is not None:
                if existing.request_digest != claim.request_digest:
                    evidence_id = await self._record_conflict(session, existing, claim)
                    return ClaimResult(ClaimStatus.CONFLICT, reason="idempotency_key_payload_conflict", conflict_evidence_id=evidence_id)
                return ClaimResult(ClaimStatus.REPLAY, link=await self._load_link(session, existing))
            task_run_id = _id("task")
            agent_run_id = _id("agr")
            event_id = "evt_" + hashlib.sha256((claim.idempotency_key + ":accepted").encode("ascii")).hexdigest()
            audit_id = _id("audit")
            now = _aware(await session.scalar(select(func.current_timestamp())))
            event_payload = {
                "schema": "automation.ingress.accepted.v1",
                "tenant_id": claim.tenant_id,
                "project_id": claim.project_id,
                "automation_id": claim.automation_id,
                "task_run_id": task_run_id,
                "source_event_id": claim.source.event_id,
                "request_digest": claim.request_digest,
            }
            outbox_digest = _digest(event_payload)
            aggregate_key = ":".join(
                f"{len(part)}={part}" for part in (claim.tenant_id, "task_run", task_run_id, "1")
            )
            receipt = AutomationIngressClaimRow(
                id=_id("claim"), tenant_id=claim.tenant_id, project_id=claim.project_id,
                source_kind=claim.source.kind.value, source_id=claim.source.source_id,
                source_event_id=claim.source.event_id, subscription_key=claim.subscription_id or "",
                subscription_id=claim.subscription_id, idempotency_key=claim.idempotency_key,
                request_digest=claim.request_digest,
                claim_json={"claim": json.loads(claim.provenance_json), "payload": json.loads(claim.payload_json)},
                provenance_digest=claim.provenance_digest, source_claim_marker=None,
                task_run_id=task_run_id, agent_run_id=agent_run_id, audit_id=audit_id,
            )
            agent_run = AgentRunRow(
                id=agent_run_id, project_id=claim.project_id, session_id=None,
                requested_by_employee_id=None, idempotency_key=claim.agent_run_key,
                status="queued", input_payload=json.loads(claim.payload_json), output_payload=None,
                failure_code=None, failure_message=None, attempt=1,
            )
            task = AutomationTaskIntentRow(
                task_run_id=task_run_id, tenant_id=claim.tenant_id, project_id=claim.project_id,
                automation_id=claim.automation_id, agent_run_id=agent_run_id,
                idempotency_key=claim.idempotency_key, request_digest=claim.request_digest,
                source_event_id=claim.source.event_id, input_payload=json.loads(claim.payload_json),
                state="queued", attempt=0, lease_generation=0, available_at=now,
            )
            ingress_event = AutomationIngressEventRow(
                event_id=event_id, tenant_id=claim.tenant_id, project_id=claim.project_id,
                run_id=agent_run_id, sequence=1, source_event_id=claim.source.event_id,
                event_type=claim.event_type, request_digest=claim.request_digest,
                correlation_id=claim.correlation_id, payload=event_payload, occurred_at=now,
            )
            outbox_event = AutomationOutboxEventRow(
                event_id=event_id, tenant_id=claim.tenant_id, project_id=claim.project_id,
                automation_id=claim.automation_id, aggregate_type="task_run", aggregate_id=task_run_id,
                aggregate_version=1, event_type="automation.task.accepted", source_run_id=agent_run_id,
                source_event_id=claim.source.event_id, occurred_at=now, payload=event_payload,
                payload_digest=outbox_digest, idempotency_key=aggregate_key,
            )
            audit = AutomationIngressAuditRow(
                id=audit_id, evidence_id=audit_id, tenant_id=claim.tenant_id, project_id=claim.project_id,
                actor_id=claim.actor_id, action="automation_ingress.accepted",
                identity_digest=claim.idempotency_key, request_digest=claim.request_digest,
                reason_code="accepted", evidence={"provenance_digest": claim.provenance_digest},
            )
            try:
                async with session.begin_nested():
                    session.add_all([
                        agent_run, task, receipt, ingress_event, outbox_event,
                        AutomationOutboxStateRow(
                            event_id=event_id, tenant_id=claim.tenant_id, project_id=claim.project_id,
                            automation_id=claim.automation_id, state="pending", available_at=now,
                        ),
                        AutomationOutboxEnqueueIntentRow(
                            event_id=event_id, queue_name="automation-ingress-events",
                            tenant_id=claim.tenant_id, deduplication_key=aggregate_key,
                            state="pending", available_at=now,
                        ),
                        audit,
                    ])
                    await session.flush()
            except IntegrityError:
                winner = await self._find_claim(session, claim)
                if winner is None:
                    raise
                if winner.idempotency_key == claim.idempotency_key and winner.request_digest == claim.request_digest:
                    return ClaimResult(ClaimStatus.REPLAY, link=await self._load_link(session, winner))
                evidence_id = await self._record_conflict(session, winner, claim)
                return ClaimResult(ClaimStatus.CONFLICT, reason="idempotency_key_payload_conflict", conflict_evidence_id=evidence_id)
            return ClaimResult(ClaimStatus.APPLIED, link=await self._load_link(session, receipt))

    async def resume_trigger(self, *, claim: IngressClaim, source_claim_marker: str) -> ClaimResult:
        async with self._session_factory() as session:
            row = await session.scalar(select(AutomationIngressClaimRow).where(
                AutomationIngressClaimRow.source_claim_marker == source_claim_marker,
                AutomationIngressClaimRow.tenant_id == claim.tenant_id,
                AutomationIngressClaimRow.project_id == claim.project_id,
                AutomationIngressClaimRow.source_kind == SourceKind.PROJECT_TRIGGER.value,
                AutomationIngressClaimRow.source_id == claim.source.source_id,
                AutomationIngressClaimRow.source_event_id == claim.source.event_id,
            ))
            if row is None:
                return ClaimResult(ClaimStatus.BLOCKED, blocker="durable_trigger_claim_marker_not_found")
            if row.idempotency_key != claim.idempotency_key or row.request_digest != claim.request_digest:
                return ClaimResult(ClaimStatus.BLOCKED, blocker="durable_trigger_claim_digest_mismatch")
            return ClaimResult(ClaimStatus.REPLAY, link=await self._load_link(session, row))

    async def quarantine(self, *, evidence: Quarantine) -> QuarantineReceipt:
        evidence_id = "quarantine:" + evidence.key[7:]
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(select(AutomationIngressAuditRow).where(
                AutomationIngressAuditRow.evidence_id == evidence_id,
            ).with_for_update())
            if existing is not None:
                if existing.identity_digest != evidence.key or existing.request_digest != evidence.payload_digest:
                    raise RuntimeError("quarantine identity digest conflict")
                return QuarantineReceipt(existing.id, evidence.key, evidence.digest, existing.evidence_id)
            row_id = _id("quarantine")
            session.add(AutomationIngressAuditRow(
                id=row_id, evidence_id=evidence_id, tenant_id=evidence.tenant_id,
                project_id=evidence.project_id, actor_id=None, action="automation_ingress.quarantine",
                identity_digest=evidence.key, request_digest=evidence.payload_digest,
                reason_code=evidence.reason[:128],
                evidence={"source_kind": evidence.source.kind.value, "source_id": evidence.source.source_id,
                          "source_event_id": evidence.source.event_id, "schema_version": evidence.source.schema_version,
                          "subscription_id": evidence.subscription_id, "mapping_id": evidence.mapping_id,
                          "parent_proof_id": evidence.parent_proof_id, "source_evidence_id": evidence.source_evidence_id,
                          "correlation_id": evidence.correlation_id, "digest": evidence.digest},
            ))
            await session.flush()
            return QuarantineReceipt(row_id, evidence.key, evidence.digest, evidence_id)

    async def _find_claim(self, session: AsyncSession, claim: IngressClaim) -> AutomationIngressClaimRow | None:
        return await session.scalar(select(AutomationIngressClaimRow).where(
            (AutomationIngressClaimRow.idempotency_key == claim.idempotency_key)
            | ((AutomationIngressClaimRow.tenant_id == claim.tenant_id)
               & (AutomationIngressClaimRow.project_id == claim.project_id)
               & (AutomationIngressClaimRow.source_kind == claim.source.kind.value)
               & (AutomationIngressClaimRow.source_id == claim.source.source_id)
               & (AutomationIngressClaimRow.source_event_id == claim.source.event_id)
               & (AutomationIngressClaimRow.subscription_key == (claim.subscription_id or "")))
        ).with_for_update())

    async def _record_conflict(
        self, session: AsyncSession, existing: AutomationIngressClaimRow, claim: IngressClaim,
    ) -> str:
        evidence_id = "conflict:" + _digest({"identity": claim.idempotency_key, "request": claim.request_digest})[7:]
        audit = await session.scalar(select(AutomationIngressAuditRow).where(
            AutomationIngressAuditRow.evidence_id == evidence_id,
        ).with_for_update())
        if audit is None:
            session.add(AutomationIngressAuditRow(
                id=_id("audit"), evidence_id=evidence_id, tenant_id=claim.tenant_id,
                project_id=claim.project_id, actor_id=claim.actor_id,
                action="automation_ingress.idempotency_conflict", identity_digest=claim.idempotency_key,
                request_digest=claim.request_digest, reason_code="idempotency_key_payload_conflict",
                evidence={"stored_request_digest": existing.request_digest,
                          "incoming_request_digest": claim.request_digest,
                          "stored_task_run_id": existing.task_run_id},
            ))
            await session.flush()
        return evidence_id

    async def _load_link(self, session: AsyncSession, receipt: AutomationIngressClaimRow) -> RunLink:
        task = await session.get(AutomationTaskIntentRow, receipt.task_run_id)
        if task is None:
            raise RuntimeError("durable ingress receipt lost its task intent")
        rows = (await session.scalars(select(AutomationIngressEventRow).where(
            AutomationIngressEventRow.run_id == receipt.agent_run_id,
            AutomationIngressEventRow.tenant_id == receipt.tenant_id,
            AutomationIngressEventRow.project_id == receipt.project_id,
        ).order_by(AutomationIngressEventRow.sequence))).all()
        if not rows:
            raise RuntimeError("durable ingress receipt lost its ordered event linkage")
        event_links = tuple(EventLink(
            event_id=row.event_id, sequence=row.sequence, run_id=row.run_id,
            tenant_id=row.tenant_id, project_id=row.project_id,
            source_event_id=row.source_event_id, correlation_id=row.correlation_id,
            request_digest=row.request_digest, event_type=row.event_type,
        ) for row in rows)
        provenance = receipt.claim_json.get("claim", {})
        return RunLink(
            task_run_id=receipt.task_run_id, agent_run_id=receipt.agent_run_id,
            tenant_id=receipt.tenant_id, project_id=receipt.project_id,
            session_id=None, agent_run_key="automation-ingress:" + receipt.idempotency_key[7:],
            request_digest=receipt.request_digest, provenance_digest=receipt.provenance_digest,
            correlation_id=event_links[0].correlation_id, transaction_id=receipt.id,
            audit_id=receipt.audit_id, audit_actor_id=provenance.get("actor_id") or "automation-ingress",
            claim_evidence_id=receipt.source_claim_marker or "ingress-claim:" + receipt.id,
            events=event_links, approvals=(), committed_at=_aware(rows[0].occurred_at),
        )


__all__ = ["SqlAlchemyAutomationIngressRepository"]
