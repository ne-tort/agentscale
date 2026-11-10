"""Production admin wiring owns a durable SQL route registry."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.adapters.stores import InMemoryAdminAudit
from prodavan.application.plugins.wiring import install
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
    SqlPluginPublicationApprovalRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.routes import (
    SqlRouteRegistry,
)

from ..adapters.fakes import SECRET
from ..adapters.test_build import _deps


def test_admin_ports_share_repository_session_factory_without_memory_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps()

    def session_factory() -> Any:
        # Callable shared-factory double required by SqlPluginUpdateLock and
        # SqlInstallationScopeRepository composition; never invoked in this test.
        raise AssertionError("composition-only test must not open SQL sessions")

    grants = SqlCapabilityGrantRepository(
        session_factory=session_factory  # type: ignore[arg-type]
    )
    approvals = SqlPluginPublicationApprovalRepository(
        session_factory=session_factory  # type: ignore[arg-type]
    )
    audit = InMemoryAdminAudit()
    repos = SimpleNamespace(
        instance_store=deps["instance_store"],
        artifact_store=deps["artifact_store"],
        grant_revocation=deps["revocation_source"],
        grant_store=grants,
        audit=audit,
        publication_approvals=approvals,
    )
    zone_services = {
        "tenant_db": deps["tenant_db_service"],
        "object_store": deps["object_store_service"],
        "object_buckets": deps["bucket_admin"],
        "artifact_registry": deps["artifact_registry"],
        "activator": deps["activator"],
    }
    monkeypatch.setattr(install, "_zone_services", lambda *args: zone_services)
    overrides = {
        "platform_admin": deps["platform"],
        "instance_store": deps["instance_store"],
        "instance_resolver": deps["instance_resolver"],
        "secret_projection": object(),
        "deployment_admin": deps["deployment_admin"],
        "artifact_store": deps["artifact_store"],
        "revocation_source": deps["revocation_source"],
        "admin_audit": audit,
    }
    in_memory = []

    ports = install._admin_ports(
        SimpleNamespace(),
        repos,
        SimpleNamespace(claims_secret=SECRET),
        overrides,
        [],
        {},
        in_memory,
    )

    assert isinstance(ports.routes, SqlRouteRegistry)
    assert ports.routes._session_factory is session_factory
    assert ports.routes._session is None
    assert ports.grant_store._session_factory is session_factory
    assert ports.publication_approvals._session_factory is session_factory
    assert not any(item.startswith("routes ") for item in in_memory)
