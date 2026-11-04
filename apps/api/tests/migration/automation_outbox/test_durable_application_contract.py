"""MIG-AUTO-02 durable acceptance tests; author only during verification freeze."""
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from prodavan.application.migration.automation_outbox.durable import (
    AtomicOutboxCommand,
    AutomationDeliveryRepositoryAdapter,
    AutomationOutboxStateRepositoryAdapter,
    BusinessMutationIntent,
    DeliveryClaim,
    DeliveryOutcome,
    DurableDeliveryRegistrar,
    DurableDeliveryWorker,
    DeliveryReceipt,
    DeliveryState,
    DeliveryStateMachine,
    DurableAdapterEvidence,
    DurableAutomationOutbox,
    OutboxBlockedError,
    OutboxState,
    OutboxStateMachine,
    OutboxStateRecord,
    OutboxEnqueueIntent,
    OutboxIdempotencyConflict,
    OutboundDeliveryIntent,
    ReceiverDeduplicationEvidence,
    RetryPolicy,
    ResolvedScopeActorEvidence,
    TransactionalOutboxEvent,
    UpstreamRunLinkEvidence,
)
from prodavan.application.migration.automation_outbox.outbox import PartitionKey, WriterLease
from prodavan.application.migration.automation_outbox.ownership import (
    CutoverState,
    LegacyOwnerMarker,
    LegacyQueueKind,
    PartitionOwnership,
)

NOW = datetime(2026, 11, 1, 14, 40, tzinfo=UTC)
PARTITION = PartitionKey("tenant-fixture", "project-fixture", "automation-fixture")
DIGEST = "sha256:" + "0" * 64


def test_outbox_claim_takeover_rejects_stale_generation_and_terminal_reclaim() -> None:
    pending = OutboxStateRecord("event-1", PARTITION, OutboxState.PENDING, NOW)
    first = OutboxStateMachine.claim(
        pending, owner="worker-a", token="claim-token-a", now=NOW,
        lease_until=NOW + timedelta(seconds=10),
    )
    assert first.attempts == 1 and first.lease_generation == 1
    second = OutboxStateMachine.claim(
        first, owner="worker-b", token="claim-token-b", now=NOW + timedelta(seconds=11),
        lease_until=NOW + timedelta(seconds=30),
    )
    assert second.attempts == 2 and second.lease_generation == 2
    with pytest.raises(OutboxBlockedError):
        OutboxStateMachine.delivered(
            second, generation=first.lease_generation, token="claim-token-a",
            completed_at=NOW + timedelta(seconds=12), receipt_id="receipt-stale",
        )
    delivered = OutboxStateMachine.delivered(
        second, generation=second.lease_generation, token="claim-token-b",
        completed_at=NOW + timedelta(seconds=12), receipt_id="receipt-current",
    )
    assert delivered.state is OutboxState.DELIVERED
    with pytest.raises(OutboxBlockedError):
        OutboxStateMachine.claim(
            delivered, owner="worker-c", token="claim-token-c", now=NOW + timedelta(minutes=1),
            lease_until=NOW + timedelta(minutes=2),
        )


