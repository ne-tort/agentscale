"""Source acceptance contracts for the T03 grant projection decision adapter.

Migration verification is frozen; this suite is intentionally not executed here.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prodavan.application.canonical_grants import (
    CanonicalGrantDecision,
    CanonicalGrantDecisionResult,
    CanonicalGrantReason,
    RuntimeFencingEvidence,
)
from prodavan.application.dynamic_apps_definition import DefinitionResolutionOutcome
from prodavan.application.grant_projection import (
    GrantProjectionDecision,
    GrantProjectionDecisionAdapter,
    GrantProjectionReason,
    GrantProjectionRequest,
)
from prodavan.application.plugin_instances import (
    FencingToken,
    PluginInstanceIdentity,
    PluginInstanceRecord,
    PluginInstanceState,
    PositiveWriteDecision,
    PluginInstanceDecisionReason,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class Definitions:
    def __init__(self, outcome=DefinitionResolutionOutcome.RESOLVED):
        self.outcome = outcome

    async def resolve(self, query):
        if self.outcome is DefinitionResolutionOutcome.QUARANTINED:
            return SimpleNamespace(outcome=self.outcome, reference=None, version=None)
        return SimpleNamespace(
            outcome=self.outcome,
            reference=SimpleNamespace(
                tenant_id=query.tenant_id,
                app_id=query.app_id,
                app_definition_id="definition-1",
                app_version_id="version-1",
                manifest_version=query.manifest_version,
                artifact_digest=DIGEST,
            ),
            version=SimpleNamespace(requested_capabilities=(SimpleNamespace(name="storage.kv"),)),
        )


class Fencing:
    def __init__(self):
        self.calls = []

    async def resolve_identity(self, **kwargs):
        self.calls.append(("identity", kwargs))
        return PluginInstanceIdentity("plugin-1", "definition-1", "version-1", "tenant-1", "project", "project-1", DIGEST)

    async def authorize_positive_write(self, request):
        self.calls.append(("fence", request))
        return PositiveWriteDecision(
            True,
            PluginInstanceDecisionReason.ALLOWED,
            PluginInstanceRecord(
                request.identity,
                request.subject_id,
                request.session_id,
                PluginInstanceState.ACTIVE,
                request.fencing,
                request.lease_owner,
                request.lease_id,
                request.now,
            ),
        )


class Grants:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def authorize(self, request, *, now=None):
        self.calls.append(request)
        return self.result


class Transition:
    def __init__(self):
        self.commands = []

    async def project(self, command):
        self.commands.append(command)
        return True


def request(**changes):
    value = dict(
        tenant_id="tenant-1", company_id="company-1", app_id="billing", manifest_version="1.0.0",
        plugin_instance_id="plugin-1", scope_kind="project", scope_id="project-1",
        subject_id="employee-1", session_id="session-1", capability_key="storage.kv",
        operation="read", resource="invoice/1", domain="storage",
        fencing=RuntimeFencingEvidence(
            "plugin-1", "employee-1", "session-1", 2, 3, "writer-1", "lease-1", NOW, DIGEST
        ),
    )
    value.update(changes)
    return GrantProjectionRequest(**value)


def adapter(*, definitions=None, grant=None):
    fencing = Fencing()
    grants = Grants(grant or CanonicalGrantDecisionResult(CanonicalGrantDecision.ALLOW, CanonicalGrantReason.ALLOWED, "corr"))
    transition = Transition()
    return GrantProjectionDecisionAdapter(definitions=definitions or Definitions(), fencing=fencing, grants=grants, transition=transition), fencing, grants, transition


@pytest.mark.asyncio
async def test_exact_identity_and_capability_key_reach_m05_m06_and_atomic_projection():
    gateway, fencing, grants, transition = adapter()
    result = await gateway.decide(request(), now=NOW)
    assert result.allowed
    assert grants.calls[0].capability_key == "storage.kv"
    assert grants.calls[0].plugin_instance_id == "plugin-1"
    assert grants.calls[0].fencing.subject_id == "employee-1"
    assert transition.commands[0].request.scope_id == "project-1"
    assert result.projected
    assert result.requested_capability_key == "storage.kv"
    assert result.granted_capability_key == "storage.kv"
    assert result.granted_scope == ("project", "project-1")
    assert result.granted_subject_id == "employee-1"
    assert result.granted_session_id == "session-1"


@pytest.mark.asyncio
async def test_grant_quarantine_is_propagated_and_projected_atomically():
    grant = CanonicalGrantDecisionResult(CanonicalGrantDecision.QUARANTINE, CanonicalGrantReason.UNSUPPORTED_GRANT, "corr")
    gateway, fencing, grants, transition = adapter(grant=grant)
    result = await gateway.decide(request(), now=NOW)
    assert result.quarantined
    assert result.reason is GrantProjectionReason.GRANT_QUARANTINED
    assert result.granted_capability_key is None
    assert transition.commands[0].decision is GrantProjectionDecision.QUARANTINE


@pytest.mark.asyncio
async def test_definition_quarantine_is_propagated_without_m05_or_m06_calls():
    gateway, fencing, grants, transition = adapter(definitions=Definitions(DefinitionResolutionOutcome.QUARANTINED))
    result = await gateway.decide(request(), now=NOW)
    assert result.quarantined
    assert result.reason is GrantProjectionReason.DEFINITION_QUARANTINED
    assert not fencing.calls and not grants.calls
    assert transition.commands[0].decision is GrantProjectionDecision.QUARANTINE


@pytest.mark.asyncio
async def test_legacy_and_wildcard_sources_are_quarantined_without_downstream_calls():
    gateway, fencing, grants, transition = adapter()
    legacy = await gateway.decide(request(source_kind="legacy"), now=NOW)
    wildcard = await gateway.decide(request(resource="invoice/*"), now=NOW)
    assert legacy.decision is GrantProjectionDecision.QUARANTINE
    assert legacy.reason is GrantProjectionReason.LEGACY_SOURCE
    assert wildcard.reason is GrantProjectionReason.WILDCARD_SOURCE
    assert not fencing.calls and not grants.calls
    assert len(transition.commands) == 2


def test_request_rejects_legacy_scope_shapes_and_mismatched_instance_scope():
    with pytest.raises(ValueError, match="scope_kind"):
        request(scope_kind="legacy")
    with pytest.raises(ValueError, match="scope_id"):
        request(scope_kind="plugin_instance", scope_id="other-plugin")


def test_contract_exposes_only_atomic_project_port_not_orm_or_direct_writes():
    from prodavan.application.grant_projection import ports

    assert not hasattr(ports.AtomicGrantProjectionPort, "add")
    assert not hasattr(ports.AtomicGrantProjectionPort, "update")
    assert not hasattr(ports.AtomicGrantProjectionPort, "delete")
