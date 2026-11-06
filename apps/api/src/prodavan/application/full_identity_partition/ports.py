"""Ports for full identity partition resolution."""

from __future__ import annotations

from typing import Mapping, Protocol

from prodavan.application.shared_identity import IdentityEnvelope, PromotionPartition

from .contracts import FullIdentityPartitionDecision


class FullIdentityPartitionResolverPort(Protocol):
    """Resolve and verify a complete envelope-bound promotion digest."""

    def resolve(
        self,
        *,
        envelope: IdentityEnvelope,
        partition: PromotionPartition,
        supplied_digest: str | None = None,
    ) -> FullIdentityPartitionDecision: ...


class FullIdentityPartitionInputPort(Protocol):
    """Optional boundary for callers supplying an already validated envelope."""

    def resolve_mapping(
        self,
        *,
        envelope: IdentityEnvelope,
        partition: Mapping[str, object],
        supplied_digest: str | None = None,
    ) -> FullIdentityPartitionDecision: ...


__all__ = ["FullIdentityPartitionInputPort", "FullIdentityPartitionResolverPort"]