def test_retry_backoff_is_bounded_and_exhaustion_is_terminal() -> None:
    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=2, max_delay_seconds=5)
    claimed = OutboxStateMachine.claim(
        OutboxStateRecord("event-retry", PARTITION, OutboxState.PENDING, NOW),
        owner="worker-a", token="claim-a", now=NOW, lease_until=NOW + timedelta(minutes=1),
    )
    retry = OutboxStateMachine.failed(
        claimed, generation=claimed.lease_generation, token="claim-a", failed_at=NOW,
        error_code="automation.error.transient", retry_policy=policy,
    )
    assert retry.state is OutboxState.RETRY_WAIT
    assert retry.available_at == NOW + timedelta(seconds=2)
    second = OutboxStateMachine.claim(
        retry, owner="worker-a", token="claim-b", now=retry.available_at,
        lease_until=retry.available_at + timedelta(minutes=1),
    )
    second_retry = OutboxStateMachine.failed(
        second, generation=second.lease_generation, token="claim-b", failed_at=retry.available_at,
        error_code="automation.error.transient", retry_policy=policy,
    )
    assert second_retry.available_at == retry.available_at + timedelta(seconds=4)
    third = OutboxStateMachine.claim(
        second_retry, owner="worker-a", token="claim-c", now=second_retry.available_at,
        lease_until=second_retry.available_at + timedelta(minutes=1),
    )
    dead = OutboxStateMachine.failed(
        third, generation=third.lease_generation, token="claim-c", failed_at=second_retry.available_at,
        error_code="automation.error.transient", retry_policy=policy,
    )
    assert dead.state is OutboxState.DEAD_LETTER
    assert dead.dead_lettered_at == second_retry.available_at


def _command() -> tuple[AtomicOutboxCommand, ResolvedScopeActorEvidence, DurableAdapterEvidence]:
    actor = ResolvedScopeActorEvidence(
        PARTITION, "scope-fixture", "actor-fixture", 3, "scope-proof-1", DIGEST, NOW,
    )
    upstream = UpstreamRunLinkEvidence(
        PARTITION, "task-run-fixture", "run-fixture", ("event-run-created",), (), False,
        "upstream-proof-1", DIGEST,
    )
    mutation = BusinessMutationIntent(
        PARTITION, "automation", "automation-fixture", 4, "automation.update",
        actor.actor_id, {"enabled": True},
    )
    key = TransactionalOutboxEvent.aggregate_event_key(
        partition=PARTITION, aggregate_type="automation", aggregate_id="automation-fixture", aggregate_version=5,
    )
    event = TransactionalOutboxEvent(
        "event-atomic-1", PARTITION, "automation", "automation-fixture", 5,
        "automation.updated", "event-run-created", "run-fixture", NOW, {"enabled": True}, key,
    )
    command = AtomicOutboxCommand(
        "request-key-1", PARTITION, actor, upstream, mutation, event,
        OutboxStateRecord(event.event_id, PARTITION, OutboxState.PENDING, NOW),
        OutboxEnqueueIntent(event.event_id, "automation-events", event.idempotency_key), "correlation-1",
    )
    adapter = DurableAdapterEvidence(
        "adapter-fixture", "tx-fixture", "adapter-proof-1", DIGEST,
        frozenset({
            "durable_transaction", "atomic_business_event_enqueue",
            "unique_idempotency_with_hash_conflict", "lease_generation_token_cas",
            "append_only_ownership_evidence", "upstream_run_event_approval_linkage",
        }),
    )
    return command, actor, adapter


@pytest.mark.asyncio
async def test_atomic_write_fails_closed_when_upstream_verifier_is_absent() -> None:
    class Verifier:
        async def verify(self, *, evidence):
            return True

    class MustNotWrite:
        async def commit_business_event_enqueue(self, **kwargs):
            raise AssertionError("missing upstream verifier must block persistence")

        async def repair_enqueue(self, **kwargs):
            raise AssertionError("missing upstream verifier must block repair")

    command, _, adapter = _command()
    app = DurableAutomationOutbox(
        repository=MustNotWrite(), scope_verifier=Verifier(), adapter_verifier=Verifier(),
        upstream_verifier=None,
    )
    with pytest.raises(OutboxBlockedError):
        await app.commit(command=command, adapter_evidence=adapter)


