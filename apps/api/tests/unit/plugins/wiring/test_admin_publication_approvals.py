"""Contract test: production admin composition owns publication approvals."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from prodavan.api.v1 import plugin_admin as api
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.application.plugins.installation_scope.contracts import InstallationScope
from prodavan.application.plugins.adapters.stores import (
    InMemoryAdminAudit,
    InMemoryRouteRegistry,
)
from prodavan.application.plugins.platform_admin.state import attach_admin_runtime
from prodavan.application.plugins.wiring import install
from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
    SqlPluginPublicationApprovalRepository,
)

from ..admin.fakes import FakeGrantStore, FakePublicationApprovalStore
from ..adapters.fakes import COMPANY, PLUGIN, SECRET
from ..adapters.test_build import _deps
from ..adapters.test_integration_seam import DIGEST, VERSION


@pytest.mark.asyncio
async def test_composed_admin_app_without_approval_override_supports_preapproval_and_regrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps()

    def session_factory() -> Any:
        # Callable shared-factory double for update_lock/installation_scopes
        # composition; the test itself never opens a real SQL session.
        raise AssertionError("composition-only test must not open SQL sessions")

    repository = SqlPluginPublicationApprovalRepository(
        session_factory=session_factory  # type: ignore[arg-type]
    )
    grant_store = FakeGrantStore()
    # _admin_ports reads the shared factory from the grant store for the real
    # SqlPluginUpdateLock and SqlInstallationScopeRepository construction.
    grant_store._session_factory = session_factory
    audit = InMemoryAdminAudit()
    repos = SimpleNamespace(
        instance_store=deps["instance_store"],
        artifact_store=deps["artifact_store"],
        grant_revocation=deps["revocation_source"],
        grant_store=grant_store,
        audit=audit,
        publication_approvals=repository,
    )
    zone_services = {
        "tenant_db": deps["tenant_db_service"],
        "object_store": deps["object_store_service"],
        "object_buckets": deps["bucket_admin"],
        "artifact_registry": deps["artifact_registry"],
        "activator": deps["activator"],
    }
    monkeypatch.setattr(install, "_zone_services", lambda *args: zone_services)

    # Deliberately no publication_approvals override: _admin_ports must take
    # the SQL repository from the shared PluginRepositories bundle.
    overrides = {
        "platform_admin": deps["platform"],
        "routes": InMemoryRouteRegistry(),
        "instance_store": deps["instance_store"],
        "instance_resolver": deps["instance_resolver"],
        "secret_projection": object(),
        "deployment_admin": deps["deployment_admin"],
        "artifact_store": deps["artifact_store"],
        "revocation_source": deps["revocation_source"],
        "admin_grant_store": grant_store,
        "admin_audit": audit,
    }
    ports = install._admin_ports(
        SimpleNamespace(),
        repos,
        SimpleNamespace(claims_secret=SECRET),
        overrides,
        [],
        {},
        [],
    )
    assert isinstance(ports.publication_approvals, SqlPluginPublicationApprovalRepository)
    assert ports.publication_approvals._session_factory is session_factory

    # Isolate this composition contract from DB I/O while preserving the real
    # SQL repository type and shared-factory assertion above.
    durable_fake = FakePublicationApprovalStore()
    monkeypatch.setattr(repository, "get", durable_fake.get)
    monkeypatch.setattr(repository, "create", durable_fake.create)
    monkeypatch.setattr(repository, "mark_consumed", durable_fake.mark_consumed)

    # The real SqlInstallationScopeRepository type stays in place, but its DB
    # ensure_from_source is isolated exactly like the approval repository above:
    # the grant saga must not open the inert shared session factory.
    async def fake_ensure_from_source(
        *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> InstallationScope:
        return InstallationScope(
            company_id=company_id,
            plugin_instance_id=plugin_instance_id,
            cabinet_id="cab_acceptance",
            project_id="proj_acceptance",
        )

    monkeypatch.setattr(
        ports.installation_scopes, "ensure_from_source", fake_ensure_from_source
    )

    app = FastAPI()
    service = PluginAdminService(ports)
    attach_admin_runtime(app, ports=ports, service=service)
    request = SimpleNamespace(app=app)
    capabilities = api.CapabilitiesBody(capabilities=("cache",))
    approval_body = api.PublicationApprovalBody(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=capabilities,
    )
    grant_body = api.GrantBody(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=capabilities,
    )

    approval = await api.approve_plugin_publication(  # type: ignore[arg-type]
        body=approval_body, request=request, actor_id="platform-admin"
    )
    assert approval.created is True
    first = await api.grant_plugin(  # type: ignore[arg-type]
        body=grant_body, request=request, _="platform-admin"
    )
    assert first.grant_status.value == "active"

    await service.revoke(company_id=COMPANY, plugin_id=PLUGIN)
    regranted = await api.grant_plugin(  # type: ignore[arg-type]
        body=grant_body, request=request, _="platform-admin"
    )

    assert regranted.grant_status.value == "active"
    assert regranted.grant_id != first.grant_id
    assert len(durable_fake.records) == 1
    stored = next(iter(durable_fake.records.values()))
    assert stored.consumed_at is not None
    assert len(grant_store.grants) == 1
