"""Test-only ports for MIG-AUTO-01; never production persistence."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from prodavan.application.migration.automation_ingress.ingress import (
    ApprovalLink,
    AutomationIngressService,
    ClaimResult,
    ClaimStatus,
    DefinitionIdentity,
    DefinitionProof,
    EndpointMapping,
    EndpointResolution,
    EventLink,
    GateEvidence,
    ProjectAssertion,
    QuarantineReceipt,
    ResolutionStatus,
    RunLink,
    ScopeProof,
    SignatureProof,
    SigningKey,
    SubscriptionIdentity,
    WebhookRequest,
)

NOW = datetime(2026, 11, 1, 14, 25, tzinfo=UTC)
SECRET = b"acceptance-signing-key"
BODY = b'{"event_id":"event-1001","event_type":"automation.refresh","data":{"value":1}}'
VERSION = DefinitionIdentity("definition-01", "version-01", "sha256:" + "a" * 64)
MAPPING = EndpointMapping(
    mapping_id="mapping-01",
    subscription=SubscriptionIdentity("partner-hook", "subscription-01"),
    owner=ProjectAssertion("project-01", "tenant-01"),
    endpoint_id="endpoint-01",
    automation_id="automation-01",
    actor_id="automation-owner-01",
    definition=VERSION,
    secret_ref="secret://tenant-01/project-01/endpoint-01/signing-key",
    schema_version="partner.event.v1",
    routes=("/v1/automation/endpoint-01/events", "/v0/hooks/partner-hook"),
    event_types=("automation.refresh",),
)
SCOPE = ScopeProof(
    "tenant-01", "project-01", "scope-01", "parent-proof-01", "snapshot-01",
    "m04-gate-01", True, True,
)
DEFINITION = DefinitionProof(
    "tenant-01", "project-01", "automation-01", "automation-owner-01", VERSION,
    "refresh-task", "agent_run.create", ("automation.refresh",),
    "definition-evidence-01", "m04e-gate-01", "snapshot-01", True,
)
PARTITION_DIGEST = "sha256:" + hashlib.sha256(
    b'{"project_id":"project-01","tenant_id":"tenant-01"}'
).hexdigest()
M04_GATE = GateEvidence(
    "M04", "m04-gate-01", PARTITION_DIGEST, "sha256:" + "1" * 64,
    "signature-ref:m04", "migration-owner", ("scope-01", "parent-proof-01"), True,
)
M04E_GATE = GateEvidence(
    "M04.e", "m04e-gate-01", PARTITION_DIGEST, "sha256:" + "2" * 64,
    "signature-ref:m04e", "migration-owner",
    ("definition-evidence-01", M04_GATE.digest), True,
)


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return digest_bytes(encoded.encode("utf-8"))


def signed_request(*, body: bytes = BODY, event_id: str = "event-1001", signature: str | None = None,
                   route: str = "/v1/automation/endpoint-01/events") -> WebhookRequest:
    value = signature or "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return WebhookRequest(
        "partner-hook", "subscription-01", route, event_id, body,
        {"X-Webhook-Signature": value}, NOW,
    )


class FakeEndpointResolver:
    def __init__(self, mappings: tuple[EndpointMapping, ...] = (MAPPING,)) -> None:
        self.mappings = tuple(mappings)

    async def resolve(self, *, identity: SubscriptionIdentity, route: str) -> EndpointResolution:
        found = [mapping for mapping in self.mappings
                 if mapping.subscription == identity and route in mapping.routes]
        if len(found) == 1:
            return EndpointResolution(ResolutionStatus.RESOLVED, found[0], "mapping-evidence-01")
        status = ResolutionStatus.AMBIGUOUS if found else ResolutionStatus.MISSING
        return EndpointResolution(status)


class FakeSecretResolver:
    async def resolve(self, *, secret_ref: str, tenant_id: str, project_id: str,
                      endpoint_id: str) -> SigningKey | None:
        if secret_ref != MAPPING.secret_ref:
            return None
        return SigningKey(secret_ref, tenant_id, project_id, endpoint_id, "secret-version-01", True, SECRET)


class FakeSignatureVerifier:
    def __init__(self) -> None:
        from prodavan.application.migration.automation_ingress.ingress import HmacVerifier
        self._verifier = HmacVerifier(FakeSecretResolver())

    async def verify(self, *, request: WebhookRequest, mapping: EndpointMapping) -> SignatureProof:
        return await self._verifier.verify(request=request, mapping=mapping)


class FakeScopeResolver:
    def __init__(self, proof: ScopeProof | None = SCOPE) -> None:
        self.proof = proof

    async def resolve(self, *, assertion: ProjectAssertion) -> ScopeProof | None:
        if self.proof is None or self.proof.project_id != assertion.project_id:
            return None
        if assertion.tenant_id and self.proof.tenant_id != assertion.tenant_id:
            return None
        return self.proof


class FakeDefinitionResolver:
    def __init__(self, proof: DefinitionProof | None = DEFINITION) -> None:
        self.proof = proof

    async def resolve(self, *, tenant_id: str, project_id: str, identity: DefinitionIdentity,
                      snapshot_id: str) -> DefinitionProof | None:
        proof = self.proof
        if proof is None or (tenant_id, project_id, identity, snapshot_id) != (
            proof.tenant_id, proof.project_id, proof.identity, proof.snapshot_id
        ):
            return None
        return proof


class FakeGateVerifier:
    def __init__(self, *, m04: GateEvidence | None = M04_GATE,
                 m04e: GateEvidence | None = M04E_GATE) -> None:
        self.m04 = m04
        self.m04e = m04e

    async def verify_m04(self, *, scope: ScopeProof) -> GateEvidence | None:
        return self.m04

    async def verify_m04e(self, *, scope: ScopeProof,
                          definition: DefinitionProof) -> GateEvidence | None:
        return self.m04e


class FakeAtomicTransaction:
    """In-memory port double only; it does not prove durable transaction semantics."""

    def __init__(self) -> None:
        self.claims: dict[str, tuple[str, RunLink]] = {}
        self.quarantines: dict[str, QuarantineReceipt] = {}
        self.conflicts: list[tuple[str, str]] = []
        self.claim_calls = 0
        self.quarantine_calls = 0
        self.blocker: str | None = None
        self.approvals: tuple[ApprovalLink, ...] = ()
        self.last_claim: Any | None = None

    async def claim_or_resume(self, *, claim: Any) -> ClaimResult:
        self.claim_calls += 1
        self.last_claim = claim
        if self.blocker:
            return ClaimResult(ClaimStatus.BLOCKED, blocker=self.blocker)
        existing = self.claims.get(claim.idempotency_key)
        if existing is not None:
            old_digest, link = existing
            if old_digest != claim.request_digest:
                self.conflicts.append((claim.idempotency_key, claim.request_digest))
                return ClaimResult(
                    ClaimStatus.CONFLICT, reason="idempotency_hash_conflict",
                    conflict_evidence_id=f"conflict-audit-{len(self.conflicts)}",
                )
            return ClaimResult(ClaimStatus.REPLAY, link=link)
        number = len(self.claims) + 1
        agent_run_id = f"agent-run-{number}"
        task_run_id = f"task-run-{number}"
        event = EventLink(
            f"ordered-event-{number}", 1, agent_run_id, claim.tenant_id, claim.project_id,
            claim.source.event_id, claim.correlation_id, claim.request_digest, claim.event_type,
        )
        link = RunLink(
            task_run_id, agent_run_id, claim.tenant_id, claim.project_id, None,
            claim.agent_run_key, claim.request_digest, claim.provenance_digest,
            claim.correlation_id, f"transaction-{number}", f"audit-{number}",
            claim.actor_id, f"claim-evidence-{number}", (event,), self.approvals, NOW,
        )
        self.claims[claim.idempotency_key] = (claim.request_digest, link)
        return ClaimResult(ClaimStatus.APPLIED, link=link)

    async def resume_trigger(self, *, claim: Any, source_claim_marker: str) -> ClaimResult:
        return ClaimResult(ClaimStatus.BLOCKED, blocker="trigger_claim_marker_not_found")

    async def quarantine(self, *, evidence: Any) -> QuarantineReceipt:
        self.quarantine_calls += 1
        current = self.quarantines.get(evidence.key)
        if current is not None:
            if current.digest != evidence.digest:
                raise RuntimeError("quarantine digest conflict")
            return current
        number = len(self.quarantines) + 1
        receipt = QuarantineReceipt(
            f"quarantine-{number}", evidence.key, evidence.digest, f"quarantine-audit-{number}"
        )
        self.quarantines[evidence.key] = receipt
        return receipt


CASES = (
    "exact source/subscription/route resolver returns one owned endpoint; missing or ambiguous rows quarantine",
    "HMAC-SHA256 verifies exact raw bytes with a tenant/project/endpoint scoped secret_ref",
    "strict normalized JSON rejects duplicate keys, non-finite numbers, invalid UTF-8 and extra envelope fields",
    "payload byte, depth, node, item, string and integer limits are enforced before claim",
    "tenant/project are taken from typed scope proof, never from caller payload or URL",
    "exact replay returns the original task/run/event/approval links",
    "same scoped idempotency identity with changed request hash conflicts without overwrite",
    "unsupported event, invalid signature and unresolved source never create a task intent",
    "missing durable transaction and resolver ports fail closed without positive identifiers",
    "provenance carries source/evidence digests but no raw body, headers or secret material",
    "outbound delivery is rejected by the inbound service",
    "memory fakes are test doubles only and do not prove persistence readiness",
)