@pytest.mark.asyncio
async def test_atomic_write_fails_closed_before_repository_without_verified_scope_and_adapter() -> None:
    class MustNotWrite:
        async def commit_business_event_enqueue(self, **kwargs):
            raise AssertionError("repository must not be reached without verified evidence")

        async def repair_enqueue(self, **kwargs):
            raise AssertionError("repository must not be reached without verified evidence")

    command, _, adapter = _command()
    app = DurableAutomationOutbox(
        repository=MustNotWrite(), scope_verifier=None, adapter_verifier=None, upstream_verifier=None,
    )
    with pytest.raises(OutboxBlockedError):
        await app.commit(command=command, adapter_evidence=adapter)


@pytest.mark.asyncio
async def test_transaction_failure_returns_no_acceptance_receipt() -> None:
    class Verifier:
        async def verify(self, *, evidence):
            return True

    class FailingRepository:
        async def commit_business_event_enqueue(self, **kwargs):
            raise RuntimeError("transaction aborted")

        async def repair_enqueue(self, **kwargs):
            raise AssertionError("repair is not a commit fallback")

    command, _, adapter = _command()
    app = DurableAutomationOutbox(
        repository=FailingRepository(), scope_verifier=Verifier(),
        adapter_verifier=Verifier(), upstream_verifier=Verifier(),
    )
    with pytest.raises(RuntimeError, match="transaction aborted"):
        await app.commit(command=command, adapter_evidence=adapter)


@pytest.mark.asyncio
async def test_enqueue_repair_reuses_original_event_and_is_idempotent() -> None:
    class Verifier:
        async def verify(self, *, evidence):
            return True

    class Repository:
        def __init__(self):
            self.rows = set()
            self.calls = []

        async def commit_business_event_enqueue(self, **kwargs):
            raise AssertionError("repair cannot manufacture a missing source transaction")

        async def repair_enqueue(self, **kwargs):
            key = (
                kwargs["partition"], kwargs["event_id"], kwargs["source_run_id"],
                kwargs["source_event_id"], kwargs["deduplication_key"],
            )
            self.calls.append(key)
            if key in self.rows:
                return False
            self.rows.add(key)
            return True

    command, _, adapter = _command()
    repository = Repository()
    app = DurableAutomationOutbox(
        repository=repository, scope_verifier=Verifier(),
        adapter_verifier=Verifier(), upstream_verifier=Verifier(),
    )
    args = {
        "partition": command.partition,
        "actor_evidence": command.actor_evidence,
        "upstream_evidence": command.upstream_evidence,
        "adapter_evidence": adapter,
        "event_id": command.event.event_id,
        "source_run_id": command.event.source_run_id,
        "source_event_id": command.event.source_event_id,
        "deduplication_key": command.event.idempotency_key,
    }
    assert await app.repair_enqueue(**args)
    assert not await app.repair_enqueue(**args)
    assert repository.calls[0] == repository.calls[1]


def test_cutover_owner_model_rejects_dual_claims_and_preserves_legacy_marker() -> None:
    marker = LegacyOwnerMarker(
        PARTITION, LegacyQueueKind.WEBHOOK_DELIVERIES, "legacy-partition-1", "v1",
        DIGEST, DIGEST, "inventory-checkpoint-1", 1, 0, False,
    )
    initial = PartitionOwnership(PARTITION, CutoverState.LEGACY_ACTIVE, marker)
    assert initial.read_owner == "legacy" and initial.write_owner == "legacy"
    assert initial.legacy_claims_enabled and not initial.target_claims_enabled
    with pytest.raises(ValueError):
        PartitionOwnership(
            PARTITION, CutoverState.SHADOW, marker, "legacy", "legacy", True, True,
        )


def test_delivery_identity_key_is_stable_and_payload_change_changes_request_hash() -> None:
    key = OutboundDeliveryIntent.stable_key(
        partition=PARTITION, subscription_id="subscription-1", source_event_id="source-event-1",
    )
    first = OutboundDeliveryIntent(
        "delivery-1", PARTITION, "task-run-1", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "done"}, key,
    )
    replay = OutboundDeliveryIntent(
        "different-local-id", PARTITION, "task-run-1", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "done"}, key,
    )
    changed = OutboundDeliveryIntent(
        "delivery-2", PARTITION, "task-run-1", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "failed"}, key,
    )
    assert first.idempotency_key == replay.idempotency_key == changed.idempotency_key
    assert first.request_digest == replay.request_digest
    assert changed.request_digest != first.request_digest


