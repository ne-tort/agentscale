"""Ports for the decision-only MCP grants adapter."""

from __future__ import annotations

from typing import Protocol

from prodavan.application.canonical_grants import CanonicalGrantDecisionResult, CanonicalGrantRequest
from prodavan.application.capability_method_mapping import MappingResult, MethodIdentity


class MethodCapabilityMappingDecisionPort(Protocol):
    async def decide(
        self,
        *,
        identity: MethodIdentity,
        capability_key: str,
        operation: str,
        resource: str | None,
        domain: str | None,
        protocol_version: str,
        tool_version: str,
        audit,
    ) -> MappingResult: ...


class CanonicalGrantsDecisionPort(Protocol):
    async def authorize(
        self, request: CanonicalGrantRequest, *, now=None
    ) -> CanonicalGrantDecisionResult: ...


__all__ = ["CanonicalGrantsDecisionPort", "MethodCapabilityMappingDecisionPort"]
