"""Source acceptance contracts for the MCP grants method adapter.

Verification is intentionally frozen during migration; this file is not executed here.
"""

from datetime import UTC, datetime
import inspect
from types import SimpleNamespace

import pytest

from prodavan.application.canonical_grants import (
    CanonicalGrantDecision,
    CanonicalGrantDecisionResult,
    CanonicalGrantReason,
)
from prodavan.application.canonical_grants_method_adapter import (
    CanonicalGrantsMethodAdapter,
    CanonicalGrantsMethodRequest,
    McpAuditCorrelation,
)
from prodavan.application.capability_method_mapping import (
    CapabilityMethodMapping,
    MappingAuditMetadata,
    MappingDecision,
    MethodIdentity,
    ResourceDomainConstraint,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
IDENTITY = MethodIdentity("files", "read", "2.1.0")
MCP = McpAuditCorrelation("trace-1", "span-1", "mcp-correlation-1", "agent-1", DIGEST, "2026-01-01T00:00:00+00:00")


def mapping_result(*, decision=MappingDecision.ALLOW, capability_key="files.read", operation="read_file"):
    mapping = CapabilityMethodMapping(
        identity=IDENTITY,
        capability_key=capability_key,
        operation=operation,
        constraints=ResourceDomainConstraint(resources=("workspace/a.txt",), domains=("project",)),
        supported_protocol_version="2025-06-18",
        tool_version_compatibility=("2.1.0",),
    )
    return SimpleNamespace(
        decision=decision,
        mapping=mapping if decision is MappingDecision.ALLOW else None,
        reason=None,
        mapping_digest=mapping.mapping_digest if decision is MappingDecision.ALLOW else None,
        audit=MCP.mapping_audit(),
        quarantine=decision is not MappingDecision.ALLOW,
    )


def request():
    return CanonicalGrantsMethodRequest(
        company_id="company-1", app_id="files", manifest_version="1.0.0",
        plugin_instance_id="plugin-1", identity=IDENTITY, capability_key="files.read",
        operation="read_file", protocol_version="2025-06-18", tool_version="2.1.0",
        resource="workspace/a.txt", domain="project", constraints={"region": "eu"},
        fencing=None, mcp=MCP,
    )


class Mapping:
    def __init__(self, result):
        self.result = result
        self.audits = []

    async def decide(self, **kwargs):
        self.audits.append(kwargs)
        return self.result


class Grants:
    def __init__(self):
        self.requests = []

    async def authorize(self, request, *, now=None):
        self.requests.append((request, now))
        return CanonicalGrantDecisionResult(CanonicalGrantDecision.ALLOW, CanonicalGrantReason.ALLOWED, request.correlation_id, grant_id="grant-1")


@pytest.mark.asyncio
async def test_exact_identity_maps_to_canonical_request_and_preserves_correlation_and_fencing():
    mapping = Mapping(mapping_result())
    grants = Grants()
    result = await CanonicalGrantsMethodAdapter(mapping=mapping, grants=grants).authorize(request(), now=NOW)
    assert result.allowed
    forwarded, forwarded_now = grants.requests[0]
    original = request()
    assert forwarded.capability_key == "files.read"
    assert forwarded.operation == "read_file"
    assert forwarded.resource == original.resource
    assert forwarded.domain == original.domain
    assert forwarded.constraints == original.constraints
    assert forwarded.fencing is original.fencing
    assert forwarded.correlation_id == "mcp-correlation-1"
    assert forwarded_now is NOW
    audit = mapping.audits[0]["audit"]
    assert audit.trace_id == "trace-1"
    assert audit.span_id == "span-1"
    assert audit.actor == "agent-1"
    assert audit.request_digest == DIGEST
    assert audit.occurred_at == "2026-01-01T00:00:00+00:00"
    assert audit.extra["correlation_id"] == "mcp-correlation-1"
    assert result.mapping.mapping_digest == mapping_result().mapping_digest
    assert result.requested_capability_key == original.capability_key
    assert result.requested_operation == original.operation
    assert result.requested_resource == original.resource
    assert result.requested_domain == original.domain
    assert result.requested_constraints == original.constraints
    assert result.granted_capability_key == "files.read"
    assert result.granted_operation == "read_file"
    assert result.granted_resources == ("workspace/a.txt",)
    assert result.granted_domains == ("project",)
    assert result.granted_constraints == original.constraints


@pytest.mark.asyncio
async def test_mapping_quarantine_precedes_grant_lookup():
    mapping = Mapping(mapping_result(decision=MappingDecision.QUARANTINE))
    grants = Grants()
    result = await CanonicalGrantsMethodAdapter(mapping=mapping, grants=grants).authorize(request())
    assert result.mapping.quarantine
    assert result.grant is None
    assert grants.requests == []


@pytest.mark.asyncio
async def test_requested_values_are_preserved_when_mapping_grants_differ():
    mapping = Mapping(mapping_result(capability_key="documents.read", operation="read_document"))
    grants = Grants()
    original = request()
    result = await CanonicalGrantsMethodAdapter(mapping=mapping, grants=grants).authorize(original)
    forwarded = grants.requests[0][0]
    assert result.requested_capability_key == original.capability_key
    assert result.requested_operation == original.operation
    assert result.granted_capability_key == "documents.read"
    assert result.granted_operation == "read_document"
    assert forwarded.capability_key == result.granted_capability_key
    assert forwarded.operation == result.granted_operation


def test_adapter_is_decision_only_without_persistence_or_positive_grant_writes():
    source = inspect.getsource(CanonicalGrantsMethodAdapter)
    assert "persist" not in source.lower()
    assert "append" not in source.lower()
    assert "create_grant" not in source.lower()