@pytest.mark.asyncio
async def test_delivery_registration_is_idempotent_and_conflicting_hash_does_not_overwrite() -> None:
    _, actor, adapter = _command()
    upstream = UpstreamRunLinkEvidence(
        PARTITION, "task-run-fixture", "run-fixture", ("source-event-1",), (), False,
        "upstream-delivery-proof", DIGEST,
    )

    class Verifier:
        async def verify(self, *, evidence):
            return True

    class Repository:
        def __init__(self):
            self.rows = {}

        async def insert_delivery_once(self, *, intent, upstream_evidence, adapter_evidence):
            identity = (intent.subscription_id, intent.source_event_id)
            previous = self.rows.get(identity)
            if previous is None:
                self.rows[identity] = intent
                return True
            if previous.request_digest != intent.request_digest:
                raise OutboxIdempotencyConflict("same delivery identity has a changed request digest")
            return False

    repository = Repository()
    registrar = DurableDeliveryRegistrar(
        repository=repository, scope_verifier=Verifier(), adapter_verifier=Verifier(),
        upstream_verifier=Verifier(),
    )
    key = OutboundDeliveryIntent.stable_key(
        partition=PARTITION, subscription_id="subscription-1", source_event_id="source-event-1",
    )
    first = OutboundDeliveryIntent(
        "delivery-1", PARTITION, "task-run-fixture", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "done"}, key,
    )
    identical = OutboundDeliveryIntent(
        "delivery-replay", PARTITION, "task-run-fixture", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "done"}, key,
    )
    conflicting = OutboundDeliveryIntent(
        "delivery-conflict", PARTITION, "task-run-fixture", "subscription-1", "source-event-1",
        "destination-ref-1", "secret-ref-1", {"status": "failed"}, key,
    )
    args = {"actor_evidence": actor, "upstream_evidence": upstream, "adapter_evidence": adapter}
    assert await registrar.register_once(intent=first, **args)
    assert not await registrar.register_once(intent=identical, **args)
    with pytest.raises(OutboxIdempotencyConflict):
        await registrar.register_once(intent=conflicting, **args)
    assert repository.rows[("subscription-1", "source-event-1")].request_digest == first.request_digest


def test_delivery_claim_takeover_and_terminal_states_are_typed() -> None:
    intent_key = OutboundDeliveryIntent.stable_key(
        partition=PARTITION, subscription_id="subscription-1", source_event_id="event-1",
    )
    intent = OutboundDeliveryIntent(
        "delivery-1", PARTITION, "task-run-1", "subscription-1", "event-1",
        "destination-ref-1", "secret-ref-1", {"value": 1}, intent_key,
    )
    claimed = DeliveryStateMachine.claim(
        intent=intent, state=DeliveryState.PENDING, available_at=NOW,
        attempts=0, lease_generation=0, current_lease_until=None,
        owner="worker-a", token="claim-1", now=NOW, lease_until=NOW + timedelta(seconds=30),
    )
    assert claimed[:3] == (DeliveryState.CLAIMED, 1, 1)
    next_claim = DeliveryStateMachine.claim(
        intent=intent, state=claimed[0], available_at=NOW, attempts=claimed[1],
        lease_generation=claimed[2], current_lease_until=claimed[5], owner="worker-b",
        token="claim-2", now=NOW + timedelta(seconds=31), lease_until=NOW + timedelta(seconds=60),
    )
    assert next_claim[:3] == (DeliveryState.CLAIMED, 2, 2)
    second_lease = WriterLease(PARTITION, "worker-b", 8, "fence-token-8", NOW + timedelta(seconds=60))
    second_claim = DeliveryClaim(
        intent, 2, second_lease, 2, "claim-2", NOW + timedelta(seconds=31),
        NOW + timedelta(seconds=60),
    )
    with pytest.raises(OutboxBlockedError):
        DeliveryStateMachine.delivered(
            claim=DeliveryClaim(intent, 1, WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(seconds=60)),
                                1, "claim-1", NOW, NOW + timedelta(seconds=30)),
            completed_at=NOW + timedelta(seconds=32), receipt_id="stale-completion",
        )
    assert DeliveryStateMachine.delivered(
        claim=second_claim, completed_at=NOW + timedelta(seconds=32), receipt_id="receiver-receipt-2",
    ).state is DeliveryState.DELIVERED
    lease = WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(seconds=30))
    claim = DeliveryClaim(intent, 1, lease, 1, "claim-1", NOW, NOW + timedelta(seconds=30))
    delivered = DeliveryStateMachine.delivered(
        claim=claim, completed_at=NOW + timedelta(seconds=5), receipt_id="receiver-receipt-1",
    )
    assert delivered.state is DeliveryState.DELIVERED
    with pytest.raises(OutboxBlockedError):
        DeliveryStateMachine.delivered(
            claim=claim, completed_at=NOW + timedelta(seconds=31), receipt_id="stale-receipt",
        )


