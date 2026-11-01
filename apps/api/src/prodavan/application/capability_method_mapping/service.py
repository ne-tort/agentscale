"""Decision-only service for versioned capability method mappings."""

from __future__ import annotations

from .contracts import (
    CapabilityMethodMapping,
    MappingAuditMetadata,
    MappingDecision,
    MappingReason,
    MappingResult,
    MethodIdentity,
)
from .ports import CapabilityMethodMappingReadPort, ProtocolCompatibilityReadPort


class CapabilityMethodMappingService:
    """Resolve one exact mapping and fail closed on unknown or broadening requests."""

    def __init__(
        self,
        *,
        mappings: CapabilityMethodMappingReadPort,
        protocol: ProtocolCompatibilityReadPort | None = None,
    ) -> None:
        self._mappings = mappings
        self._protocol = protocol

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
        audit: MappingAuditMetadata,
    ) -> MappingResult:
        """Return a bounded decision; this method has no grant or persistence side effect."""
        if (
            not isinstance(identity, MethodIdentity)
            or not all(isinstance(value, str) and value.strip() for value in (
                capability_key, operation, resource, domain, protocol_version, tool_version,
            ))
        ):
            return self._quarantine(MappingReason.INVALID_REQUEST, audit)
        mappings = await self._mappings.list_mappings(capability_key=capability_key)
        matches = tuple(
            item for item in mappings
            if item.identity == identity
            and item.capability_key == capability_key
            and item.operation == operation
        )
        if len(matches) > 1:
            return self._quarantine(MappingReason.BROADENING_MAPPING, audit)
        mapping = await self._mappings.find_mapping(
            identity=identity, capability_key=capability_key, operation=operation
        )
        if mapping is None:
            return self._quarantine(MappingReason.UNKNOWN_MAPPING, audit)
        if not matches or matches[0] != mapping:
            return self._quarantine(MappingReason.BROADENING_MAPPING, audit, mapping)
        if mapping.identity != identity or mapping.capability_key != capability_key or mapping.operation != operation:
            return self._quarantine(MappingReason.BROADENING_MAPPING, audit, mapping)
        if not mapping.constraints.contains(resource=resource, domain=domain):
            return self._quarantine(MappingReason.BROADENING_MAPPING, audit, mapping)
        if not mapping.supports_tool_version(tool_version):
            return self._quarantine(MappingReason.INCOMPATIBLE_TOOL_VERSION, audit, mapping)
        if self._protocol is not None:
            compatible = await self._protocol.supports_protocol(
                protocol_version=protocol_version,
                required_version=mapping.supported_protocol_version,
            )
        else:
            compatible = protocol_version == mapping.supported_protocol_version
        if not compatible:
            return self._quarantine(MappingReason.UNSUPPORTED_PROTOCOL, audit, mapping)
        return MappingResult(
            decision=MappingDecision.ALLOW,
            reason=None,
            mapping=mapping,
            mapping_digest=mapping.mapping_digest,
            audit=audit,
        )

    @staticmethod
    def _quarantine(
        reason: MappingReason,
        audit: MappingAuditMetadata,
        mapping: CapabilityMethodMapping | None = None,
    ) -> MappingResult:
        return MappingResult(
            decision=MappingDecision.QUARANTINE,
            reason=reason,
            mapping=mapping,
            mapping_digest=mapping.mapping_digest if mapping is not None else None,
            audit=audit,
            quarantine=True,
        )


__all__ = ["CapabilityMethodMappingService"]
