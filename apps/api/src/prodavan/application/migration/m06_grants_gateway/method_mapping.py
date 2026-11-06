"""Approved method -> operation lookup for M06 mapping and shadow evidence.

The registry is the existing decision-only `capability_method_mapping` zone; the
`module_capabilities` grammar supplies the closed set of declarative capability
keys and the operations they can express. This module joins both without adding
persistence, an executor, or any authorization side effect: a lookup result is
evidence only, and the legacy gateway stays the decision owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from prodavan.application.capability_method_mapping.contracts import (
    CapabilityMethodMapping,
    MappingAuditMetadata,
    MappingDecision,
    MethodIdentity,
)
from prodavan.application.capability_method_mapping.ports import (
    CapabilityMethodMappingReadPort,
    ProtocolCompatibilityReadPort,
)
from prodavan.application.capability_method_mapping.service import CapabilityMethodMappingService

from .adapter import (
    APPROVED_CAPABILITIES,
    CAPABILITY_OPERATIONS,
    METHOD_MAPPING_VERSION,
    MethodOperationPort,
    MethodOperationResolution,
    ResolvedGrantIdentity,
    digest,
)

MODULE_CAPABILITY_ACTIONS = MappingProxyType({
    "data.create_row": frozenset({"create"}),
    "data.select_row": frozenset({"read"}),
    "data.delete_row": frozenset({"delete"}),
    "documents.convert": frozenset({"convert"}),
    "documents.fill_template": frozenset({"render"}),
})
"""Declarative module action kinds, keyed to the canonical operation they express.