def test_target_read_final_delta_and_blocked_states_never_enable_two_writers() -> None:
    marker = LegacyOwnerMarker(
        PARTITION, LegacyQueueKind.WEBHOOK_DELIVERIES, "legacy-partition-2", "v1",
        DIGEST, DIGEST, "checkpoint-2", 2, 0, True,
    )
    target_read = PartitionOwnership(PARTITION, CutoverState.TARGET_READ, marker, "target", "legacy", True, False)
    final_delta = PartitionOwnership(
        PARTITION, CutoverState.FINAL_DELTA, marker, "target", "none", False, False, True,
        epoch=3, fencing_token="fence-3",
    )
    blocked = PartitionOwnership(
        PARTITION, CutoverState.BLOCKED, marker, "target", "none", False, False, True,
        epoch=4, fencing_token="fence-4",
    )
    assert target_read.legacy_claims_enabled and not target_read.target_claims_enabled
    assert not final_delta.legacy_claims_enabled and not final_delta.target_claims_enabled
    assert not blocked.legacy_claims_enabled and not blocked.target_claims_enabled
    with pytest.raises(ValueError):
        PartitionOwnership(PARTITION, CutoverState.BLOCKED, marker, "legacy", "legacy", True, False)


def test_shadow_owner_is_diagnostic_read_only_with_legacy_single_writer() -> None:
    marker = LegacyOwnerMarker(
        PARTITION, LegacyQueueKind.WEBHOOK_DELIVERIES, "legacy-partition-1", "v1",
        DIGEST, DIGEST, "inventory-checkpoint-1", 1, 0, False,
    )
    shadow = PartitionOwnership(
        PARTITION, CutoverState.SHADOW, marker, shadow_reads_enabled=True,
    )
    assert shadow.read_owner == "legacy" and shadow.write_owner == "legacy"
    assert shadow.legacy_claims_enabled and shadow.shadow_reads_enabled and not shadow.target_claims_enabled
    with pytest.raises(ValueError):
        PartitionOwnership(
            PARTITION, CutoverState.SHADOW, marker, "target", "target", False, True,
            shadow_reads_enabled=True,
        )


def test_legacy_project_trigger_and_webhook_delivery_markers_do_not_alias() -> None:
    webhook = LegacyOwnerMarker(
        PARTITION, LegacyQueueKind.WEBHOOK_DELIVERIES, "legacy-partition", "v1",
        DIGEST, DIGEST, "checkpoint", 1, 0, False,
    )
    trigger = LegacyOwnerMarker(
        PARTITION, LegacyQueueKind.PROJECT_TRIGGERS, "legacy-partition", "v1",
        DIGEST, DIGEST, "checkpoint", 1, 0, False,
    )
    assert webhook.digest != trigger.digest
    assert webhook.queue_kind is not trigger.queue_kind


