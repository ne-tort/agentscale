"""Decision-only composition of MCP method mapping and canonical grants."""

from __future__ import annotations

from datetime import datetime

from prodavan.application.canonical_grants import CanonicalGrantRequest
from prodavan.application.capability_method_mapping import MappingDecision

from .contracts import (
    CanonicalGrantsMethodDecision,
    CanonicalGrantsMethodRequest,
)
from .ports import CanonicalGrantsDecisionPort, MethodCapabilityMappingDecisionPort


class CanonicalGrantsMethodAdapter:
    """Quarantine mapping failures before delegating an exact grant decision."""

    def __init__(
        self,
        *,
        mapping: MethodCapabilityMappingDecisionPort,
        grants: CanonicalGrantsDecisionPort,
    ) -> None:
        self._mapping = mapping
        self._grants = grants

    async def authorize(
        self,
        request: CanonicalGrantsMethodRequest,
        *,
        now: datetime | None = None,
    ) -> CanonicalGrantsMethodDecision:
        mapping = await self._mapping.decide(
            identity=request.identity,
            capability_key=request.capability_key,
            operation=request.operation,
            resource=request.resource,
            domain=request.domain,
            protocol_version=request.protocol_version,
            tool_version=request.tool_version,
            audit=request.mcp.mapping_audit(),
        )
        if mapping.decision is not MappingDecision.ALLOW or mapping.mapping is None:
            return CanonicalGrantsMethodDecision(
                request=request,
                mapping=mapping,
                grant=None,
                requested_capability_key=request.capability_key,
                requested_operation=request.operation,
                requested_resource=request.resource,
                requested_domain=request.domain,
                requested_constraints=request.constraints,
            )

        declaration = mapping.mapping
        grant_request = CanonicalGrantRequest(
            company_id=request.company_id,
            app_id=request.app_id,
            manifest_version=request.manifest_version,
            plugin_instance_id=request.plugin_instance_id,
            capability_key=declaration.capability_key,
            operation=declaration.operation,
            resource=request.resource,
            domain=request.domain,
            constraints=request.constraints,
            fencing=request.fencing,
            correlation_id=request.mcp.correlation_id,
        )
        grant = await self._grants.authorize(grant_request, now=now)
        return CanonicalGrantsMethodDecision(
            request=request,
            mapping=mapping,
            grant=grant,
            requested_capability_key=request.capability_key,
            requested_operation=request.operation,
            requested_resource=request.resource,
            requested_domain=request.domain,
            requested_constraints=request.constraints,
            granted_capability_key=declaration.capability_key,
            granted_operation=declaration.operation,
            granted_resources=declaration.constraints.resources,
            granted_domains=declaration.constraints.domains,
            granted_constraints=request.constraints,
        )


__all__ = ["CanonicalGrantsMethodAdapter"]
