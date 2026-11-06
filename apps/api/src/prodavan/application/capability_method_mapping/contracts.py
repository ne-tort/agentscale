"""Versioned capability method-to-operation mapping contracts.

This zone is intentionally persistence-neutral and decision-only.  Mapping records
are immutable declarations; decisions never grant, persist, or mutate capability
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


class MappingDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class MappingReason(StrEnum):
    UNKNOWN_MAPPING = "unknown_mapping"
    BROADENING_MAPPING = "broadening_mapping"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    INCOMPATIBLE_TOOL_VERSION = "incompatible_tool_version"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True, slots=True)
class MethodIdentity:
    """The complete versioned MCP method identity; name alone is never enough."""

    server: str
    tool: str
    version: str

    def __post_init__(self) -> None:
        _required(self.server, "server")
        _required(self.tool, "tool")
        _required(self.version, "version")


@dataclass(frozen=True, slots=True)
class ResourceDomainConstraint:
    """Closed resource/domain boundary for one operation mapping."""

    resources: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, values in (("resources", self.resources), ("domains", self.domains)):
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
            if any(_is_broad(v) for v in values):
                raise ValueError(f"{name} cannot contain broadening wildcard")
        if not self.resources or not self.domains:
            raise ValueError("resources and domains must be non-empty")

    def contains(self, *, resource: str | None, domain: str | None) -> bool:
        return (
            resource is not None and resource in self.resources
            and domain is not None and domain in self.domains
        )


@dataclass(frozen=True, slots=True)
class CapabilityMethodMapping:
    """Versioned declaration mapping one MCP method to one bounded operation."""

    identity: MethodIdentity
    capability_key: str
    operation: str
    constraints: ResourceDomainConstraint
    supported_protocol_version: str
    tool_version_compatibility: tuple[str, ...] = ()
    audit_metadata: Mapping[str, Any] = field(default_factory=dict)
    mapping_digest: str = ""

    def __post_init__(self) -> None:
        _required(self.capability_key, "capability_key")
        _required(self.operation, "operation")
        _required(self.supported_protocol_version, "supported_protocol_version")
        if _is_broad(self.capability_key) or _is_broad(self.operation):
            raise ValueError("capability_key and operation cannot contain broadening wildcard")
        if not isinstance(self.tool_version_compatibility, tuple) or any(not isinstance(v, str) or not v.strip() for v in self.tool_version_compatibility):
            raise ValueError("tool_version_compatibility must contain non-empty strings")
        if not self.tool_version_compatibility:
            raise ValueError("tool_version_compatibility must be non-empty")
        if len(set(self.tool_version_compatibility)) != len(self.tool_version_compatibility):
            raise ValueError("tool_version_compatibility must not contain duplicates")
        frozen = _freeze_json_mapping(self.audit_metadata)
        object.__setattr__(self, "audit_metadata", frozen)
        expected = mapping_digest(self)
        if self.mapping_digest and self.mapping_digest != expected:
            raise ValueError("mapping_digest does not match mapping content")
        object.__setattr__(self, "mapping_digest", expected)

    def supports_tool_version(self, tool_version: str) -> bool:
        return tool_version in self.tool_version_compatibility


@dataclass(frozen=True, slots=True)
class MappingAuditMetadata:
    """Redacted decision attribution; raw arguments and secrets are excluded."""

    trace_id: str
    span_id: str
    actor: str
    request_digest: str
    occurred_at: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("trace_id", "span_id", "actor", "request_digest", "occurred_at"):
            _required(getattr(self, name), name)
        _sha256(self.request_digest, "request_digest")
        object.__setattr__(self, "extra", _freeze_json_mapping(self.extra))


@dataclass(frozen=True, slots=True)
class MappingResult:
    decision: MappingDecision
    reason: MappingReason | None
    mapping: CapabilityMethodMapping | None
    mapping_digest: str | None
    audit: MappingAuditMetadata
    quarantine: bool = False

    def __post_init__(self) -> None:
        if self.decision is MappingDecision.ALLOW:
            if self.mapping is None or self.reason is not None or self.quarantine:
                raise ValueError("allow requires a mapping and cannot be quarantined")
        elif self.decision is MappingDecision.QUARANTINE and not self.quarantine:
            raise ValueError("quarantine decision requires quarantine evidence")
        if self.mapping_digest is not None:
            _sha256(self.mapping_digest, "mapping_digest")


def mapping_digest(mapping: CapabilityMethodMapping) -> str:
    """Stable SHA-256 over the complete canonical declaration, excluding digest."""
    payload = {
        "server": mapping.identity.server,
        "tool": mapping.identity.tool,
        "version": mapping.identity.version,
        "capability_key": mapping.capability_key,
        "operation": mapping.operation,
        "resources": mapping.constraints.resources,
        "domains": mapping.constraints.domains,
        "supported_protocol_version": mapping.supported_protocol_version,
        "tool_version_compatibility": mapping.tool_version_compatibility,
        "audit_metadata": mapping.audit_metadata,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_json_default)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _is_broad(value: str) -> bool:
    return any(token in value for token in ("*", "?", "[", "]")) or value.casefold() in {"all", "any"}


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{name} must be a sha256: digest")
    if any(c not in "0123456789abcdef" for c in value[7:]):
        raise ValueError(f"{name} must be a sha256: digest")


def _freeze_json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("mapping must be a JSON object")
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(k, str) or not k for k in item):
                raise ValueError("metadata object keys must be non-empty strings")
            return MappingProxyType({k: freeze(v) for k, v in item.items()})
        if isinstance(item, list):
            return tuple(freeze(v) for v in item)
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("metadata must contain finite JSON numbers")
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        raise ValueError("metadata must contain JSON values")
    if any(not isinstance(k, str) or not k for k in value):
        raise ValueError("metadata object keys must be non-empty strings")
    return MappingProxyType({k: freeze(v) for k, v in value.items()})


def _json_default(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


__all__ = ["CapabilityMethodMapping", "MappingAuditMetadata", "MappingDecision", "MappingReason", "MappingResult", "MethodIdentity", "ResourceDomainConstraint", "mapping_digest"]