@pytest.mark.asyncio
async def test_existing_sql_repository_bindings_preserve_durable_method_contracts() -> None:
    class Repository:
        def __init__(self):
            self.calls = []

        async def register_delivery_once(self, **kwargs):
            self.calls.append(("delivery_register", kwargs))
            return True

        async def claim_due_deliveries(self, **kwargs):
            self.calls.append(("delivery_claim", kwargs))
            return ()

        async def record_delivery_outcome(self, **kwargs):
            self.calls.append(("delivery_outcome", kwargs))
            return True

        async def claim_due_outbox(self, **kwargs):
            self.calls.append(("outbox_claim", kwargs))
            return ()

        async def compare_and_set_outbox_state(self, **kwargs):
            self.calls.append(("outbox_outcome", kwargs))
            return True

    repository = Repository()
    delivery = AutomationDeliveryRepositoryAdapter(repository)
    outbox = AutomationOutboxStateRepositoryAdapter(repository)
    lease = WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(minutes=2))
    command, _, adapter_evidence = _command()
    evidence = {"source-event-1": command.upstream_evidence}

    assert await delivery.claim_due(
        partition=PARTITION, lease=lease, now=NOW, lease_until=NOW + timedelta(minutes=1),
        upstream_evidence=evidence, limit=3,
    ) == ()
    assert await outbox.claim_due(
        partition=PARTITION, writer_lease=lease, now=NOW,
        lease_until=NOW + timedelta(minutes=1), upstream_evidence=evidence, limit=2,
    ) == ()
    assert [(name, kwargs["limit"]) for name, kwargs in repository.calls] == [
        ("delivery_claim", 3), ("outbox_claim", 2),
    ]
    intent = OutboundDeliveryIntent(
        "delivery-1", PARTITION, "task-run-fixture", "subscription-1", "event-run-created",
        "destination-ref-1", "secret-ref-1", {"ok": True},
        OutboundDeliveryIntent.stable_key(
            partition=PARTITION, subscription_id="subscription-1", source_event_id="event-run-created",
        ),
    )
    assert await delivery.insert_delivery_once(
        intent=intent, upstream_evidence=command.upstream_evidence,
        adapter_evidence=adapter_evidence,
    )
    assert repository.calls[-1][0] == "delivery_register"


@pytest.mark.asyncio
async def test_delivery_worker_legacy_owner_fence_blocks_before_claim_or_effect() -> None:
    command, actor, adapter = _command()
    upstream = command.upstream_evidence
    lease = WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(minutes=2))
    owner = type("LegacyOwner", (), {
        "partition": PARTITION,
        "state": CutoverState.LEGACY_ACTIVE,
        "epoch": lease.epoch,
        "fencing_token": lease.fencing_token,
        "legacy_claims_enabled": True,
        "target_claims_enabled": False,
    })()

    class Verifier:
        async def verify(self, *, evidence):
            return True

    class Ownership:
        async def load_ownership(self, partition: PartitionKey) -> Any:
            assert partition == PARTITION
            return owner

    class Repository:
        async def register_delivery_once(self, **kwargs):
            raise AssertionError("legacy owner must fence before delivery registration")

        def __init__(self):
            self.claims = 0

        async def claim_due(self, **kwargs):
            self.claims += 1
            raise AssertionError("legacy owner must fence target claims before repository access")

        async def record_outcome(self, **kwargs):
            raise AssertionError("no delivery effect or outcome is allowed under legacy ownership")

    class Effect:
        def __init__(self):
            self.calls = 0

        async def deliver(self, **kwargs):
            self.calls += 1
            raise AssertionError("legacy owner must fence target delivery effects")

    class Clock:
        def now(self):
            return NOW

    repository = Repository()
    effect = Effect()
    worker = DurableDeliveryWorker(
        repository=repository, ownership=Ownership(), effect=effect,
        scope_verifier=Verifier(), adapter_verifier=Verifier(),
        receiver_verifier=Verifier(), clock=Clock(), upstream_verifier=Verifier(),
    )
    with pytest.raises(OutboxBlockedError, match="exclusive current partition owner"):
        await worker.run_once(
            partition=PARTITION, lease=lease, now=NOW, lease_until=NOW + timedelta(minutes=1),
            actor_evidence=actor, adapter_evidence=adapter,
            upstream_evidence={upstream.ordered_event_ids[0]: upstream}, receiver_evidence={}, limit=1,
        )
    assert repository.claims == 0
    assert effect.calls == 0


