"""SQLAlchemy MIG-AUTO-02 persistence; no worker or dispatch is wired."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.automation_outbox.durable import (
    AtomicOutboxCommand,
    AtomicOutboxReceipt,
    DeliveryClaim,
    DeliveryOutcome,
    DeliveryState,
    DurableAdapterEvidence,
    OutboxBlockedError,
    OutboxEnqueueIntent,
    OutboxIdempotencyConflict,
    OutboxState,
    OutboxStateRecord,
    OutboxClaim,
    OutboundDeliveryIntent,
    PartitionKey,
    ProjectionUpdate,
    TransactionalOutboxEvent,
    UpstreamRunLinkEvidence,
    WriterLease,
)
from prodavan.application.migration.automation_outbox.ownership import (
    LegacyOwnerMarker,
    LegacyQueueKind,
    PartitionOwnership,
)
from prodavan.infrastructure.persistence.automation_migration.models import (
    AutomationDeliveryIntentRow,
    AutomationDeliveryStateRow,
    AutomationIngressEventRow,
    AutomationOutboxEnqueueIntentRow,
    AutomationOutboxEventRow,
    AutomationOutboxStateRow,
    AutomationOwnershipEvidenceRow,
    AutomationPartitionOwnershipRow,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class SqlAlchemyAutomationOutboxRepository:
    """Concrete delivery/state CAS repository; atomic business mutation is blocked."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def commit_business_event_enqueue(
        self, *, command: AtomicOutboxCommand, adapter_evidence: DurableAdapterEvidence,
    ) -> AtomicOutboxReceipt:
        """Fail closed: event-only persistence cannot satisfy the business mutation UoW."""
        raise OutboxBlockedError(
            "atomic business/event/enqueue is blocked: no authoritative automation aggregate repository or expected-revision CAS is available"
        )

    async def repair_enqueue(
        self, *, partition: PartitionKey, event_id: str, source_run_id: str,
        source_event_id: str, deduplication_key: str, adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            event = await session.scalar(select(AutomationOutboxEventRow).where(
                AutomationOutboxEventRow.event_id == event_id,
                AutomationOutboxEventRow.tenant_id == partition.tenant_id,
                AutomationOutboxEventRow.project_id == partition.project_id,
                AutomationOutboxEventRow.automation_id == partition.automation_id,
                AutomationOutboxEventRow.source_run_id == source_run_id,
                AutomationOutboxEventRow.source_event_id == source_event_id,
                AutomationOutboxEventRow.idempotency_key == deduplication_key,
            ).with_for_update())
            if event is None:
                raise OutboxBlockedError("repair must reference an exact existing event/run/source identity")
            row = await session.scalar(select(AutomationOutboxEnqueueIntentRow).where(
                AutomationOutboxEnqueueIntentRow.event_id == event_id,
                AutomationOutboxEnqueueIntentRow.queue_name == "automation-events",
            ).with_for_update())
            if row is not None:
                if row.deduplication_key != deduplication_key:
                    raise OutboxIdempotencyConflict("enqueue repair conflicts with the stored deduplication key")
                return False
            session.add(AutomationOutboxEnqueueIntentRow(
                event_id=event_id, queue_name="automation-events", tenant_id=partition.tenant_id,
                deduplication_key=deduplication_key, state="pending", available_at=event.occurred_at,
            ))
            await session.flush()
            return True

    async def register_delivery_once(
        self, *, intent: OutboundDeliveryIntent, upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        if (intent.partition != upstream_evidence.partition
                or intent.task_run_id != upstream_evidence.task_run_id
                or intent.source_event_id not in upstream_evidence.ordered_event_ids
                or (upstream_evidence.approval_required and not upstream_evidence.approval_ids)):
            raise OutboxBlockedError("delivery does not match upstream task/run/event/approval evidence")
        async with self._session_factory() as session, session.begin():
            source = await session.scalar(select(AutomationIngressEventRow).where(
                AutomationIngressEventRow.event_id == intent.source_event_id,
                AutomationIngressEventRow.run_id == upstream_evidence.source_run_id,
                AutomationIngressEventRow.tenant_id == intent.partition.tenant_id,
                AutomationIngressEventRow.project_id == intent.partition.project_id,
            ).with_for_update())
            if source is None:
                raise OutboxBlockedError("delivery source event is not persisted in the requested partition")
            existing = await session.scalar(select(AutomationDeliveryIntentRow).where(
                AutomationDeliveryIntentRow.tenant_id == intent.partition.tenant_id,
                AutomationDeliveryIntentRow.project_id == intent.partition.project_id,
                AutomationDeliveryIntentRow.automation_id == intent.partition.automation_id,
                AutomationDeliveryIntentRow.subscription_id == intent.subscription_id,
                AutomationDeliveryIntentRow.source_event_id == intent.source_event_id,
            ).with_for_update())
            if existing is not None:
                if existing.request_digest != intent.request_digest:
                    raise OutboxIdempotencyConflict("delivery identity is already bound to a different request digest")
                return False
            try:
                async with session.begin_nested():
                    session.add_all([
                        AutomationDeliveryIntentRow(
                            delivery_id=intent.delivery_id, tenant_id=intent.partition.tenant_id,
                            project_id=intent.partition.project_id, automation_id=intent.partition.automation_id,
                            task_run_id=intent.task_run_id, source_run_id=upstream_evidence.source_run_id,
                            subscription_id=intent.subscription_id, source_event_id=intent.source_event_id,
                            destination_ref=intent.destination_ref, secret_ref=intent.secret_ref,
                            payload=dict(intent.payload), payload_digest=intent.payload_digest,
                            request_digest=intent.request_digest, idempotency_key=intent.idempotency_key,
                            state="pending",
                        ),
                        AutomationDeliveryStateRow(
                            delivery_id=intent.delivery_id, state="pending", available_at=source.occurred_at,
                        ),
                    ])
                    await session.flush()
            except IntegrityError as exc:
                winner = await session.scalar(select(AutomationDeliveryIntentRow).where(
                    AutomationDeliveryIntentRow.tenant_id == intent.partition.tenant_id,
                    AutomationDeliveryIntentRow.project_id == intent.partition.project_id,
                    AutomationDeliveryIntentRow.automation_id == intent.partition.automation_id,
                    AutomationDeliveryIntentRow.subscription_id == intent.subscription_id,
                    AutomationDeliveryIntentRow.source_event_id == intent.source_event_id,
                ))
                if winner is None:
                    raise
                if winner.request_digest != intent.request_digest:
                    raise OutboxIdempotencyConflict("concurrent delivery identity has a changed digest") from exc
                return False
            return True

    async def _active_owner(
        self, session: AsyncSession, partition: PartitionKey, lease: WriterLease, now: datetime,
    ) -> AutomationPartitionOwnershipRow | None:
        row = await session.scalar(select(AutomationPartitionOwnershipRow).where(
            AutomationPartitionOwnershipRow.tenant_id == partition.tenant_id,
            AutomationPartitionOwnershipRow.project_id == partition.project_id,
            AutomationPartitionOwnershipRow.automation_id == partition.automation_id,
        ).with_for_update())
        if (row is None or row.state != "target_active" or row.read_owner != "target"
                or row.write_owner != "target" or row.legacy_claims_enabled
                or not row.target_claims_enabled or row.epoch != lease.epoch
                or row.fencing_token != lease.fencing_token or row.lease_owner != lease.holder
                or row.lease_until is None or row.lease_until <= now):
            return None
        return row

    async def claim_due_deliveries(
        self, *, partition: PartitionKey, lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[DeliveryClaim, ...]:
        now = _now(now)
        lease_until = _now(lease_until)
        if (lease.partition != partition or not lease.valid_at(now) or not now < lease_until <= lease.expires_at or limit < 1):
            raise OutboxBlockedError("a bounded live same-partition lease is required")
        async with self._session_factory() as session, session.begin():
            if await self._active_owner(session, partition, lease, now) is None:
                raise OutboxBlockedError("target delivery worker is not the exclusive persisted partition owner")
            states = (await session.scalars(
                select(AutomationDeliveryStateRow)
                .join(AutomationDeliveryIntentRow, AutomationDeliveryIntentRow.delivery_id == AutomationDeliveryStateRow.delivery_id)
                .where(
                    AutomationDeliveryIntentRow.tenant_id == partition.tenant_id,
                    AutomationDeliveryIntentRow.project_id == partition.project_id,
                    AutomationDeliveryIntentRow.automation_id == partition.automation_id,
                    or_(
                        and_(AutomationDeliveryStateRow.state.in_(("pending", "retry_wait")), AutomationDeliveryStateRow.available_at <= now),
                        and_(AutomationDeliveryStateRow.state == "claimed", AutomationDeliveryStateRow.lease_until <= now),
                    ),
                ).order_by(AutomationDeliveryStateRow.available_at, AutomationDeliveryStateRow.delivery_id)
                .limit(limit).with_for_update(skip_locked=True)
            )).all()
            claims: list[DeliveryClaim] = []
            for row in states:
                intent_row = await session.get(AutomationDeliveryIntentRow, row.delivery_id)
                evidence = upstream_evidence.get(row.delivery_id) or upstream_evidence.get(intent_row.source_event_id if intent_row else "")
                if (intent_row is None or evidence is None or evidence.partition != partition
                        or evidence.task_run_id != intent_row.task_run_id
                        or evidence.source_run_id != intent_row.source_run_id
                        or intent_row.source_event_id not in evidence.ordered_event_ids
                        or (evidence.approval_required and not evidence.approval_ids)):
                    raise OutboxBlockedError("delivery has no exact upstream run/event/approval linkage")
                token = uuid.uuid4().hex + uuid.uuid4().hex
                generation, attempt = row.lease_generation + 1, row.attempts + 1
                result = await session.execute(update(AutomationDeliveryStateRow).where(
                    AutomationDeliveryStateRow.delivery_id == row.delivery_id,
                    AutomationDeliveryStateRow.revision == row.revision,
                    AutomationDeliveryStateRow.lease_generation == row.lease_generation,
                    or_(
                        and_(AutomationDeliveryStateRow.state.in_(("pending", "retry_wait")), AutomationDeliveryStateRow.available_at <= now),
                        and_(AutomationDeliveryStateRow.state == "claimed", AutomationDeliveryStateRow.lease_until <= now),
                    ),
                ).values(
                    state="claimed", attempts=attempt, lease_generation=generation,
                    lease_owner=lease.holder, claim_token=token, lease_until=lease_until,
                    revision=row.revision + 1,
                ))
                if result.rowcount != 1:
                    continue
                claims.append(DeliveryClaim(
                    intent=OutboundDeliveryIntent(
                        intent_row.delivery_id, partition, intent_row.task_run_id,
                        intent_row.subscription_id, intent_row.source_event_id,
                        intent_row.destination_ref, intent_row.secret_ref, intent_row.payload,
                        intent_row.idempotency_key, intent_row.payload_digest, intent_row.request_digest,
                    ),
                    attempt=attempt, lease=lease, lease_generation=generation,
                    claim_token=token, claimed_at=now, lease_until=lease_until,
                ))
            return tuple(claims)

    async def record_delivery_outcome(
        self, *, claim: DeliveryClaim, outcome: DeliveryOutcome, lease: WriterLease, now: datetime,
    ) -> bool:
        now = _now(now)
        if (lease != claim.lease or outcome.delivery_id != claim.intent.delivery_id
                or outcome.attempt != claim.attempt or outcome.lease_generation != claim.lease_generation
                or outcome.claim_token != claim.claim_token or outcome.completed_at > now
                or now >= claim.lease_until or not lease.valid_at(now)):
            return False
        if outcome.state not in (DeliveryState.RETRY_WAIT, DeliveryState.DELIVERED, DeliveryState.DEAD_LETTER):
            return False
        async with self._session_factory() as session, session.begin():
            if await self._active_owner(session, claim.intent.partition, lease, now) is None:
                return False
            available = outcome.retry_at if outcome.state is DeliveryState.RETRY_WAIT else now
            changed = await session.execute(update(AutomationDeliveryStateRow).where(
                AutomationDeliveryStateRow.delivery_id == claim.intent.delivery_id,
                AutomationDeliveryStateRow.state == "claimed",
                AutomationDeliveryStateRow.attempts == claim.attempt,
                AutomationDeliveryStateRow.lease_generation == claim.lease_generation,
                AutomationDeliveryStateRow.lease_owner == lease.holder,
                AutomationDeliveryStateRow.claim_token == claim.claim_token,
                AutomationDeliveryStateRow.lease_until > now,
            ).values(
                state=outcome.state.value, available_at=available,
                retry_at=outcome.retry_at, lease_owner=None, claim_token=None, lease_until=None,
                delivered_at=outcome.completed_at if outcome.state is DeliveryState.DELIVERED else None,
                dead_lettered_at=outcome.completed_at if outcome.state is DeliveryState.DEAD_LETTER else None,
                receipt_id=outcome.receipt_id, error_code=outcome.error_code,
                revision=AutomationDeliveryStateRow.revision + 1,
            ))
            if changed.rowcount != 1:
                return False
            await session.execute(update(AutomationDeliveryIntentRow).where(
                AutomationDeliveryIntentRow.delivery_id == claim.intent.delivery_id,
            ).values(state=outcome.state.value))
            return True

    async def claim_due_outbox(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[OutboxClaim, ...]:
        now = _now(now)
        lease_until = _now(lease_until)
        if (writer_lease.partition != partition or not writer_lease.valid_at(now)
                or not now < lease_until <= writer_lease.expires_at or limit < 1):
            raise OutboxBlockedError("a bounded live same-partition lease is required")
        async with self._session_factory() as session, session.begin():
            if await self._active_owner(session, partition, writer_lease, now) is None:
                raise OutboxBlockedError("target outbox worker is not the exclusive persisted partition owner")
            states = (await session.scalars(select(AutomationOutboxStateRow).where(
                AutomationOutboxStateRow.tenant_id == partition.tenant_id,
                AutomationOutboxStateRow.project_id == partition.project_id,
                AutomationOutboxStateRow.automation_id == partition.automation_id,
                or_(
                    and_(AutomationOutboxStateRow.state.in_(("pending", "retry_wait")), AutomationOutboxStateRow.available_at <= now),
                    and_(AutomationOutboxStateRow.state == "claimed", AutomationOutboxStateRow.lease_until <= now),
                ),
            ).order_by(AutomationOutboxStateRow.available_at, AutomationOutboxStateRow.event_id)
                .limit(limit).with_for_update(skip_locked=True))).all()
            claims: list[OutboxClaim] = []
            for row in states:
                event = await session.get(AutomationOutboxEventRow, row.event_id)
                evidence = upstream_evidence.get(event.source_event_id) if event is not None else None
                if (event is None or evidence is None or evidence.partition != partition
                        or evidence.source_run_id != event.source_run_id
                        or event.source_event_id not in evidence.ordered_event_ids
                        or (evidence.approval_required and not evidence.approval_ids)):
                    raise OutboxBlockedError("outbox event has no exact upstream run/event/approval linkage")
                token = uuid.uuid4().hex + uuid.uuid4().hex
                generation, attempt = row.lease_generation + 1, row.attempts + 1
                changed = await session.execute(update(AutomationOutboxStateRow).where(
                    AutomationOutboxStateRow.event_id == row.event_id,
                    AutomationOutboxStateRow.revision == row.revision,
                    AutomationOutboxStateRow.lease_generation == row.lease_generation,
                    or_(
                        and_(AutomationOutboxStateRow.state.in_(("pending", "retry_wait")), AutomationOutboxStateRow.available_at <= now),
                        and_(AutomationOutboxStateRow.state == "claimed", AutomationOutboxStateRow.lease_until <= now),
                    ),
                ).values(
                    state="claimed", attempts=attempt, lease_generation=generation,
                    lease_owner=writer_lease.holder, lease_token=token,
                    lease_until=lease_until, revision=row.revision + 1,
                ))
                if changed.rowcount != 1:
                    continue
                row = await session.get(AutomationOutboxStateRow, row.event_id)
                state = OutboxStateRecord(
                    event_id=row.event_id, partition=partition, state=OutboxState.CLAIMED,
                    available_at=row.available_at, attempts=row.attempts,
                    lease_generation=row.lease_generation, lease_owner=row.lease_owner,
                    lease_token=row.lease_token, lease_until=row.lease_until,
                    last_error_code=row.last_error_code, delivered_at=row.delivered_at,
                    dead_lettered_at=row.dead_lettered_at, receipt_id=row.receipt_id,
                    revision=row.revision,
                )
                claims.append(OutboxClaim(
                    event=TransactionalOutboxEvent(
                        event_id=event.event_id, partition=partition,
                        aggregate_type=event.aggregate_type, aggregate_id=event.aggregate_id,
                        aggregate_version=event.aggregate_version, event_type=event.event_type,
                        source_event_id=event.source_event_id, source_run_id=event.source_run_id,
                        occurred_at=event.occurred_at, payload=event.payload,
                        idempotency_key=event.idempotency_key, payload_digest=event.payload_digest,
                    ), state=state, writer_lease=writer_lease, claimed_at=now,
                ))
            return tuple(claims)

    async def compare_and_set_outbox_state(
        self, *, claim: OutboxClaim, next_state: OutboxStateRecord,
        expected_revision: int, writer_lease: WriterLease, now: datetime,
    ) -> bool:
        now = _now(now)
        state = claim.state
        if (claim.writer_lease != writer_lease or state.revision != expected_revision
                or state.state is not OutboxState.CLAIMED or state.lease_until is None
                or state.lease_until <= now or not writer_lease.valid_at(now)
                or next_state.event_id != claim.event.event_id or next_state.partition != claim.event.partition
                or next_state.attempts != state.attempts or next_state.lease_generation != state.lease_generation
                or next_state.revision != expected_revision + 1
                or next_state.state not in (OutboxState.RETRY_WAIT, OutboxState.DELIVERED, OutboxState.DEAD_LETTER)):
            return False
        async with self._session_factory() as session, session.begin():
            if await self._active_owner(session, state.partition, writer_lease, now) is None:
                return False
            changed = await session.execute(update(AutomationOutboxStateRow).where(
                AutomationOutboxStateRow.event_id == state.event_id,
                AutomationOutboxStateRow.tenant_id == state.partition.tenant_id,
                AutomationOutboxStateRow.project_id == state.partition.project_id,
                AutomationOutboxStateRow.automation_id == state.partition.automation_id,
                AutomationOutboxStateRow.state == "claimed",
                AutomationOutboxStateRow.revision == expected_revision,
                AutomationOutboxStateRow.attempts == state.attempts,
                AutomationOutboxStateRow.lease_generation == state.lease_generation,
                AutomationOutboxStateRow.lease_owner == writer_lease.holder,
                AutomationOutboxStateRow.lease_token == state.lease_token,
                AutomationOutboxStateRow.lease_until > now,
            ).values(
                state=next_state.state.value, available_at=next_state.available_at,
                lease_owner=None, lease_token=None, lease_until=None,
                last_error_code=next_state.last_error_code, delivered_at=next_state.delivered_at,
                dead_lettered_at=next_state.dead_lettered_at, receipt_id=next_state.receipt_id,
                revision=next_state.revision,
            ))
            return changed.rowcount == 1

    async def load_ownership(self, partition: PartitionKey) -> PartitionOwnership | None:
        async with self._session_factory() as session:
            row = await session.get(
                AutomationPartitionOwnershipRow,
                (partition.tenant_id, partition.project_id, partition.automation_id),
            )
        if row is None:
            return None
        marker_data = row.legacy_marker
        marker = LegacyOwnerMarker(
            partition=partition, queue_kind=LegacyQueueKind(marker_data["queue_kind"]),
            source_partition_id=marker_data["source_partition_id"],
            source_schema_version=marker_data["source_schema_version"],
            source_snapshot_digest=marker_data["source_snapshot_digest"],
            lease_inventory_digest=marker_data["lease_inventory_digest"],
            checkpoint_id=marker_data["checkpoint_id"], owner_epoch=marker_data["owner_epoch"],
            active_leases=marker_data["active_leases"], claims_stopped=marker_data["claims_stopped"],
        )
        if marker.digest != row.legacy_marker_digest:
            raise OutboxBlockedError("legacy owner marker digest does not match persisted evidence")
        return PartitionOwnership(
            partition=partition, state=row.state, legacy_marker=marker,
            read_owner=row.read_owner, write_owner=row.write_owner,
            legacy_claims_enabled=row.legacy_claims_enabled,
            target_claims_enabled=row.target_claims_enabled,
            projection_read_only=row.projection_read_only, epoch=row.epoch,
            fencing_token=row.fencing_token, revision=row.revision,
            evidence_id=row.evidence_id, shadow_reads_enabled=row.shadow_reads_enabled,
        )

    async def bind_legacy_marker(self, marker: LegacyOwnerMarker) -> bool:
        partition = marker.partition
        body = {
            "queue_kind": marker.queue_kind.value,
            "source_partition_id": marker.source_partition_id,
            "source_schema_version": marker.source_schema_version,
            "source_snapshot_digest": marker.source_snapshot_digest,
            "lease_inventory_digest": marker.lease_inventory_digest,
            "checkpoint_id": marker.checkpoint_id,
            "owner_epoch": marker.owner_epoch,
            "active_leases": marker.active_leases,
            "claims_stopped": marker.claims_stopped,
        }
        async with self._session_factory() as session, session.begin():
            old = await session.get(
                AutomationPartitionOwnershipRow,
                (partition.tenant_id, partition.project_id, partition.automation_id),
            )
            if old is not None:
                return old.legacy_marker_digest == marker.digest
            session.add(AutomationPartitionOwnershipRow(
                tenant_id=partition.tenant_id, project_id=partition.project_id,
                automation_id=partition.automation_id, state="legacy_active",
                read_owner="legacy", write_owner="legacy",
                legacy_claims_enabled=True, target_claims_enabled=False,
                projection_read_only=False, shadow_reads_enabled=False,
                epoch=0, fencing_token=None, lease_owner=None, lease_token=None, lease_until=None,
                revision=0, legacy_marker=body, legacy_marker_digest=marker.digest,
                active_legacy_leases=marker.active_leases, active_target_leases=0,
            ))
            await session.flush()
            return True

    async def compare_and_set_ownership(
        self, *, expected_revision: int, ownership: PartitionOwnership,
        evidence: Any, actor_evidence: Any, adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        partition = ownership.partition
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(AutomationPartitionOwnershipRow).where(
                AutomationPartitionOwnershipRow.tenant_id == partition.tenant_id,
                AutomationPartitionOwnershipRow.project_id == partition.project_id,
                AutomationPartitionOwnershipRow.automation_id == partition.automation_id,
            ).with_for_update())
            if (row is None or row.revision != expected_revision
                    or row.legacy_marker_digest != evidence.legacy_marker_digest
                    or ownership.revision != expected_revision + 1
                    or ownership.epoch != evidence.lease_fencing_epoch
                    or ownership.epoch <= max(row.epoch, row.legacy_marker.get("owner_epoch", 0))):
                return False
            if (evidence.partition != partition or evidence.source_snapshot_digest != row.legacy_marker.get("source_snapshot_digest")
                    or evidence.active_legacy_leases != 0 or evidence.active_target_leases != 0):
                return False
            previous = row.state
            changed = await session.execute(update(AutomationPartitionOwnershipRow).where(
                AutomationPartitionOwnershipRow.tenant_id == partition.tenant_id,
                AutomationPartitionOwnershipRow.project_id == partition.project_id,
                AutomationPartitionOwnershipRow.automation_id == partition.automation_id,
                AutomationPartitionOwnershipRow.revision == expected_revision,
                AutomationPartitionOwnershipRow.epoch == row.epoch,
                AutomationPartitionOwnershipRow.legacy_marker_digest == evidence.legacy_marker_digest,
            ).values(
                state=ownership.state.value, read_owner=ownership.read_owner,
                write_owner=ownership.write_owner,
                legacy_claims_enabled=ownership.legacy_claims_enabled,
                target_claims_enabled=ownership.target_claims_enabled,
                projection_read_only=ownership.projection_read_only,
                shadow_reads_enabled=ownership.shadow_reads_enabled,
                epoch=ownership.epoch, fencing_token=ownership.fencing_token,
                lease_owner=None, lease_token=None, lease_until=None,
                revision=ownership.revision, evidence_id=evidence.evidence_id,
                active_legacy_leases=0, active_target_leases=0, updated_at=func.current_timestamp(),
            ))
            if changed.rowcount != 1:
                return False
            session.add(AutomationOwnershipEvidenceRow(
                transition_id=evidence.transition_id, tenant_id=partition.tenant_id,
                project_id=partition.project_id, automation_id=partition.automation_id,
                epoch=ownership.epoch, revision=ownership.revision,
                from_state=previous, to_state=ownership.state.value,
                evidence_id=evidence.evidence_id, evidence_digest=evidence.evidence_digest,
                actor_id=actor_evidence.actor_id, correlation_id=evidence.correlation_id,
                evidence={"source_snapshot_digest": evidence.source_snapshot_digest,
                          "parity_digest": evidence.parity_digest,
                          "legacy_marker_digest": evidence.legacy_marker_digest,
                          "adapter_evidence_id": adapter_evidence.evidence_id,
                          "actor_evidence_id": actor_evidence.evidence_id},
            ))
            await session.flush()
            return True

    async def apply_projection(
        self, *, update: ProjectionUpdate, expected_revision: int,
        legacy_marker: Any, adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        raise OutboxBlockedError("legacy projection mutation is outside this migration persistence zone")


__all__ = ["SqlAlchemyAutomationOutboxRepository"]
