"""MIG-AUTO-01 acceptance tests; intentionally not executed during freeze."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from prodavan.application.migration.automation_ingress.ingress import (
    AutomationIngressService,
    ClaimStatus,
    IngressStatus,
    OutboundDeliveryRequest,
    PayloadLimits,
    TransactionPort,
)
from .acceptance_fixtures import (
    BODY,
    CASES,
    MAPPING,
    SECRET,
    FakeAtomicTransaction,
    FakeDefinitionResolver,
    FakeEndpointResolver,
    FakeGateVerifier,
    FakeScopeResolver,
    FakeSignatureVerifier,
    digest_bytes,
    signed_request,
)

_DEFAULT = object()


def make_service(*, endpoint: Any = _DEFAULT, scopes: Any = _DEFAULT,
                 definitions: Any = _DEFAULT, gates: Any = _DEFAULT,
                 signatures: Any = _DEFAULT, transaction: Any = _DEFAULT,
                 limits: PayloadLimits = PayloadLimits()) -> AutomationIngressService:
    return AutomationIngressService(
        endpoints=FakeEndpointResolver() if endpoint is _DEFAULT else endpoint,
        triggers=None,
        trigger_fence=None,
        scopes=FakeScopeResolver() if scopes is _DEFAULT else scopes,
        definitions=FakeDefinitionResolver() if definitions is _DEFAULT else definitions,
        gates=FakeGateVerifier() if gates is _DEFAULT else gates,
        signatures=FakeSignatureVerifier() if signatures is _DEFAULT else signatures,
        transaction=FakeAtomicTransaction() if transaction is _DEFAULT else transaction,
        limits=limits,
    )


def test_acceptance_matrix_covers_claim_conflict_and_fail_closed_cases() -> None:
    assert len(CASES) >= 10
    assert any("changed request hash" in item for item in CASES)
    assert any("unsupported event" in item for item in CASES)
    assert any("memory fakes" in item for item in CASES)


def test_endpoint_contract_has_explicit_scope_and_event_allowlist() -> None:
    assert MAPPING.subscription.subscription_id == "subscription-01"
    assert MAPPING.owner.tenant_id == "tenant-01"
    assert MAPPING.definition.version_id == "version-01"
    assert MAPPING.routes[0] != MAPPING.routes[1]
    assert MAPPING.event_types == ("automation.refresh",)


@pytest.mark.asyncio
async def test_exact_replay_returns_original_task_run_agent_run_and_event_ids() -> None:
    tx = FakeAtomicTransaction()
    app = make_service(transaction=tx)
    first = await app.accept(signed_request())
    replay = await app.accept(signed_request())
    assert first.status is IngressStatus.ACCEPTED
    assert replay.status is IngressStatus.DUPLICATE
    assert (first.task_run_id, first.agent_run_id, first.event_ids) == (
        replay.task_run_id, replay.agent_run_id, replay.event_ids
    )
    assert tx.claim_calls == 2
    assert len(tx.claims) == 1


@pytest.mark.asyncio
async def test_same_scoped_identity_changed_body_hash_conflicts_without_overwrite() -> None:
    tx = FakeAtomicTransaction()
    app = make_service(transaction=tx)
    first = await app.accept(signed_request())
    changed_body = b'{"event_id":"event-1001","event_type":"automation.refresh","data":{"value":2}}'
    second = await app.accept(signed_request(body=changed_body))
    assert first.status is IngressStatus.ACCEPTED
    assert second.status is IngressStatus.CONFLICT
    assert second.evidence_id == "conflict-audit-1"
    assert len(tx.claims) == 1
    assert len(tx.conflicts) == 1


@pytest.mark.asyncio
async def test_mapping_replacement_preserves_identity_and_conflicts_on_request_hash() -> None:
    tx = FakeAtomicTransaction()
    first = await make_service(transaction=tx).accept(signed_request())
    replacement = replace(MAPPING, mapping_id="mapping-new", endpoint_id="endpoint-new")
    second = await make_service(
        endpoint=FakeEndpointResolver((replacement,)), transaction=tx
    ).accept(signed_request())
    assert first.status is IngressStatus.ACCEPTED
    assert second.status is IngressStatus.CONFLICT
    assert len(tx.claims) == 1


@pytest.mark.asyncio
async def test_invalid_signature_is_quarantined_before_claim() -> None:
    tx = FakeAtomicTransaction()
    result = await make_service(transaction=tx).accept(
        signed_request(signature="sha256=" + "0" * 64)
    )
    assert result.status is IngressStatus.QUARANTINED
    assert tx.claim_calls == 0
    assert len(tx.quarantines) == 1


@pytest.mark.asyncio
async def test_signed_event_id_mismatch_is_quarantined_before_claim() -> None:
    tx = FakeAtomicTransaction()
    result = await make_service(transaction=tx).accept(
        signed_request(event_id="caller-assertion")
    )
    assert result.status is IngressStatus.QUARANTINED
    assert tx.claim_calls == 0


@pytest.mark.asyncio
async def test_payload_limits_and_duplicate_json_keys_are_rejected() -> None:
    tx = FakeAtomicTransaction()
    app = make_service(
        transaction=tx,
        limits=PayloadLimits(max_bytes=256, max_depth=3, max_nodes=24,
                             max_items=4, max_string_chars=24),
    )
    duplicate = b'{"event_id":"event-1001","event_type":"automation.refresh","data":{"x":1,"x":2}}'
    too_large = (b'{"event_id":"event-1001","event_type":"automation.refresh","data":{"x":"'
                 + b"z" * 300 + b'"}}')
    first = await app.accept(signed_request(body=duplicate))
    second = await app.accept(signed_request(body=too_large))
    assert first.status is IngressStatus.QUARANTINED
    assert second.status is IngressStatus.QUARANTINED
    assert tx.claim_calls == 0


@pytest.mark.asyncio
async def test_unsupported_event_and_ambiguous_endpoint_do_not_create_claims() -> None:
    tx = FakeAtomicTransaction()
    unsupported_body = b'{"event_id":"event-1001","event_type":"unknown.event","data":{}}'
    unsupported = await make_service(transaction=tx).accept(
        signed_request(body=unsupported_body)
    )
    ambiguous = await make_service(
        endpoint=FakeEndpointResolver((MAPPING, replace(MAPPING, mapping_id="second-map"))),
        transaction=tx,
    ).accept(signed_request())
    assert unsupported.status is IngressStatus.QUARANTINED
    assert unsupported.reason == "unsupported_subscription_event_type"
    assert ambiguous.status is IngressStatus.QUARANTINED
    assert tx.claim_calls == 0


@pytest.mark.asyncio
async def test_missing_endpoint_resolver_fails_closed() -> None:
    result = await make_service(endpoint=None).accept(signed_request())
    assert result.status is IngressStatus.BLOCKED
    assert result.blocker == "subscription_resolver_unwired"
    assert result.task_run_id is None and result.agent_run_id is None


@pytest.mark.asyncio
async def test_missing_scope_definition_gate_and_transaction_ports_fail_closed() -> None:
    cases = (
        (make_service(scopes=None), "m04_scope_resolver_unwired"),
        (make_service(definitions=None), "m04e_definition_resolver_unwired"),
        (make_service(gates=None), "promotion_gate_verifier_unwired"),
        (make_service(transaction=None), "atomic_ingress_transaction_unwired"),
    )
    for app, blocker in cases:
        result = await app.accept(signed_request())
        assert result.status is IngressStatus.BLOCKED
        assert result.blocker == blocker
        assert result.task_run_id is None and result.agent_run_id is None


@pytest.mark.asyncio
async def test_missing_m04_gate_fails_closed_without_claim() -> None:
    tx = FakeAtomicTransaction()

    class MissingGate:
        async def verify_m04(self, *, scope):
            return None

        async def verify_m04e(self, *, scope, definition):
            return None

    result = await make_service(gates=MissingGate(), transaction=tx).accept(signed_request())
    assert result.status is IngressStatus.BLOCKED
    assert tx.claim_calls == 0


@pytest.mark.asyncio
async def test_atomic_transaction_blocker_does_not_expose_positive_run_ids() -> None:
    tx = FakeAtomicTransaction()
    tx.blocker = "durable_adapter_unavailable"
    result = await make_service(transaction=tx).accept(signed_request())
    assert result.status is IngressStatus.BLOCKED
    assert result.blocker == "durable_adapter_unavailable"
    assert result.task_run_id is None and result.agent_run_id is None
    assert not result.event_ids


@pytest.mark.asyncio
async def test_durable_task_intent_and_redacted_provenance_are_passed_to_transaction_port() -> None:
    tx = FakeAtomicTransaction()
    result = await make_service(transaction=tx).accept(signed_request())
    claim = tx.last_claim
    assert result.status is IngressStatus.ACCEPTED
    assert claim is not None
    assert claim.direction.value == "inbound_trigger"
    assert claim.task_name == "refresh-task"
    assert claim.command == "agent_run.create"
    assert claim.session_id is None
    assert claim.raw_payload_digest == digest_bytes(BODY)
    assert claim.payload_digest != claim.raw_payload_digest
    assert claim.agent_run_key.startswith("automation-ingress:")
    assert claim.source_evidence_id == "mapping-evidence-01"
    assert claim.signature_key_version_id == "secret-version-01"
    assert BODY.decode() not in claim.provenance_json
    assert SECRET.decode() not in claim.provenance_json
    assert "X-Webhook-Signature" not in claim.provenance_json
    assert claim.raw_payload_digest in claim.provenance_json
    payload = json.loads(claim.payload_json)
    assert payload["data"]["value"] == 1
    link = next(iter(tx.claims.values()))[1]
    assert link.task_run_id != link.agent_run_id
    assert link.events[0].sequence == 1


@pytest.mark.asyncio
async def test_quarantine_is_idempotent_and_excludes_raw_payload_and_secret() -> None:
    tx = FakeAtomicTransaction()
    request = signed_request(signature="invalid-secret-signature")
    first = await make_service(transaction=tx).accept(request)
    replay = await make_service(transaction=tx).accept(request)
    assert first.status is IngressStatus.QUARANTINED
    assert replay.evidence_id == first.evidence_id
    assert len(tx.quarantines) == 1
    evidence = next(iter(tx.quarantines.values()))
    assert SECRET.decode() not in str(evidence)
    assert BODY.decode() not in str(evidence)


@pytest.mark.asyncio
async def test_outbound_delivery_is_rejected_by_inbound_service() -> None:
    tx = FakeAtomicTransaction()
    result = await make_service(transaction=tx).accept(OutboundDeliveryRequest(
        "task-run-01", "subscription-01", "event-01", digest_bytes(BODY)
    ))
    assert result.status is IngressStatus.REJECTED
    assert result.reason == "outbound_delivery_is_not_ingress"
    assert tx.claim_calls == 0


def test_transaction_port_contract_requires_atomic_identity_conflict_and_replay() -> None:
    contract = " ".join((TransactionPort.claim_or_resume.__doc__ or "").split())
    assert ClaimStatus.CONFLICT.value == "conflict"
    assert "Atomically persist" in contract
    assert "changed digest conflicts" in contract