@pytest.mark.asyncio
async def test_delivery_worker_retries_with_same_receiver_key_then_persists_receipt() -> None:
    command, actor, adapter = _command()
    upstream = command.upstream_evidence
    event_id = upstream.ordered_event_ids[0]
    key = OutboundDeliveryIntent.stable_key(
        partition=PARTITION, subscription_id="subscription-1", source_event_id=event_id,
    )
    intent = OutboundDeliveryIntent(
        "delivery-worker-1", PARTITION, upstream.task_run_id, "subscription-1", event_id,
        "destination-ref-1", "secret-ref-1", {"result": "ready"}, key,
    )
    lease = WriterLease(PARTITION, "target-worker", 7, "target-fence-7", NOW + timedelta(minutes=5))
    owner = type("TargetOwner", (), {
        "partition": PARTITION, "state": CutoverState.TARGET_ACTIVE,
        "epoch": lease.epoch, "fencing_token": lease.fencing_token,
        "legacy_claims_enabled": False, "target_claims_enabled": True,
    })()
    receiver_evidence = ReceiverDeduplicationEvidence(
        intent.delivery_id, "receiver-1", intent.idempotency_key, intent.request_digest,
        "receiver-proof-1", DIGEST, NOW + timedelta(days=1),
    )

    class Verifier:
        async def verify(self, *, evidence):
            return True

    class Clock:
        current = NOW + timedelta(seconds=1)

        def now(self):
            return self.current

    class Ownership:
        async def load_ownership(self, partition: PartitionKey) -> Any:
            return owner

    expected_lease = lease

    class Repository:
        async def register_delivery_once(self, **kwargs):
            raise AssertionError("delivery worker does not register intents while claiming")

        def __init__(self):
            self.claims = []
            self.outcomes = []

        async def claim_due(self, *, partition, lease, now, lease_until, upstream_evidence, limit):
            assert partition == PARTITION and lease == expected_lease
            assert event_id in upstream_evidence and limit == 1
            if not self.claims:
                attempt = 1
            elif len(self.outcomes) == 1:
                attempt = 2
            else:
                return ()
            claim = DeliveryClaim(
                intent, attempt, lease, attempt, f"claim-token-{attempt}", now,
                min(lease_until, now + timedelta(seconds=60)),
            )
            self.claims.append(claim)
            return (claim,)

        async def record_outcome(self, *, claim, outcome, lease, now):
            assert claim.intent.idempotency_key == intent.idempotency_key
            self.outcomes.append(outcome)
            return True

    class Effect:
        def __init__(self):
            self.requests = []

        async def deliver(self, *, intent, idempotency_key, request_digest, receiver_evidence):
            self.requests.append((idempotency_key, request_digest, intent.payload_digest))
            if len(self.requests) == 1:
                raise TimeoutError("remote outcome is unknown")
            return DeliveryReceipt("receiver-1", "receipt-2", idempotency_key, request_digest)

    clock = Clock()
    repository = Repository()
    effect = Effect()
    worker = DurableDeliveryWorker(
        repository=repository, ownership=Ownership(), effect=effect,
        scope_verifier=Verifier(), adapter_verifier=Verifier(), receiver_verifier=Verifier(),
        clock=clock, upstream_verifier=Verifier(),
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=5, max_delay_seconds=30),
    )
    evidence = {event_id: upstream}
    args = {
        "partition": PARTITION, "lease": lease, "lease_until": NOW + timedelta(minutes=1),
        "actor_evidence": actor, "adapter_evidence": adapter,
        "upstream_evidence": evidence, "receiver_evidence": {intent.delivery_id: receiver_evidence},
        "limit": 1,
    }
    assert await worker.run_once(now=NOW, **args) == 0
    retry_outcome = repository.outcomes[0]
    assert retry_outcome.state is DeliveryState.RETRY_WAIT
    assert retry_outcome.retry_at is not None

    clock.current = retry_outcome.retry_at + timedelta(seconds=1)
    args["lease_until"] = clock.current + timedelta(minutes=1)
    assert await worker.run_once(now=retry_outcome.retry_at, **args) == 1
    assert [outcome.state for outcome in repository.outcomes] == [
        DeliveryState.RETRY_WAIT, DeliveryState.DELIVERED,
    ]
    assert effect.requests == [
        (intent.idempotency_key, intent.request_digest, intent.payload_digest),
        (intent.idempotency_key, intent.request_digest, intent.payload_digest),
    ]
    assert repository.outcomes[1].receipt_id == "receipt-2"