This table is *intent*, not approval: it names the operation a module action kind
means, and :func:`module_capability_operations` is the fail-closed gate that keeps
only the entries whose operation exists in the closed approved vocabulary. Some
kinds therefore stay unmapped on purpose — ``create``, ``convert`` have no canonical
operation (the approved grammar expresses row creation as ``write`` and only allows
``render`` for ``ui.render``), so those actions quarantine instead of being silently
translated onto a neighbouring operation.
"""

APPROVED_OPERATION_KEYS = frozenset(
    operation for operations in CAPABILITY_OPERATIONS.values() for operation in operations
)


@dataclass(frozen=True, slots=True)
class ApprovedMethodMapping:
    """Immutable, reviewable declaration of one approved method -> operation mapping."""

    method: str
    capability_key: str
    operation: str
    resources: tuple[str, ...]
    domains: tuple[str, ...]
    protocol_version: str
    tool_version: str
    server: str

    def __post_init__(self) -> None:
        for name, value in (
            ("method", self.method), ("capability_key", self.capability_key),
            ("operation", self.operation), ("protocol_version", self.protocol_version),
            ("tool_version", self.tool_version), ("server", self.server),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"approved method mapping requires {name}")
        if self.capability_key not in APPROVED_CAPABILITIES:
            raise ValueError("approved method mapping must target an approved capability")
        if self.operation not in CAPABILITY_OPERATIONS[self.capability_key]:
            raise ValueError("approved method mapping must target an approved operation")
        for name, values in (("resources", self.resources), ("domains", self.domains)):
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"approved method mapping requires bounded {name}")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"approved method mapping {name} must be non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"approved method mapping {name} must be unique")
            if any(_broad(item) for item in values):
                raise ValueError(f"approved method mapping {name} cannot contain wildcards")

    @property
    def mapping_digest(self) -> str:
        return digest({
            "mapping_version": METHOD_MAPPING_VERSION,
            "method": self.method, "server": self.server, "tool_version": self.tool_version,
            "capability_key": self.capability_key, "operation": self.operation,
            "resources": list(self.resources), "domains": list(self.domains),
            "protocol_version": self.protocol_version,
        })


def declared_method_mapping(mapping: CapabilityMethodMapping) -> ApprovedMethodMapping:
    """Project a versioned registry row onto the M06 approved form."""
    if not isinstance(mapping, CapabilityMethodMapping):
        raise ValueError("method mapping must be a versioned registry declaration")
    return ApprovedMethodMapping(
        method=mapping.identity.tool,
        capability_key=mapping.capability_key,
        operation=mapping.operation,
        resources=tuple(mapping.constraints.resources),
        domains=tuple(mapping.constraints.domains),
        protocol_version=mapping.supported_protocol_version,
        tool_version=mapping.identity.version,
        server=mapping.identity.server,
    )


class DeclaredMappingReadPort:
    """Read-only registry projection for composition and acceptance use only.

    Production wiring must supply a persistence-backed `CapabilityMethodMappingReadPort`;
    this adapter never writes, never caches a decision and never authorizes.
    """

    def __init__(self, mappings: Sequence[CapabilityMethodMapping]) -> None:
        frozen = tuple(mappings)
        if any(not isinstance(item, CapabilityMethodMapping) for item in frozen):
            raise ValueError("declared mappings must be versioned registry declarations")
        keys = [(item.identity.server, item.identity.tool, item.identity.version,
                 item.capability_key, item.operation) for item in frozen]
        if len(set(keys)) != len(keys):
            raise ValueError("declared mappings must not repeat a mapping identity")
        self._mappings = frozen

    async def find_mapping(self, *, identity: MethodIdentity, capability_key: str,
                           operation: str) -> CapabilityMethodMapping | None:
        matches = [item for item in self._mappings
                   if item.identity == identity and item.capability_key == capability_key
                   and item.operation == operation]
        return matches[0] if len(matches) == 1 else None

    async def list_mappings(self, *, capability_key: str) -> Sequence[CapabilityMethodMapping]:
        return tuple(item for item in self._mappings if item.capability_key == capability_key)

    def configured(self) -> tuple[CapabilityMethodMapping, ...]:
        """Expose the read-only registry snapshot for review and digest stability tests."""
        return self._mappings


class ApprovedMethodOperationAdapter(MethodOperationPort):
    """Resolve one exact approved mapping; unknown, deprecated or ambiguous lookups quarantine.

    ``method_servers`` is server-owned configuration (method name -> declared MCP
    server) and ``method_versions`` is the approved tool version per method. A
    request-supplied method name is only ever looked up there; it never defines an
    approval by itself.
    """

    def __init__(self, *, mappings: CapabilityMethodMappingReadPort,
                 method_servers: Mapping[str, str],
                 method_versions: Mapping[str, str],
                 protocol: ProtocolCompatibilityReadPort | None = None,
                 protocol_version: str = "mcp-1",
                 now: datetime | None = None) -> None:
        if not isinstance(protocol_version, str) or not protocol_version.strip():
            raise ValueError("protocol version is required")
        servers = dict(method_servers)
        versions = dict(method_versions)
        if any(not _text(key) or not _text(value) for key, value in (*servers.items(), *versions.items())):
            raise ValueError("method server and version configuration must be non-empty")
        if set(servers) != set(versions):
            raise ValueError("every approved method needs exactly one server and one tool version")
        self._mappings = mappings
        self._service = CapabilityMethodMappingService(mappings=mappings, protocol=protocol)
        self._servers = MappingProxyType(servers)
        self._versions = MappingProxyType(versions)
        self._protocol_version = protocol_version
        self._now = now

    async def resolve(self, *, identity: ResolvedGrantIdentity, method: str, capability: str,
                      operation: str, resource: str, domain: str | None) -> MethodOperationResolution:
        # Rejection order is coarse-to-fine on purpose. The wire-level reason must name
        # the *request* defect whenever the request itself is already unacceptable:
        # a capability/operation/method that is not on its approved list is refused
        # without consulting the registry, so the generic registry outcome
        # (``unknown_mapping`` for a pair that was simply never declared) can never
        # mask "this operation is not approved for this capability" or "this method is
        # not server-approved". Only an otherwise valid, approved triple reaches the
        # registry, where a missing declaration is an honest ``unknown_mapping``.
        if capability not in APPROVED_CAPABILITIES:
            return MethodOperationResolution.rejected("capability_unknown")
        if operation not in CAPABILITY_OPERATIONS[capability]:
            return MethodOperationResolution.rejected("operation_unapproved")
        server = self._servers.get(method)
        if server is None:
            return MethodOperationResolution.rejected("method_unapproved")
        if _broad(method) or _broad(capability) or _broad(operation) or _broad(resource) or _broad(domain or ""):
            return MethodOperationResolution.rejected("wildcard_request_unrepresentable")
        tool_version = self._versions[method]
        result = await self._service.decide(
            identity=MethodIdentity(server=server, tool=method, version=tool_version),
            capability_key=capability, operation=operation, resource=resource, domain=domain,
            protocol_version=self._protocol_version, tool_version=tool_version,
            audit=MappingAuditMetadata(
                trace_id=identity.correlation_id, span_id=identity.correlation_id,
                actor=identity.subject_id,
                request_digest=digest({"method": method, "capability": capability,
                                       "operation": operation, "resource": resource,
                                       "domain": domain}),
                occurred_at=(self._now or datetime.now(UTC)).isoformat(),
            ),
        )
        if result.decision is not MappingDecision.ALLOW or result.mapping is None:
            reason = (result.reason.value if result.reason is not None else "unknown_mapping")
            if reason == "unknown_mapping":
                # Capability, operation and method all passed their approved-list checks,
                # so the registry's "nothing declared for this triple" means this method
                # is not approved *for this operation of this capability* (the method is
                # declared for another capability/operation). Reporting the precise
                # operation-level reason keeps the quarantine evidence actionable, and
                # both codes stay in the evidence store's unknown-mapping set, so the
                # partition is blocked either way.
                reason = "operation_unapproved"
            return MethodOperationResolution.rejected(reason)
        declared = declared_method_mapping(result.mapping)
        if (declared.capability_key != capability or declared.operation != operation
                or declared.method != method or declared.server != server):
            return MethodOperationResolution.rejected("broadening_mapping")
        return MethodOperationResolution.approved_mapping(
            mapping_digest=declared.mapping_digest, capability_key=capability, operation=operation)


def module_capability_operations(action_kind: str) -> frozenset[str] | None:
    """Map a normalized module action kind onto approved capability operations."""
    operations = MODULE_CAPABILITY_ACTIONS.get(action_kind)
    if operations is None or any(item not in APPROVED_OPERATION_KEYS for item in operations):
        return None
    return operations


def _broad(value: str) -> bool:
    return any(token in value for token in ("*", "?", "[", "]"))


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "APPROVED_OPERATION_KEYS",
    "ApprovedMethodMapping",
    "ApprovedMethodOperationAdapter",
    "DeclaredMappingReadPort",
    "MODULE_CAPABILITY_ACTIONS",
    "declared_method_mapping",
    "module_capability_operations",
]
