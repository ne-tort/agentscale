"""MIG-AUTO-01 fail-closed automation ingress and agent-run linkage contract.

This module defines application semantics only. A production adapter must make
claim, task intent, agent run, ordered events and approval linkage atomic in a
durable control-plane transaction; no in-memory or queue-only fallback exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _required(value: str, name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EndpointMapping:
    mapping_id: str
    tenant_id: str
    project_id: str
    source: str
    endpoint_id: str
    target_id: str
    task_name: str
    secret_ref: str
    compatibility_routes: tuple[str, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("mapping_id", "tenant_id", "project_id", "source", "endpoint_id", "target_id", "task_name", "secret_ref"):
            _required(getattr(self, name), name)
        if not self.compatibility_routes or any(not route.startswith("/") for route in self.compatibility_routes):
            raise ValueError("compatibility routes must be non-empty absolute paths")
        if len(set(self.compatibility_routes)) != len(self.compatibility_routes):
            raise ValueError("compatibility routes must be unique")


@dataclass(frozen=True, slots=True)
class InboundDelivery:
    source: str
    route: str
    event_id: str
    body: bytes
    headers: Mapping[str, str]
    received_at: datetime

    def __post_init__(self) -> None:
        for name in ("source", "route", "event_id"):
            _required(getattr(self, name), name)
        _aware(self.received_at, "received_at")

    @property
    def payload_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedIngress:
    mapping: EndpointMapping
    delivery: InboundDelivery
    request_digest: str
    idempotency_key: str

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "source": self.delivery.source,
            "source_event_id": self.delivery.event_id,
            "endpoint_id": self.mapping.endpoint_id,
            "target_id": self.mapping.target_id,
            "mapping_id": self.mapping.mapping_id,
            "payload_digest": self.delivery.payload_digest,
            "request_digest": self.request_digest,
        }


class IngressStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class IngressClaim:
    tenant_id: str
    project_id: str
    idempotency_key: str
    request_digest: str
    mapping_id: str
    endpoint_id: str
    target_id: str
    source: str
    source_event_id: str
    task_name: str
    task_payload: dict[str, Any]
    provenance: dict[str, str]
    agent_run_idempotency_key: str
    compatibility_route: str


@dataclass(frozen=True, slots=True)
class IngressAcceptance:
    status: IngressStatus
    task_run_id: str | None = None
    agent_run_id: str | None = None
    ordered_event_ids: tuple[str, ...] = ()
    approval_ids: tuple[str, ...] = ()
    blocker: str | None = None


class DurableIngressControlPlanePort(Protocol):
    async def claim_and_create_intent(self, *, claim: IngressClaim) -> IngressAcceptance:
        """Atomically persist unique claim, task intent, agent_run and ordered event(s).

        Return the original linked identifiers on an identical replay; reject a
        digest mismatch for the same scoped idempotency identity. Approval IDs
        may be empty until a run requests approval, but linkage must be durable.
        The implementation MUST use transactional durable persistence, not a
        process-local cache, broker-only dedupe, or read-then-write sequence.
        """
        ...


class InboundVerifierPort(Protocol):
    def verify(self, *, body: bytes, headers: Mapping[str, str], secret_ref: str) -> bool: ...


class AutomationIngressService:
    """Resolve an explicit endpoint mapping, verify, then request durable claim."""

    def __init__(self, *, control_plane: DurableIngressControlPlanePort, verifier: InboundVerifierPort) -> None:
        self._control_plane = control_plane
        self._verifier = verifier

    async def accept(
        self,
        *,
        mappings: tuple[EndpointMapping, ...],
        delivery: InboundDelivery,
        payload: dict[str, Any],
    ) -> IngressAcceptance:
        candidates = [
            mapping for mapping in mappings
            if mapping.enabled and mapping.source == delivery.source and delivery.route in mapping.compatibility_routes
        ]
        if len(candidates) != 1:
            return IngressAcceptance(IngressStatus.REJECTED, blocker="endpoint_mapping_missing_or_ambiguous")
        mapping = candidates[0]
        if not self._verifier.verify(body=delivery.body, headers=delivery.headers, secret_ref=mapping.secret_ref):
            return IngressAcceptance(IngressStatus.REJECTED, blocker="inbound_verification_failed")

        request_digest = _digest({"body": delivery.payload_digest, "payload": payload})
        raw_key = "\x00".join((mapping.tenant_id, mapping.project_id, mapping.endpoint_id, delivery.source, delivery.event_id))
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        claim = IngressClaim(
            tenant_id=mapping.tenant_id,
            project_id=mapping.project_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            mapping_id=mapping.mapping_id,
            endpoint_id=mapping.endpoint_id,
            target_id=mapping.target_id,
            source=delivery.source,
            source_event_id=delivery.event_id,
            task_name=mapping.task_name,
            task_payload=payload,
            provenance={
                "source": delivery.source,
                "source_event_id": delivery.event_id,
                "endpoint_id": mapping.endpoint_id,
                "target_id": mapping.target_id,
                "mapping_id": mapping.mapping_id,
                "payload_digest": delivery.payload_digest,
                "request_digest": request_digest,
            },
            agent_run_idempotency_key=f"automation-ingress:{idempotency_key}",
            compatibility_route=delivery.route,
        )
        return await self._control_plane.claim_and_create_intent(claim=claim)


__all__ = [
    "AutomationIngressService", "DurableIngressControlPlanePort", "EndpointMapping",
    "InboundDelivery", "IngressAcceptance", "IngressClaim", "IngressStatus",
    "InboundVerifierPort", "VerifiedIngress",
]
