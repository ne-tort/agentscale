"""Ports for resolving, propagating, and durably classifying identity envelopes."""

from __future__ import annotations

from typing import Mapping, Protocol

from .contracts import IdentityContext, IdentityEnvelope, QuarantineEvidence


class IdentityEnvelopeResolverPort(Protocol):
    """Resolve one server-authenticated, complete identity snapshot."""

    async def resolve(self, *, context: IdentityContext) -> IdentityEnvelope: ...


class IdentityPropagationPort(Protocol):
    """Carry the same digest-linked context across an adapter boundary."""

    def inject(self, context: IdentityContext) -> Mapping[str, str]: ...

    def extract(self, headers: Mapping[str, str], *, hop: str) -> IdentityContext: ...


class IdentityQuarantinePort(Protocol):
    """Persist redacted evidence; a quarantine result can never authorize."""

    async def append(self, evidence: QuarantineEvidence) -> None: ...

    async def count(self, *, tenant_id: str, partition_digest: str) -> int: ...


class IdentityContextPort(Protocol):
    """Set and clear transaction-local context from a resolved envelope."""

    async def set(self, envelope: IdentityEnvelope) -> None: ...

    async def clear(self) -> None: ...


__all__ = [
    "IdentityContextPort",
    "IdentityEnvelopeResolverPort",
    "IdentityPropagationPort",
    "IdentityQuarantinePort",
]