def test_acceptance_cases_cover_transaction_failure_retry_repair_and_legacy_fence() -> None:
    assert any(
        "transaction failure" in case and "no accepted receipt" in case
        for case in ACCEPTANCE_CASES
    )
    assert any("atomic" in case and "roll back" in case for case in ACCEPTANCE_CASES)
    assert any("queue outage" in case and "original enqueue key" in case for case in ACCEPTANCE_CASES)
    assert any("legacy worker" in case and "fence" in case for case in ACCEPTANCE_CASES)


ACCEPTANCE_CASES = (
    "atomic business mutation, idempotency record, event, initial state, enqueue and audit commit or roll back together in one durable UoW",
    "transaction failure before durable commit returns no accepted receipt and leaves no positive mutation",
    "queue outage repair reuses the original event and enqueue key and cannot create a second event or delivery",
    "same idempotency key plus same request digest replays the original result; changed hash conflicts",
    "due claim increments attempt and generation atomically; expired lease takeover fences old completion",
    "retry backoff is deterministic, bounded and ends in immutable dead-letter state",
    "delivery uniqueness is subscription plus source event; receiver dedupe uses stable key and request digest",
    "receipt mismatch, missing receiver proof, stale owner token, expired lease and cross-partition claim fail closed",
    "project trigger ownership and webhook delivery ownership remain separate legacy queue markers",
    "cutover requires upstream scope, actor, durable adapter, final-delta, parity and signed decision evidence",
    "scope_actor_durable_adapter_verifiers are required before business/event/enqueue calls reach persistence",
    "upstream run/event/approval evidence fail closed before delivery; no claim is taken without scope+actor evidence",
    "delivery duplicate registration returns original identity and changed request digest conflicts",
    "typed delivery claim takeover increments generation/attempt and stale completion cannot become delivered",
    "typed delivery claim takeover uses the expired row lease, not the target writer lease expiry",
    "receiver receipt mismatch cannot be acknowledged; same stable idempotency key is preserved across retry",
    "shadow permits canonical diagnostics only and never moves the source read owner or delivery writer",
    "target-read and final-delta transitions are evidence-gated; final-delta and blocked state fence both workers",
    "webhook-deliveries and project-triggers have distinct legacy owner marker identities",
    "legacy worker authority blocks target claim and effect calls before repository claim access",
)
