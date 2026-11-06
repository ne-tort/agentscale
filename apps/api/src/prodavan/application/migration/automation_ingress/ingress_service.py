"""MIG-AUTO-01 typed inbound automation ingress application boundary.

Application algorithms and ports only. Production persistence, DDL, wiring,
imports and live writes are outside this exclusive task zone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, NewType, Protocol, TypeAlias

TenantId = NewType("TenantId", str)
ProjectId = NewType("ProjectId", str)
SourceId = NewType("SourceId", str)
EventId = NewType("EventId", str)
SubscriptionId = NewType("SubscriptionId", str)


def _required(value: str, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


def _valid_digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdef" for char in value[7:])


def _canonical(value: Any) -> str:
    def thaw(item: Any) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            return {key: thaw(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [thaw(child) for child in item]
        return item
    return json.dumps(thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _json_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8", errors="strict")).hexdigest()


def _raw_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("non_finite_json_number")


@dataclass(frozen=True, slots=True)
class PayloadLimits:
    max_bytes: int = 65_536
    max_depth: int = 24
    max_nodes: int = 4_096
    max_items: int = 512
    max_string_chars: int = 8_192

    def __post_init__(self) -> None:
        if not 1 <= self.max_bytes <= 1_048_576 or not 1 <= self.max_depth <= 64:
            raise ValueError("byte/depth limit outside bounds")
        if not 1 <= self.max_nodes <= 65_536 or not 1 <= self.max_items <= 4_096:
            raise ValueError("node/item limit outside bounds")
        if not 1 <= self.max_string_chars <= 65_536:
            raise ValueError("string limit outside bounds")


def _bounded(value: Any, limits: PayloadLimits) -> Any:
    count = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > limits.max_nodes:
            raise ValueError("payload_node_limit_exceeded")
        if depth > limits.max_depth:
            raise ValueError("payload_depth_limit_exceeded")
        if item is None or type(item) is bool:
            return item
        if isinstance(item, str):
            if len(item) > limits.max_string_chars:
                raise ValueError("payload_string_limit_exceeded")
            item.encode("utf-8", errors="strict")
            return item
        if type(item) is int:
            if item.bit_length() > 256:
                raise ValueError("payload_integer_limit_exceeded")
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("payload_non_finite_number")
            return item
        if isinstance(item, Mapping):
            if len(item) > limits.max_items:
                raise ValueError("payload_object_item_limit_exceeded")
            copied: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > limits.max_string_chars:
                    raise ValueError("payload_key_invalid_or_over_limit")
                key.encode("utf-8", errors="strict")
                copied[key] = visit(child, depth + 1)
            return copied
        if isinstance(item, (tuple, list)):
            if len(item) > limits.max_items:
                raise ValueError("payload_array_item_limit_exceeded")
            return [visit(child, depth + 1) for child in item]
        raise ValueError("payload_contains_non_json_value")

    try:
        copied = visit(value, 0)
        encoded = _canonical(copied).encode("utf-8", errors="strict")
    except (RecursionError, UnicodeError, TypeError) as exc:
        raise ValueError("payload_not_bounded_json") from exc
    if len(encoded) > limits.max_bytes:
        raise ValueError("payload_byte_limit_exceeded")
    return copied


def _parse_webhook(body: bytes, limits: PayloadLimits) -> tuple[dict[str, Any], str, str]:
    if len(body) > limits.max_bytes:
        raise ValueError("payload_byte_limit_exceeded")
    try:
        envelope = json.loads(body.decode("utf-8", errors="strict"),
                              object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        envelope = _bounded(envelope, limits)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise ValueError("webhook_not_strict_json") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"event_id", "event_type", "data"}:
        raise ValueError("webhook_envelope_shape_unsupported")
    event_id = _required(envelope["event_id"], "event_id", 256)
    event_type = _required(envelope["event_type"], "event_type", 128)
    if not isinstance(envelope["data"], dict):
        raise ValueError("webhook_data_must_be_object")
    return envelope["data"], event_id, event_type


class SourceKind(StrEnum):
    WEBHOOK = "webhook"
    PROJECT_TRIGGER = "project_trigger"


class Direction(StrEnum):
    INBOUND_TRIGGER = "inbound_trigger"
    OUTBOUND_DELIVERY = "outbound_delivery"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"


class IngressStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class ClaimStatus(StrEnum):
    APPLIED = "applied"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    kind: SourceKind
    source_id: str
    event_id: str
    schema_version: str

    def __post_init__(self) -> None:
        _required(self.source_id, "source_id", 128)
        _required(self.event_id, "event_id", 256)
        _required(self.schema_version, "schema_version", 64)


@dataclass(frozen=True, slots=True)
class SubscriptionIdentity:
    source_id: str
    subscription_id: str

    def __post_init__(self) -> None:
        _required(self.source_id, "source_id", 128)
        _required(self.subscription_id, "subscription_id", 128)


@dataclass(frozen=True, slots=True)
class ProjectAssertion:
    project_id: str
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.project_id, "project_id", 64)
        if self.tenant_id is not None:
            _required(self.tenant_id, "tenant_id", 64)


@dataclass(frozen=True, slots=True)
class DefinitionIdentity:
    definition_id: str
    version_id: str
    artifact_digest: str

    def __post_init__(self) -> None:
        _required(self.definition_id, "definition_id", 64)
        _required(self.version_id, "version_id", 64)
        if not _valid_digest(self.artifact_digest):
            raise ValueError("artifact digest invalid")


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    source_id: str
    subscription_id: str
    route: str
    event_id_assertion: str
    body: bytes
    headers: Mapping[str, str]
    received_at: datetime

    def __post_init__(self) -> None:
        _required(self.source_id, "source_id", 128)
        _required(self.subscription_id, "subscription_id", 128)
        if not _required(self.route, "route", 512).startswith("/"):
            raise ValueError("route must be absolute")
        _required(self.event_id_assertion, "event_id_assertion", 256)
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be aware")
        if not isinstance(self.headers, Mapping) or len(self.headers) > 128:
            raise ValueError("headers must be bounded")
        folded: dict[str, str] = {}
        for name, value in self.headers.items():
            _required(name, "header name", 128)
            if not isinstance(value, str) or len(value) > 8_192:
                raise ValueError("header value exceeds bound")
            key = name.casefold()
            if key in folded:
                raise ValueError("duplicate_case_insensitive_header")
            folded[key] = value
        object.__setattr__(self, "headers", MappingProxyType(folded))


@dataclass(frozen=True, slots=True)
class ProjectTriggerRequest:
    trigger_id: str

    def __post_init__(self) -> None:
        _required(self.trigger_id, "trigger_id", 128)


@dataclass(frozen=True, slots=True)
class OutboundDeliveryRequest:
    task_run_id: str
    subscription_id: str
    event_id: str
    payload_digest: str

    def __post_init__(self) -> None:
        for name in ("task_run_id", "subscription_id", "event_id"):
            _required(getattr(self, name), name, 256)
        if not _valid_digest(self.payload_digest):
            raise ValueError("payload digest invalid")


IngressRequest: TypeAlias = WebhookRequest | ProjectTriggerRequest


@dataclass(frozen=True, slots=True)
class EndpointMapping:
    mapping_id: str
    subscription: SubscriptionIdentity
    owner: ProjectAssertion
    endpoint_id: str
    automation_id: str
    actor_id: str
    definition: DefinitionIdentity
    secret_ref: str
    schema_version: str
    routes: tuple[str, ...]
    event_types: tuple[str, ...]
    enabled: bool = True
    inbound_enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("mapping_id", "endpoint_id", "automation_id", "actor_id", "secret_ref", "schema_version"):
            _required(getattr(self, name), name, 256)
        if not self.secret_ref.startswith("secret://") or self.owner.tenant_id is None:
            raise ValueError("scoped secret ref and tenant required")
        if not self.routes or len(set(self.routes)) != len(self.routes) or any(not route.startswith("/") for route in self.routes):
            raise ValueError("unique absolute routes required")
        if not self.event_types or len(set(self.event_types)) != len(self.event_types):
            raise ValueError("event allowlist required")


@dataclass(frozen=True, slots=True)
class EndpointResolution:
    status: ResolutionStatus
    mapping: EndpointMapping | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if (self.status is ResolutionStatus.RESOLVED) != (self.mapping is not None):
            raise ValueError("endpoint resolution mismatch")
        if self.status is ResolutionStatus.RESOLVED:
            _required(self.evidence_id or "", "mapping evidence", 256)


@dataclass(frozen=True, slots=True)
class ProjectTrigger:
    trigger_id: str
    project_id: str
    schema_version: str
    event_type: str
    payload: Mapping[str, Any]
    payload_digest: str
    mapping_id: str
    automation_id: str
    actor_id: str
    definition: DefinitionIdentity
    source_evidence_id: str
    eligible: bool
    eligibility_evidence_id: str
    state: str
    legacy_fence_id: str | None
    legacy_fence_generation: int | None
    target_claim_evidence_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("trigger_id", "project_id", "schema_version", "event_type", "mapping_id",
                     "automation_id", "actor_id", "source_evidence_id"):
            _required(getattr(self, name), name, 256)
        if not _valid_digest(self.payload_digest):
            raise ValueError("trigger digest invalid")
        if self.eligible:
            _required(self.eligibility_evidence_id, "eligibility evidence", 256)
        if self.legacy_fence_id is not None:
            _required(self.legacy_fence_id, "legacy fence", 256)
        if self.legacy_fence_generation is not None and self.legacy_fence_generation < 1:
            raise ValueError("legacy fence generation invalid")
        if self.state == "claimed" and not self.target_claim_evidence_id:
            raise ValueError("claimed trigger requires durable target claim marker")
        if self.state == "queued" and self.target_claim_evidence_id is not None:
            raise ValueError("queued trigger cannot carry claim marker")
        copied = _bounded(self.payload, PayloadLimits())
        if not isinstance(copied, dict):
            raise ValueError("trigger payload must be object")
        object.__setattr__(self, "payload", _freeze(copied))


@dataclass(frozen=True, slots=True)
class TriggerResolution:
    status: ResolutionStatus
    trigger: ProjectTrigger | None = None

    def __post_init__(self) -> None:
        if (self.status is ResolutionStatus.RESOLVED) != (self.trigger is not None):
            raise ValueError("trigger resolution mismatch")


@dataclass(frozen=True, slots=True)
class ScopeProof:
    tenant_id: str
    project_id: str
    scope_id: str
    parent_proof_id: str
    snapshot_id: str
    gate_record_id: str
    active: bool
    parent_compatible: bool

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "scope_id", "parent_proof_id", "snapshot_id", "gate_record_id"):
            _required(getattr(self, name), name, 256)


@dataclass(frozen=True, slots=True)
class DefinitionProof:
    tenant_id: str
    project_id: str
    automation_id: str
    actor_id: str
    identity: DefinitionIdentity
    task_name: str
    command: str
    events: tuple[str, ...]
    evidence_id: str
    gate_record_id: str
    snapshot_id: str
    active: bool

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "automation_id", "actor_id", "task_name", "command",
                     "evidence_id", "gate_record_id", "snapshot_id"):
            _required(getattr(self, name), name, 256)
        if not self.events or len(set(self.events)) != len(self.events):
            raise ValueError("definition event mapping required")


@dataclass(frozen=True, slots=True)
class GateEvidence:
    kind: str
    record_id: str
    partition_digest: str
    evidence_digest: str
    signature_ref: str
    decision_owner: str
    dependencies: tuple[str, ...]
    verified: bool

    def __post_init__(self) -> None:
        for name in ("kind", "record_id", "signature_ref", "decision_owner"):
            _required(getattr(self, name), name, 256)
        if not _valid_digest(self.partition_digest) or not _valid_digest(self.evidence_digest) or not self.dependencies:
            raise ValueError("gate evidence invalid")

    @property
    def digest(self) -> str:
        return self.evidence_digest


@dataclass(frozen=True, slots=True)
class SigningKey:
    secret_ref: str
    tenant_id: str
    project_id: str
    endpoint_id: str
    version_id: str
    active: bool
    key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("secret_ref", "tenant_id", "project_id", "endpoint_id", "version_id"):
            _required(getattr(self, name), name, 256)
        if not isinstance(self.key, bytes) or not 1 <= len(self.key) <= 4_096:
            raise ValueError("signing key size invalid")


@dataclass(frozen=True, slots=True)
class SignatureProof:
    valid: bool
    body_digest: str
    secret_ref: str
    key_version_id: str | None
    tenant_id: str
    project_id: str
    endpoint_id: str


class HmacVerifier:
    """Verify exact raw bytes using tenant/project/endpoint-bound key evidence."""

    def __init__(self, secrets: SecretResolverPort) -> None:
        self._secrets = secrets

    async def verify(self, *, request: WebhookRequest, mapping: EndpointMapping) -> SignatureProof:
        body_digest = _raw_digest(request.body)
        header = request.headers.get("x-webhook-signature", "")
        supplied = header.removeprefix("sha256=")
        format_ok = (header.startswith("sha256=") and len(supplied) == 64
                     and all(char in "0123456789abcdef" for char in supplied))
        key = await self._secrets.resolve(
            secret_ref=mapping.secret_ref, tenant_id=mapping.owner.tenant_id or "",
            project_id=mapping.owner.project_id, endpoint_id=mapping.endpoint_id)
        scoped = (key is not None and key.active and key.secret_ref == mapping.secret_ref
                  and key.tenant_id == mapping.owner.tenant_id
                  and key.project_id == mapping.owner.project_id
                  and key.endpoint_id == mapping.endpoint_id)
        expected = hmac.new(key.key, request.body, hashlib.sha256).hexdigest() if scoped else "0" * 64
        valid = bool(scoped and format_ok and hmac.compare_digest(supplied, expected))
        return SignatureProof(valid, body_digest, mapping.secret_ref,
                              key.version_id if scoped else None,
                              mapping.owner.tenant_id or "", mapping.owner.project_id, mapping.endpoint_id)


@dataclass(frozen=True, slots=True)
class EventLink:
    event_id: str
    sequence: int
    run_id: str
    tenant_id: str
    project_id: str
    source_event_id: str
    correlation_id: str
    request_digest: str
    event_type: str

    def __post_init__(self) -> None:
        for name in ("event_id", "run_id", "tenant_id", "project_id", "source_event_id", "correlation_id", "event_type"):
            _required(getattr(self, name), name, 256)
        if self.sequence < 1 or not _valid_digest(self.request_digest):
            raise ValueError("event link invalid")


@dataclass(frozen=True, slots=True)
class ApprovalLink:
    approval_id: str
    run_id: str
    tenant_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class IngressClaim:
    direction: Direction
    source: SourceIdentity
    subscription_id: str | None
    tenant_id: str
    project_id: str
    scope_id: str
    parent_proof_id: str
    snapshot_id: str
    m04_gate_id: str
    m04_gate_digest: str
    m04e_gate_id: str
    m04e_gate_digest: str
    m04e_dependency_digest: str
    mapping_id: str
    endpoint_id: str | None
    automation_id: str
    actor_id: str
    definition: DefinitionIdentity
    definition_evidence_id: str
    source_evidence_id: str | None
    signature_key_version_id: str | None
    source_claim_marker: str | None
    eligibility_evidence_id: str | None
    legacy_fence_id: str | None
    legacy_fence_generation: int | None
    task_name: str
    command: str
    event_type: str
    raw_payload_digest: str
    payload_digest: str
    request_digest: str
    idempotency_key: str
    agent_run_key: str
    correlation_id: str
    payload_json: str
    provenance_json: str
    session_id: None = None

    def __post_init__(self) -> None:
        if self.direction is not Direction.INBOUND_TRIGGER or self.session_id is not None:
            raise ValueError("only sessionless inbound trigger can claim run")
        for name in ("tenant_id", "project_id", "scope_id", "parent_proof_id", "snapshot_id", "m04_gate_id",
                     "m04e_gate_id", "mapping_id", "automation_id", "actor_id", "definition_evidence_id",
                     "task_name", "command", "event_type", "idempotency_key", "agent_run_key", "correlation_id"):
            _required(getattr(self, name), name, 512)
        if self.source_claim_marker is not None:
            _required(self.source_claim_marker, "source claim marker", 256)
        if self.eligibility_evidence_id is not None:
            _required(self.eligibility_evidence_id, "eligibility evidence", 256)
        if self.legacy_fence_id is not None:
            _required(self.legacy_fence_id, "legacy fence", 256)
        if self.legacy_fence_generation is not None and self.legacy_fence_generation < 1:
            raise ValueError("legacy fence generation invalid")
        for name in ("m04_gate_digest", "m04e_gate_digest", "m04e_dependency_digest", "raw_payload_digest",
                     "payload_digest", "request_digest", "idempotency_key"):
            if not _valid_digest(getattr(self, name)):
                raise ValueError(f"{name} invalid")
        if self.agent_run_key != "automation-ingress:" + self.idempotency_key[7:]:
            raise ValueError("AgentRun key mismatch")
        json.loads(self.payload_json)
        json.loads(self.provenance_json)

    @property
    def provenance_digest(self) -> str:
        return _raw_digest(self.provenance_json.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class RunLink:
    task_run_id: str
    agent_run_id: str
    tenant_id: str
    project_id: str
    session_id: str | None
    agent_run_key: str
    request_digest: str
    provenance_digest: str
    correlation_id: str
    transaction_id: str
    audit_id: str
    audit_actor_id: str
    claim_evidence_id: str
    events: tuple[EventLink, ...]
    approvals: tuple[ApprovalLink, ...]
    committed_at: datetime

    def __post_init__(self) -> None:
        for name in ("task_run_id", "agent_run_id", "tenant_id", "project_id", "agent_run_key",
                     "correlation_id", "transaction_id", "audit_id", "audit_actor_id", "claim_evidence_id"):
            _required(getattr(self, name), name, 256)
        if self.task_run_id == self.agent_run_id or self.session_id is not None:
            raise ValueError("task/run must be distinct and sessionless")
        if not _valid_digest(self.request_digest) or not _valid_digest(self.provenance_digest):
            raise ValueError("run digests invalid")
        if self.committed_at.tzinfo is None or self.committed_at.utcoffset() is None:
            raise ValueError("commit time must be aware")
        sequences = [event.sequence for event in self.events]
        if not sequences or sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("event order must be contiguous")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: ClaimStatus
    link: RunLink | None = None
    reason: str | None = None
    blocker: str | None = None
    conflict_evidence_id: str | None = None

    def __post_init__(self) -> None:
        committed = self.status in {ClaimStatus.APPLIED, ClaimStatus.REPLAY}
        if committed != (self.link is not None):
            raise ValueError("claim/link status mismatch")
        if self.status is ClaimStatus.CONFLICT and not self.conflict_evidence_id:
            raise ValueError("conflict evidence ID required")
        if self.status in {ClaimStatus.BLOCKED, ClaimStatus.FAILED} and not (self.blocker or self.reason):
            raise ValueError("blocked result needs stable reason")


@dataclass(frozen=True, slots=True)
class Quarantine:
    key: str
    source: SourceIdentity
    subscription_id: str | None
    payload_digest: str
    reason: str
    tenant_id: str | None
    project_id: str | None
    mapping_id: str | None
    parent_proof_id: str | None
    source_evidence_id: str | None
    correlation_id: str

    @property
    def digest(self) -> str:
        return _json_digest({"key": self.key, "kind": self.source.kind.value, "source": self.source.source_id,
                             "event": self.source.event_id, "schema": self.source.schema_version,
                             "subscription": self.subscription_id, "payload": self.payload_digest,
                             "reason": self.reason, "tenant": self.tenant_id, "project": self.project_id,
                             "mapping": self.mapping_id, "parent": self.parent_proof_id,
                             "source_evidence": self.source_evidence_id, "correlation": self.correlation_id})


@dataclass(frozen=True, slots=True)
class QuarantineReceipt:
    id: str
    key: str
    digest: str
    audit_id: str


@dataclass(frozen=True, slots=True)
class Acceptance:
    status: IngressStatus
    task_run_id: str | None = None
    agent_run_id: str | None = None
    event_ids: tuple[str, ...] = ()
    approval_ids: tuple[str, ...] = ()
    evidence_id: str | None = None
    blocker: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        accepted = self.status in {IngressStatus.ACCEPTED, IngressStatus.DUPLICATE}
        if accepted and (not self.task_run_id or not self.agent_run_id or not self.event_ids):
            raise ValueError("positive result needs run/event links")
        if not accepted and any((self.task_run_id, self.agent_run_id, self.event_ids, self.approval_ids)):
            raise ValueError("negative result cannot expose run IDs")


class EndpointResolverPort(Protocol):
    async def resolve(self, *, identity: SubscriptionIdentity, route: str) -> EndpointResolution: ...

class TriggerResolverPort(Protocol):
    async def resolve(self, *, trigger_id: str) -> TriggerResolution: ...

class TriggerFencePort(Protocol):
    async def verify(self, *, trigger: ProjectTrigger) -> bool: ...

class ScopeResolverPort(Protocol):
    async def resolve(self, *, assertion: ProjectAssertion) -> ScopeProof | None: ...

class DefinitionResolverPort(Protocol):
    async def resolve(self, *, tenant_id: str, project_id: str, identity: DefinitionIdentity,
                      snapshot_id: str) -> DefinitionProof | None: ...

class GateVerifierPort(Protocol):
    async def verify_m04(self, *, scope: ScopeProof) -> GateEvidence | None: ...
    async def verify_m04e(self, *, scope: ScopeProof, definition: DefinitionProof) -> GateEvidence | None: ...

class SecretResolverPort(Protocol):
    async def resolve(self, *, secret_ref: str, tenant_id: str, project_id: str,
                      endpoint_id: str) -> SigningKey | None: ...

class SignatureVerifierPort(Protocol):
    async def verify(self, *, request: WebhookRequest, mapping: EndpointMapping) -> SignatureProof: ...

class TransactionPort(Protocol):
    async def claim_or_resume(self, *, claim: IngressClaim) -> ClaimResult:
        """Atomically persist claim, durable task intent, task run, AgentRun,
        ordered event/outbox, applicable approval, provenance and audit. Exact
        identity/hash replay returns original linkage; same identity with a changed
        digest conflicts and is audited.
        """
        ...
    async def resume_trigger(self, *, claim: IngressClaim, source_claim_marker: str) -> ClaimResult:
        """Lookup-only resume: verify the source marker and return original linked run.

        This operation MUST NOT create a task, run, event or approval. Marker lookup,
        partition validation, request-hash validation and durable evidence are one
        trusted transaction. The marker is not tenant/project/run ownership authority.
        """
        ...
    async def quarantine(self, *, evidence: Quarantine) -> QuarantineReceipt: ...


class AutomationIngress:
    def __init__(self, *, endpoints: EndpointResolverPort | None, triggers: TriggerResolverPort | None,
                 trigger_fence: TriggerFencePort | None, scopes: ScopeResolverPort | None,
                 definitions: DefinitionResolverPort | None, gates: GateVerifierPort | None,
                 signatures: SignatureVerifierPort | None, transaction: TransactionPort | None,
                 limits: PayloadLimits = PayloadLimits()) -> None:
        self._endpoints, self._triggers, self._trigger_fence = endpoints, triggers, trigger_fence
        self._scopes, self._definitions, self._gates = scopes, definitions, gates
        self._signatures, self._transaction, self._limits = signatures, transaction, limits

    async def accept(self, request: IngressRequest | OutboundDeliveryRequest) -> Acceptance:
        if isinstance(request, OutboundDeliveryRequest):
            return Acceptance(IngressStatus.REJECTED, reason="outbound_delivery_is_not_ingress")
        if isinstance(request, WebhookRequest):
            return await self._webhook(request)
        return await self._trigger(request)

    async def _webhook(self, request: WebhookRequest) -> Acceptance:
        raw_hash = _raw_digest(request.body)
        unresolved = SourceIdentity(SourceKind.WEBHOOK, request.source_id, request.event_id_assertion, "unresolved")
        if len(request.body) > self._limits.max_bytes:
            return await self._quarantine(unresolved, request.subscription_id, raw_hash, "payload_byte_limit_exceeded")
        if self._endpoints is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="subscription_resolver_unwired")
        subscription = SubscriptionIdentity(request.source_id, request.subscription_id)
        try:
            resolution = await self._endpoints.resolve(identity=subscription, route=request.route)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="subscription_resolution_unavailable")
        if resolution.status is not ResolutionStatus.RESOLVED or resolution.mapping is None:
            return await self._quarantine(unresolved, request.subscription_id, raw_hash,
                                          "subscription_" + resolution.status.value)
        mapping = resolution.mapping
        if (mapping.subscription != subscription or request.route not in mapping.routes
                or not mapping.enabled or not mapping.inbound_enabled):
            return await self._quarantine(unresolved, request.subscription_id, raw_hash,
                                          "subscription_mapping_or_policy_mismatch", mapping=mapping)
        if self._signatures is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="signature_verifier_unwired")
        try:
            signature = await self._signatures.verify(request=request, mapping=mapping)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="signature_verification_unavailable")
        if (not signature.valid or not signature.key_version_id or signature.body_digest != raw_hash
                or signature.secret_ref != mapping.secret_ref
                or signature.tenant_id != mapping.owner.tenant_id or signature.project_id != mapping.owner.project_id
                or signature.endpoint_id != mapping.endpoint_id):
            return await self._quarantine(unresolved, request.subscription_id, raw_hash,
                                          "signature_or_secret_scope_invalid", mapping=mapping)
        try:
            payload, event_id, event_type = _parse_webhook(request.body, self._limits)
        except ValueError as exc:
            return await self._quarantine(unresolved, request.subscription_id, raw_hash, str(exc), mapping=mapping)
        if event_id != request.event_id_assertion:
            return await self._quarantine(unresolved, request.subscription_id, raw_hash,
                                          "signed_event_id_mismatch", mapping=mapping)
        if event_type not in mapping.event_types:
            return await self._quarantine(unresolved, request.subscription_id, raw_hash,
                                          "unsupported_subscription_event_type", mapping=mapping)
        source = SourceIdentity(SourceKind.WEBHOOK, request.source_id, event_id, mapping.schema_version)
        return await self._ingest(source, subscription, mapping.owner, mapping.mapping_id,
                                  mapping.endpoint_id, mapping.automation_id, mapping.actor_id,
                                  mapping.definition, event_type, payload, raw_hash,
                                  resolution.evidence_id, signature.key_version_id, None)

    async def _trigger(self, request: ProjectTriggerRequest) -> Acceptance:
        unresolved = SourceIdentity(SourceKind.PROJECT_TRIGGER, "project_triggers", request.trigger_id, "unresolved")
        if self._triggers is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="project_trigger_resolver_unwired")
        try:
            resolved = await self._triggers.resolve(trigger_id=request.trigger_id)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="project_trigger_resolution_unavailable")
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.trigger is None:
            return await self._quarantine(unresolved, None, _json_digest({"trigger_id": request.trigger_id}),
                                          "project_trigger_" + resolved.status.value)
        trigger = resolved.trigger
        source = SourceIdentity(SourceKind.PROJECT_TRIGGER, "project_triggers", trigger.trigger_id,
                                trigger.schema_version)
        try:
            payload = _bounded(trigger.payload, self._limits)
        except ValueError as exc:
            return await self._quarantine(source, None, _json_digest({"trigger_id": request.trigger_id}), str(exc),
                                          source_evidence=trigger.source_evidence_id)
        raw_hash = _json_digest(payload)
        if trigger.trigger_id != request.trigger_id or raw_hash != trigger.payload_digest:
            return await self._quarantine(source, None, raw_hash, "trigger_identity_or_digest_mismatch",
                                          source_evidence=trigger.source_evidence_id)
        if not trigger.eligible or trigger.state not in {"queued", "claimed"}:
            return await self._quarantine(source, None, raw_hash, "trigger_not_eligible",
                                          source_evidence=trigger.source_evidence_id, blocked=True)
        if self._trigger_fence is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="trigger_fence_unwired")
        try:
            verified = await self._trigger_fence.verify(trigger=trigger)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="trigger_fence_unavailable")
        if not verified or not trigger.legacy_fence_id or not trigger.legacy_fence_generation:
            return await self._quarantine(source, None, raw_hash, "trigger_writer_fence_unverified",
                                          source_evidence=trigger.source_evidence_id, blocked=True)
        return await self._ingest(source, None, ProjectAssertion(trigger.project_id), trigger.mapping_id,
                                  None, trigger.automation_id, trigger.actor_id, trigger.definition,
                                  trigger.event_type, payload, raw_hash, trigger.source_evidence_id,
                                  None, trigger.target_claim_evidence_id,
                                  trigger.eligibility_evidence_id, trigger.legacy_fence_id,
                                  trigger.legacy_fence_generation)

    async def _ingest(self, source: SourceIdentity, subscription: SubscriptionIdentity | None,
                      assertion: ProjectAssertion, mapping_id: str, endpoint_id: str | None,
                      automation_id: str, actor_id: str, identity: DefinitionIdentity,
                      event_type: str, payload: dict[str, Any], raw_hash: str,
                      source_evidence: str | None, key_version: str | None,
                      source_claim_marker: str | None, eligibility_evidence_id: str | None = None,
                      legacy_fence_id: str | None = None,
                      legacy_fence_generation: int | None = None) -> Acceptance:
        if self._scopes is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04_scope_resolver_unwired")
        try:
            scope = await self._scopes.resolve(assertion=assertion)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04_scope_unavailable")
        if (scope is None or not scope.active or not scope.parent_compatible
                or scope.project_id != assertion.project_id
                or (assertion.tenant_id is not None and scope.tenant_id != assertion.tenant_id)):
            return await self._quarantine(source, subscription.subscription_id if subscription else None,
                                          raw_hash, "m04_scope_or_parent_unresolved", mapping_id=mapping_id, blocked=True)
        if self._gates is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="promotion_gate_verifier_unwired")
        try:
            m04 = await self._gates.verify_m04(scope=scope)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04_gate_unavailable")
        expected_partition = _json_digest({"tenant_id": scope.tenant_id, "project_id": scope.project_id})
        if (m04 is None or not m04.verified or m04.kind != "M04" or m04.record_id != scope.gate_record_id
                or m04.partition_digest != expected_partition
                or scope.scope_id not in m04.dependencies or scope.parent_proof_id not in m04.dependencies):
            return await self._quarantine(source, subscription.subscription_id if subscription else None,
                                          raw_hash, "m04_gate_not_proven", mapping_id=mapping_id,
                                          tenant_id=scope.tenant_id, project_id=scope.project_id,
                                          parent_proof_id=scope.parent_proof_id, blocked=True)
        if self._definitions is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04e_definition_resolver_unwired")
        try:
            definition = await self._definitions.resolve(tenant_id=scope.tenant_id, project_id=scope.project_id,
                                                        identity=identity, snapshot_id=scope.snapshot_id)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04e_definition_unavailable")
        if (definition is None or not definition.active or definition.tenant_id != scope.tenant_id
                or definition.project_id != scope.project_id or definition.automation_id != automation_id
                or definition.actor_id != actor_id or definition.identity != identity
                or definition.snapshot_id != scope.snapshot_id or event_type not in definition.events
                or definition.command != "agent_run.create"):
            return await self._quarantine(source, subscription.subscription_id if subscription else None,
                                          raw_hash, "m04e_definition_or_actor_unresolved", mapping_id=mapping_id,
                                          tenant_id=scope.tenant_id, project_id=scope.project_id,
                                          parent_proof_id=scope.parent_proof_id, blocked=True)
        try:
            m04e = await self._gates.verify_m04e(scope=scope, definition=definition)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="m04e_gate_unavailable")
        if (m04e is None or not m04e.verified or m04e.kind != "M04.e"
                or m04e.record_id != definition.gate_record_id or definition.evidence_id not in m04e.dependencies
                or m04.digest not in m04e.dependencies or m04e.partition_digest != expected_partition):
            return await self._quarantine(source, subscription.subscription_id if subscription else None,
                                          raw_hash, "m04e_gate_not_proven", mapping_id=mapping_id,
                                          tenant_id=scope.tenant_id, project_id=scope.project_id,
                                          parent_proof_id=scope.parent_proof_id, blocked=True)
        if source_claim_marker is not None and source.kind is not SourceKind.PROJECT_TRIGGER:
            return Acceptance(IngressStatus.BLOCKED, blocker="source_claim_marker_wrong_kind")
        if self._transaction is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="atomic_ingress_transaction_unwired")
        claim = self._build_claim(source, subscription, scope, definition, m04, m04e, mapping_id,
                                  endpoint_id, event_type, payload, raw_hash, source_evidence,
                                  key_version, source_claim_marker, eligibility_evidence_id,
                                  legacy_fence_id, legacy_fence_generation)
        try:
            result = (await self._transaction.resume_trigger(claim=claim, source_claim_marker=source_claim_marker)
                      if source_claim_marker is not None else await self._transaction.claim_or_resume(claim=claim))
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="atomic_claim_outcome_unknown")
        return self._accept_result(result, claim)

    @staticmethod
    def _build_claim(source: SourceIdentity, subscription: SubscriptionIdentity | None, scope: ScopeProof,
                     definition: DefinitionProof, m04: GateEvidence, m04e: GateEvidence,
                     mapping_id: str, endpoint_id: str | None, event_type: str,
                     payload: dict[str, Any], raw_hash: str, source_evidence: str | None,
                     key_version: str | None, source_claim_marker: str | None,
                     eligibility_evidence_id: str | None, legacy_fence_id: str | None,
                     legacy_fence_generation: int | None) -> IngressClaim:
        payload_obj = {"schema": "automation.task-input.v1", "event_type": event_type,
                       "source": {"kind": source.kind.value, "id": source.source_id,
                                  "event_id": source.event_id, "schema_version": source.schema_version},
                       "data": payload}
        payload_json = _canonical(payload_obj)
        payload_digest = _raw_digest(payload_json.encode("utf-8"))
        stable_identity = {"schema": "automation.ingress.identity.v1", "tenant_id": scope.tenant_id,
                           "project_id": scope.project_id, "source_kind": source.kind.value,
                           "source_id": source.source_id, "source_event_id": source.event_id,
                           "subscription_id": subscription.subscription_id if subscription else None}
        idempotency_key = _json_digest(stable_identity)
        agent_run_key = "automation-ingress:" + idempotency_key[7:]
        request_digest = _json_digest({"schema": "automation.ingress.request.v1", **stable_identity,
                                       "schema_version": source.schema_version, "mapping_id": mapping_id,
                                       "endpoint_id": endpoint_id, "automation_id": definition.automation_id,
                                       "actor_id": definition.actor_id,
                                       "definition_id": definition.identity.definition_id,
                                       "version_id": definition.identity.version_id,
                                       "artifact_digest": definition.identity.artifact_digest,
                                       "definition_evidence_id": definition.evidence_id,
                                       "source_evidence_id": source_evidence,
                                       "snapshot_id": definition.snapshot_id,
                                       "m04_gate_record_id": m04.record_id, "m04_gate_digest": m04.digest,
                                       "m04e_gate_record_id": m04e.record_id, "m04e_gate_digest": m04e.digest,
                                       "task_name": definition.task_name,
                                       "command": definition.command, "event_type": event_type,
                                       "raw_payload_digest": raw_hash, "payload_digest": payload_digest})
        provenance = {"schema": "automation.ingress.provenance.v1",
                      "direction": Direction.INBOUND_TRIGGER.value,
                      "source_kind": source.kind.value, "source_id": source.source_id,
                      "source_event_id": source.event_id, "source_schema_version": source.schema_version,
                      "source_evidence_id": source_evidence,
                      "eligibility_evidence_id": eligibility_evidence_id,
                      "legacy_fence_id": legacy_fence_id,
                      "legacy_fence_generation": legacy_fence_generation,
                      "subscription_id": subscription.subscription_id if subscription else None,
                      "mapping_id": mapping_id, "endpoint_id": endpoint_id,
                      "tenant_id": scope.tenant_id, "project_id": scope.project_id,
                      "scope_id": scope.scope_id, "parent_proof_id": scope.parent_proof_id,
                      "snapshot_id": scope.snapshot_id, "m04_gate_id": m04.record_id,
                      "m04_gate_digest": m04.digest, "m04e_gate_id": m04e.record_id,
                      "m04e_gate_digest": m04e.digest, "automation_id": definition.automation_id,
                      "actor_id": definition.actor_id, "definition_id": definition.identity.definition_id,
                      "version_id": definition.identity.version_id, "artifact_digest": definition.identity.artifact_digest,
                      "definition_evidence_id": definition.evidence_id, "task_name": definition.task_name,
                      "command": definition.command, "event_type": event_type,
                      "raw_payload_digest": raw_hash, "payload_digest": payload_digest,
                      "request_digest": request_digest, "idempotency_key": idempotency_key,
                      "agent_run_key": agent_run_key, "correlation_id": agent_run_key, "session_id": None,
                      "signature_mode": "hmac-sha256-raw-body" if source.kind is SourceKind.WEBHOOK
                      else "trusted-project-trigger", "signature_key_version": key_version}
        return IngressClaim(
            direction=Direction.INBOUND_TRIGGER, source=source,
            subscription_id=subscription.subscription_id if subscription else None,
            tenant_id=scope.tenant_id, project_id=scope.project_id, scope_id=scope.scope_id,
            parent_proof_id=scope.parent_proof_id, snapshot_id=scope.snapshot_id,
            m04_gate_id=m04.record_id, m04_gate_digest=m04.digest,
            m04e_gate_id=m04e.record_id, m04e_gate_digest=m04e.digest,
            m04e_dependency_digest=_json_digest(sorted(m04e.dependencies)), mapping_id=mapping_id, endpoint_id=endpoint_id,
            automation_id=definition.automation_id, actor_id=definition.actor_id,
            definition=definition.identity, definition_evidence_id=definition.evidence_id,
            source_evidence_id=source_evidence, signature_key_version_id=key_version,
            source_claim_marker=source_claim_marker, eligibility_evidence_id=eligibility_evidence_id,
            legacy_fence_id=legacy_fence_id, legacy_fence_generation=legacy_fence_generation,
            task_name=definition.task_name,
            command=definition.command, event_type=event_type, raw_payload_digest=raw_hash,
            payload_digest=payload_digest, request_digest=request_digest, idempotency_key=idempotency_key,
            agent_run_key=agent_run_key, correlation_id=agent_run_key, payload_json=payload_json,
            provenance_json=_canonical(provenance), session_id=None)

    @staticmethod
    def _accept_result(result: ClaimResult, claim: IngressClaim) -> Acceptance:
        if result.status in {ClaimStatus.APPLIED, ClaimStatus.REPLAY}:
            link = result.link
            if link is None:
                raise RuntimeError("atomic port omitted run linkage")
            valid = (link.tenant_id == claim.tenant_id and link.project_id == claim.project_id
                     and link.session_id is None and link.agent_run_key == claim.agent_run_key
                     and link.request_digest == claim.request_digest
                     and (result.status is ClaimStatus.REPLAY or link.provenance_digest == claim.provenance_digest)
                     and link.correlation_id == claim.correlation_id
                     and link.audit_actor_id == claim.actor_id
                     and (claim.source_claim_marker is None or link.claim_evidence_id == claim.source_claim_marker)
                     and all(e.run_id == link.agent_run_id and e.tenant_id == claim.tenant_id
                             and e.project_id == claim.project_id and e.source_event_id == claim.source.event_id
                             and e.correlation_id == claim.correlation_id and e.request_digest == claim.request_digest
                             for e in link.events)
                     and all(a.run_id == link.agent_run_id and a.tenant_id == claim.tenant_id
                             and a.project_id == claim.project_id for a in link.approvals))
            if not valid:
                raise RuntimeError("transaction returned mismatched run/event/audit linkage")
            return Acceptance(IngressStatus.ACCEPTED if result.status is ClaimStatus.APPLIED else IngressStatus.DUPLICATE,
                              link.task_run_id, link.agent_run_id, tuple(e.event_id for e in link.events),
                              tuple(a.approval_id for a in link.approvals))
        if result.status is ClaimStatus.CONFLICT:
            return Acceptance(IngressStatus.CONFLICT, evidence_id=result.conflict_evidence_id,
                              reason=result.reason or "idempotency_hash_conflict")
        return Acceptance(IngressStatus.BLOCKED, evidence_id=result.conflict_evidence_id,
                          blocker=result.blocker or result.reason or "atomic_ingress_blocked")

    async def _quarantine(self, source: SourceIdentity, subscription_id: str | None,
                          payload_digest: str, reason: str, *, mapping: EndpointMapping | None = None,
                          mapping_id: str | None = None, tenant_id: str | None = None,
                          project_id: str | None = None, parent_proof_id: str | None = None,
                          source_evidence: str | None = None, blocked: bool = False) -> Acceptance:
        if self._transaction is None:
            return Acceptance(IngressStatus.BLOCKED, blocker="durable_quarantine_unwired")
        target_mapping_id = mapping.mapping_id if mapping is not None else mapping_id
        key = _json_digest({"schema": "automation.quarantine.key.v1", "source_kind": source.kind.value,
                            "source_id": source.source_id, "source_event_id": source.event_id,
                            "source_schema": source.schema_version, "subscription_id": subscription_id,
                            "payload_digest": payload_digest, "reason": reason, "mapping_id": target_mapping_id})
        evidence = Quarantine(key, source, subscription_id, payload_digest, reason, tenant_id, project_id,
                              target_mapping_id, parent_proof_id, source_evidence,
                              "automation-ingress:" + key[7:])
        try:
            receipt = await self._transaction.quarantine(evidence=evidence)
        except Exception:
            return Acceptance(IngressStatus.BLOCKED, blocker="durable_quarantine_outcome_unknown")
        if receipt.key != key or receipt.digest != evidence.digest:
            raise RuntimeError("quarantine adapter returned mismatched evidence")
        return Acceptance(IngressStatus.BLOCKED if blocked else IngressStatus.QUARANTINED,
                          evidence_id=receipt.id, blocker=reason if blocked else None,
                          reason=None if blocked else reason)


__all__ = [
    "Acceptance", "ApprovalLink", "AutomationIngress", "ClaimResult", "ClaimStatus", "DefinitionIdentity",
    "DefinitionProof", "DefinitionResolverPort", "Direction", "EndpointMapping", "EndpointResolution",
    "EndpointResolverPort", "EventLink", "GateEvidence", "GateVerifierPort", "HmacVerifier",
    "IngressClaim", "IngressRequest", "IngressStatus", "OutboundDeliveryRequest", "PayloadLimits",
    "ProjectAssertion", "ProjectTrigger", "ProjectTriggerRequest", "Quarantine", "QuarantineReceipt",
    "ResolutionStatus", "RunLink", "ScopeProof", "ScopeResolverPort", "SecretResolverPort",
    "SigningKey", "SignatureProof", "SignatureVerifierPort", "SourceIdentity", "SourceKind",
    "SubscriptionIdentity", "TransactionPort", "TriggerFencePort", "TriggerResolution",
    "TriggerResolverPort", "WebhookRequest",
]
