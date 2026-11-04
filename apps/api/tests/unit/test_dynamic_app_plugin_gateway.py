"""Source contracts for the exclusive dynamic app/plugin gateway zone."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from prodavan.application.dynamic_app_plugin_gateway import (
    DynamicAppPluginGatewayService,
    GatewayOutcome,
    GatewayReason,
    PluginInvokeRequest,
    PluginOpenRequest,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    CapabilityDecision,
    CapabilityGrant,
    GrantActor,
    RequestedCapability,
    CapabilityName,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
SCOPE = AppScope(kind=AppScopeKind.TENANT, id="tenant-1")


def record(*, lifecycle=AppLifecycleStatus.PUBLISHED, methods=None, grant=True, digest=DIGEST):
    methods = methods or [{"method": "invoice.read", "capability": "storage.kv", "operation": "read", "resource": "invoice", "domain": "storage"}]
    definition = AppDefinition(id="def-1", tenant_id="tenant-1", app_id="demo-app", display_name="Demo", scope=SCOPE, status=lifecycle, current_version_id="ver-1", created_at=NOW, updated_at=NOW)
    manifest = {"protocol": "1.0", "appId": "demo-app", "version": "1.0.0", "displayName": "Demo", "requestedCapabilities": [], "entrypoints": {"plugin": {"methods": methods}}}
    version = AppVersion(id="ver-1", app_definition_id="def-1", manifest_version="1.0.0", artifact_digest=digest, manifest=manifest, requested_capabilities=(RequestedCapability(name=CapabilityName.STORAGE_KV, reason="read invoices"),), status=lifecycle, created_at=NOW, published_at=NOW if lifecycle is AppLifecycleStatus.PUBLISHED else None)
    instance = AppInstance(id="inst-1", app_definition_id="def-1", app_version_id="ver-1", scope=SCOPE, status=lifecycle, created_at=NOW, updated_at=NOW)
    grants = () if not grant else (AppGrant(id="grant-row-1", app_definition_id="def-1", app_version_id="ver-1", scope=SCOPE, grants=(CapabilityGrant(name="storage.kv", decision=CapabilityDecision.GRANTED, constraints={"operations": ("read",), "resources": ("invoice",), "domains": ("storage",)}, granted_by=GrantActor.ADMINISTRATOR, expires_at=NOW + timedelta(hours=1)),), issued_at=NOW),)
    from prodavan.application.dynamic_app_plugin_gateway.contracts import PluginRecord
    return PluginRecord(definition=definition, version=version, grants=grants, instance=instance, methods=())


class Repository:
    def __init__(self, row):
        self.row = row

    async def resolve(self, request):
        return self.row


class Scope:
    async def allows(self, **kwargs):
        return True


class FailingScope(Scope):
    async def allows(self, **kwargs):
        raise RuntimeError("scope unavailable")


class Quota:
    def __init__(self, allowed=True):
        self.allowed = allowed

    async def allows(self, **kwargs):
        return self.allowed


class Executor:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


async def test_open_issues_opaque_handle_and_invoke_uses_exact_manifest_method():
    executor = Executor()
    service = DynamicAppPluginGatewayService(Repository(record()), executors={"storage.kv": executor}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    assert opened.outcome is GatewayOutcome.OPENED
    assert "token" not in repr(opened.handle).lower()
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert result.outcome is GatewayOutcome.ALLOWED
    assert executor.calls[0]["operation"] == "read"


async def test_unknown_method_is_denied_without_executor_call():
    executor = Executor()
    service = DynamicAppPluginGatewayService(Repository(record()), executors={"storage.kv": executor}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read.extra"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.METHOD_UNKNOWN)
    assert executor.calls == []


async def test_unpublished_plugin_is_lifecycle_denied():
    service = DynamicAppPluginGatewayService(Repository(record(lifecycle=AppLifecycleStatus.DISABLED)))
    result = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.LIFECYCLE_DENIED)


async def test_missing_grant_denies_and_never_calls_executor():
    executor = Executor()
    service = DynamicAppPluginGatewayService(Repository(record(grant=False)), executors={"storage.kv": executor}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    assert (opened.outcome, opened.reason) == (GatewayOutcome.DENIED, GatewayReason.GRANTS_MISSING)
    assert executor.calls == []


async def test_quota_and_scope_are_enforced_before_executor():
    executor = Executor()
    service = DynamicAppPluginGatewayService(Repository(record()), executors={"storage.kv": executor}, scope=Scope(), quota=Quota(False))
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.QUOTA_EXCEEDED)
    assert executor.calls == []


async def test_stale_or_foreign_handle_fails_closed():
    service = DynamicAppPluginGatewayService(Repository(record()))
    from prodavan.application.dynamic_app_plugin_gateway import PluginHandle
    result = await service.invoke(PluginInvokeRequest(PluginHandle(), "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)


async def test_published_handle_is_invalidated_when_registry_lifecycle_changes():
    repository = Repository(record())
    service = DynamicAppPluginGatewayService(repository, executors={"storage.kv": Executor()}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    repository.row = record(lifecycle=AppLifecycleStatus.DISABLED)
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)


async def test_scope_port_failure_is_quarantined_before_executor():
    executor = Executor()
    service = DynamicAppPluginGatewayService(Repository(record()), executors={"storage.kv": executor}, scope=FailingScope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.QUARANTINED, GatewayReason.PORT_FAILED)
    assert executor.calls == []


async def test_handle_re_resolution_rejects_artifact_or_method_drift():
    repository = Repository(record())
    service = DynamicAppPluginGatewayService(repository, executors={"storage.kv": Executor()}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    drifted = record(digest="sha256:" + "b" * 64, methods=[{"method": "invoice.delete", "capability": "storage.kv", "operation": "delete", "resource": "invoice", "domain": "storage"}])
    repository.row = drifted
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)


async def test_malformed_method_constraints_are_rejected_at_open():
    malformed = [{"method": "invoice.read", "capability": "storage.kv", "operation": "read", "resource": "invoice", "domain": "storage", "constraints": object()}]
    service = DynamicAppPluginGatewayService(Repository(record(methods=malformed)))
    result = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    assert (result.outcome, result.reason) == (GatewayOutcome.QUARANTINED, GatewayReason.MANIFEST_INVALID)


async def test_expired_envelope_grant_denies_before_executor():
    executor = Executor()
    row = record()
    # ``AppGrant`` is a frozen pydantic model, not a dataclass, so dataclasses.replace
    # cannot rebuild it. Re-validate a dumped copy instead of passing unsent values
    # through a constructor bypass: the envelope must remain self-consistent
    # (expires_at after issued_at, both timezone-aware) and be already expired.
    expired_at = NOW - timedelta(seconds=1)
    expired_issued = NOW - timedelta(hours=1)
    grant = AppGrant.model_validate(
        {**row.grants[0].model_dump(), "issued_at": expired_issued, "expires_at": expired_at}
    )
    row = replace(row, grants=(grant,))
    assert row.grants[0].expires_at == expired_at
    service = DynamicAppPluginGatewayService(Repository(row), executors={"storage.kv": executor}, scope=Scope(), quota=Quota())
    opened = await service.open(PluginOpenRequest("tenant-1", "demo-app", "1.0.0", SCOPE, "subject-1"))
    result = await service.invoke(PluginInvokeRequest(opened.handle, "invoice.read"), now=NOW)
    assert (result.outcome, result.reason) == (GatewayOutcome.DENIED, GatewayReason.GRANT_DENIED)
    assert executor.calls == []
