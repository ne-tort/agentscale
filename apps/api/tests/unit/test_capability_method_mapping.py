"""Source acceptance tests for versioned capability mapping.

Migration verification is intentionally frozen; this file is not executed here.
"""

from types import MappingProxyType
import inspect
import math

import pytest

from prodavan.application.capability_method_mapping import (
    CapabilityMethodMapping,
    CapabilityMethodMappingService,
    MappingAuditMetadata,
    MappingDecision,
    MappingReason,
    MethodIdentity,
    ResourceDomainConstraint,
    mapping_digest,
)


DIGEST = "sha256:" + "a" * 64
IDENTITY = MethodIdentity(server="files", tool="read", version="2.1.0")
AUDIT = MappingAuditMetadata(
    trace_id="trace-1", span_id="span-1", actor="agent-1", request_digest=DIGEST,
    occurred_at="2026-01-01T00:00:00+00:00",
)


def make_mapping(**changes):
    values = {
        "identity": IDENTITY,
        "capability_key": "files.read",
        "operation": "read_file",
        "constraints": ResourceDomainConstraint(resources=("workspace/a.txt",), domains=("project",)),
        "supported_protocol_version": "2025-06-18",
        "tool_version_compatibility": ("2.1.0",),
        "audit_metadata": {"source": "signed-catalog", "revision": 3},
    }
    values.update(changes)
    return CapabilityMethodMapping(**values)


def test_identity_is_server_tool_version_and_mapping_is_immutable_with_stable_digest():
    mapping = make_mapping()
    assert mapping.identity == IDENTITY
    assert mapping.mapping_digest == mapping_digest(mapping)
    assert mapping.mapping_digest == mapping_digest(make_mapping())
    assert isinstance(mapping.audit_metadata, MappingProxyType)
    with pytest.raises((AttributeError, TypeError)):
        mapping.operation = "delete_file"
    with pytest.raises(TypeError):
        mapping.audit_metadata["source"] = "request"


def test_unknown_and_broadening_constraints_are_rejected_at_contract_boundary():
    with pytest.raises(ValueError, match="broadening"):
        ResourceDomainConstraint(resources=("*",), domains=("project",))
    with pytest.raises(ValueError, match="broadening"):
        ResourceDomainConstraint(resources=("workspace/*.txt",), domains=("project",))
    with pytest.raises(ValueError, match="broadening"):
        make_mapping(operation="*")
    with pytest.raises(ValueError, match="duplicates"):
        ResourceDomainConstraint(resources=("workspace/a.txt", "workspace/a.txt"), domains=("project",))
    with pytest.raises(ValueError, match="non-empty"):
        ResourceDomainConstraint(resources=(), domains=("project",))


def test_mapping_rejects_duplicate_versions_and_non_json_metadata():
    with pytest.raises(ValueError, match="duplicates"):
        make_mapping(tool_version_compatibility=("2.1.0", "2.1.0"))
    with pytest.raises(ValueError, match="finite"):
        make_mapping(audit_metadata={"limit": math.inf})
    with pytest.raises(ValueError, match="keys"):
        make_mapping(audit_metadata={1: "invalid"})


class _Reader:
    def __init__(self, mapping):
        self.mapping = mapping

    async def find_mapping(self, *, identity, capability_key, operation):
        return self.mapping

    async def list_mappings(self, *, capability_key):
        return (self.mapping,)


class _Protocol:
    async def supports_protocol(self, *, protocol_version, required_version):
        return protocol_version == required_version


@pytest.mark.asyncio
async def test_exact_mapping_and_mcp_tool_version_compatibility_allow_only_bounded_request():
    service = CapabilityMethodMappingService(mappings=_Reader(make_mapping()), protocol=_Protocol())
    result = await service.decide(
        identity=IDENTITY, capability_key="files.read", operation="read_file",
        resource="workspace/a.txt", domain="project", protocol_version="2025-06-18",
        tool_version="2.1.0", audit=AUDIT,
    )
    assert result.decision is MappingDecision.ALLOW
    assert result.mapping_digest == make_mapping().mapping_digest
    assert not result.quarantine


@pytest.mark.asyncio
async def test_malformed_request_quarantines_before_read_port():
    class ExplodingReader(_Reader):
        async def list_mappings(self, *, capability_key):
            raise AssertionError("read port must not receive malformed requests")

    service = CapabilityMethodMappingService(mappings=ExplodingReader(make_mapping()))
    result = await service.decide(
        identity=IDENTITY, capability_key="files.read", operation="read_file",
        resource="", domain="project", protocol_version="2025-06-18",
        tool_version="2.1.0", audit=AUDIT,
    )
    assert result.decision is MappingDecision.QUARANTINE
    assert result.reason is MappingReason.INVALID_REQUEST


@pytest.mark.asyncio
async def test_duplicate_registry_mapping_is_quarantined_without_grant_write():
    mapping = make_mapping()

    class DuplicateReader(_Reader):
        async def list_mappings(self, *, capability_key):
            return (self.mapping, self.mapping)

    service = CapabilityMethodMappingService(mappings=DuplicateReader(mapping), protocol=_Protocol())
    result = await service.decide(
        identity=IDENTITY, capability_key="files.read", operation="read_file",
        resource="workspace/a.txt", domain="project", protocol_version="2025-06-18",
        tool_version="2.1.0", audit=AUDIT,
    )
    assert result.decision is MappingDecision.QUARANTINE
    assert result.reason is MappingReason.BROADENING_MAPPING
    assert result.quarantine


@pytest.mark.asyncio
async def test_unknown_broadening_protocol_and_tool_versions_quarantine():
    mapping = make_mapping()
    service = CapabilityMethodMappingService(mappings=_Reader(mapping), protocol=_Protocol())
    cases = (
        ("other", "project", "2.1.0", MappingReason.BROADENING_MAPPING),
        ("workspace/a.txt", "other", "2.1.0", MappingReason.BROADENING_MAPPING),
        ("workspace/a.txt", "project", "9.9.9", MappingReason.INCOMPATIBLE_TOOL_VERSION),
    )
    for resource, domain, tool_version, reason in cases:
        result = await service.decide(
            identity=IDENTITY, capability_key="files.read", operation="read_file",
            resource=resource, domain=domain, protocol_version="2025-06-18",
            tool_version=tool_version, audit=AUDIT,
        )
        assert result.decision is MappingDecision.QUARANTINE
        assert result.reason is reason
        assert result.quarantine
    protocol_result = await service.decide(
        identity=IDENTITY, capability_key="files.read", operation="read_file",
        resource="workspace/a.txt", domain="project", protocol_version="old",
        tool_version="2.1.0", audit=AUDIT,
    )
    assert protocol_result.reason is MappingReason.UNSUPPORTED_PROTOCOL


def test_service_is_decision_only_and_exposes_no_positive_grant_write_api():
    source = inspect.getsource(CapabilityMethodMappingService)
    assert "grant" not in source.lower()
    assert "append" not in source.lower()
    assert "write" not in source.lower()
