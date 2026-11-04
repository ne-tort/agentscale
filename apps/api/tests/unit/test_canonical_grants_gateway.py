"""Source acceptance contracts for the M06 canonical grants gateway.

Verification is intentionally frozen during migration; this file is reviewed as
an executable contract and is not run in this stage.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from prodavan.application.dynamic_apps_definition.contracts import DefinitionResolutionOutcome
from prodavan.application.canonical_grants import (
    CanonicalAuditEvent,
    CanonicalGrant,
    CanonicalGrantDecision,
    CanonicalGrantGatewayService,
    CanonicalGrantReason,
    CanonicalGrantRequest,
    RuntimeFencingEvidence,
)
from prodavan.application.plugin_instances import (
    FencingToken,
    PluginInstanceIdentity,
    PluginInstanceDecisionReason,
    PluginInstanceRecord,
    PluginInstanceState,
    PositiveWriteDecision,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Company:
    async def resolve_tenant_id(self, *, company_id):
        return "tenant-1" if company_id == "company-1" else None


class Definitions:
    async def resolve(self, query):
        return SimpleNamespace(
            outcome=DefinitionResolutionOutcome.RESOLVED,
            reference=SimpleNamespace(tenant_id="tenant-1", app_id=query.app_id, app_definition_id="definition-1", app_version_id="version-1", manifest_version=query.manifest_version, artifact_digest="sha256:" + "a" * 64),
            version=SimpleNamespace(requested_capabilities=(SimpleNamespace(name="storage.kv"),)),
        )


PLUGIN_IDENTITY = PluginInstanceIdentity(
    "plugin-1", "definition-1", "version-1", "tenant-1", "tenant", "tenant-1", "sha256:" + "a" * 64
)


class Identity:
    async def resolve_identity(self, **kwargs):
        return PLUGIN_IDENTITY


class Fencing:
    async def authorize_positive_write(self, request):
        # M05 возвращает запись, чья identity должна совпасть с identity, ранее
        # разрешённой портом plugin_identity. Прежний фейк подставлял в поле
        # `identity` экземпляр порта-резолвера (`IdentityRecord.identity`), что
        # моделировало невозможное состояние: сервис получил бы запись с чужой
        # identity и обязан был бы отказать.
        return PositiveWriteDecision(
            True,
            PluginInstanceDecisionReason.ALLOWED,
            PluginInstanceRecord(
                identity=PLUGIN_IDENTITY,
                subject_id=request.subject_id,
                session_id=request.session_id,
                state=PluginInstanceState.ACTIVE,
                fencing=request.fencing,
                lease_owner=request.lease_owner,
                lease_id=request.lease_id,
                lease_until=request.now + timedelta(minutes=5),
            ),
        )


class Grants:
    def __init__(self, values):
        self.values = values

    async def list_grants(self, **kwargs):
        return self.values


class Audit:
    def __init__(self):
        self.events: list[CanonicalAuditEvent] = []

    async def record(self, event):
        self.events.append(event)


def request(**changes):
    value = dict(company_id="company-1", app_id="billing", manifest_version="1.0.0", plugin_instance_id="plugin-1", capability_key="storage.kv", operation="read", resource="invoice/1", domain="storage", constraints={"region": "eu"}, fencing=RuntimeFencingEvidence("plugin-1", "employee-1", "session-1", 2, 3, "writer-1", "lease-1", NOW, "sha256:" + "a" * 64), correlation_id="mcp-correlation-1")
    value.update(changes)
    return CanonicalGrantRequest(**value)


def grant(**changes):
    value = dict(grant_id="grant-1", tenant_id="tenant-1", app_definition_id="definition-1", app_id="billing", app_version_id="version-1", app_artifact_digest="sha256:" + "a" * 64, scope_kind="tenant", scope_id="tenant-1", plugin_instance_id="plugin-1", subject_id="employee-1", session_id="session-1", capability_key="storage.kv", operations=frozenset({"read"}), resources=frozenset({"invoice/1"}), domains=frozenset({"storage"}), constraints={"region": "eu"}, issued_at=NOW, issued_by="admin-1")
    value.update(changes)
    return CanonicalGrant(**value)


def service(values):
    return CanonicalGrantGatewayService(company=Company(), definitions=Definitions(), grants=Grants(values), plugin_identity=Identity(), fencing=Fencing(), audit=Audit())


@pytest.mark.asyncio
async def test_exact_canonical_grant_allows_and_keeps_mcp_correlation():
    gateway = service([grant()])
    result = await gateway.authorize(request(), now=NOW)
    assert result.allowed
    assert result.correlation_id == "mcp-correlation-1"


@pytest.mark.asyncio
async def test_requested_capability_is_not_grant_and_missing_fencing_denies():
    gateway = service([grant()])
    result = await gateway.authorize(request(fencing=None), now=NOW)
    assert result.reason is CanonicalGrantReason.PLUGIN_FENCING_DENIED


@pytest.mark.asyncio
async def test_operation_resource_domain_constraints_and_lifecycle_are_exact():
    gateway = service([grant(expires_at=NOW + timedelta(minutes=1))])
    assert (await gateway.authorize(request(operation="write"), now=NOW)).reason is CanonicalGrantReason.OPERATION_NOT_GRANTED
    assert (await gateway.authorize(request(resource="invoice/2"), now=NOW)).reason is CanonicalGrantReason.RESOURCE_NOT_GRANTED
    assert (await gateway.authorize(request(domain="network"), now=NOW)).reason is CanonicalGrantReason.DOMAIN_NOT_GRANTED
    assert (await gateway.authorize(request(constraints={"region": "us"}), now=NOW)).reason is CanonicalGrantReason.CONSTRAINTS_NOT_SATISFIED
    assert (await gateway.authorize(request(), now=NOW + timedelta(minutes=1))).reason is CanonicalGrantReason.EXPIRED


def test_wildcards_are_unrepresentable_and_audit_is_redacted():
    with pytest.raises(ValueError, match="wildcard"):
        grant(resources=frozenset({"invoice/*"}))
    event = CanonicalAuditEvent("mcp-1", NOW, "company-1", "tenant-1", "billing", "1.0.0", "version-1", "plugin-1", "storage.kv.read", "read", "invoice/1", "storage", CanonicalGrantDecision.DENY, CanonicalGrantReason.GRANT_MISSING)
    payload = event.redacted()
    assert payload["correlation_id"] == "mcp-1"
    assert "constraints" not in payload
