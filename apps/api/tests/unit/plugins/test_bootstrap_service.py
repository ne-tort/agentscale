"""Unit tests for PluginBootstrapService (employee/public bootstrap).

Uses an in-memory repository fake; no DB, no HTTP, no real cosign. Live acceptance
is intentionally out of scope here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prodavan.application.plugins.bootstrap.repository import InstallationRows, InstallationScope
from prodavan.application.plugins.bootstrap.service import PluginBootstrapService
from prodavan.domain.errors import AppError

DIGEST = "sha256:" + "a" * 64
COMPANY = "co_123"
PLUGIN = "mod_equipment"
CABINET = "cab_123"
PROJECT = "pr_123"


class FakeRepository:
    def __init__(self, rows: InstallationRows | None, *, scope=None, company_active: bool = True) -> None:
        self._rows = rows
        self._scope = scope
        self._company_active = company_active
        self.membership_calls: list[tuple[str, str]] = []

    async def require_membership(self, employee, company_id):
        self.membership_calls.append((employee.id, company_id))
        return SimpleNamespace(id=company_id)

    async def active_company(self, company_id):
        return SimpleNamespace(id=company_id) if self._company_active else None

    async def installation(self, company_id, plugin_id):
        return self._rows

    async def installation_scope(self, rows):
        return self._scope


def _manifest() -> dict:
    return {
        "plugin_id": PLUGIN,
        "version": "1.2.0",
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/tenant-co_123/mod_equipment/1.2.0"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": ["hello.greetings@1.0"],
        "migrations": {"engine": "liquibase", "path": "db/changelog.xml"},
        "egress_ref": "grant:mod_equipment",
        "public_pages": ["/", "/public"],
        "webhooks": [],
        "min_platform_version": "4.0.0",
        "rpc_operations": [
            {"capability": {"id": "hello.greetings", "version": "1.0"}, "operation": "list", "http_method": "GET", "backend_path": "/api/hello/list", "public": True},
            {"capability": {"id": "hello.greetings", "version": "1.0"}, "operation": "create", "http_method": "POST", "backend_path": "/api/hello", "public": False},
        ],
    }


def _rows(**overrides) -> InstallationRows:
    grant = SimpleNamespace(
        status="active", revoked_at=None, expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id="inst_1",
        manifest_version="1.2.0", artifact_digest=DIGEST,
        capabilities=["hello.greetings@1.0"],
    )
    instance = SimpleNamespace(
        status="active", company_id=COMPANY, plugin_id=PLUGIN,
        manifest_version="1.2.0", artifact_digest=DIGEST,
    )
    artifact = SimpleNamespace(
        status="published", cosign_verified=True, published_at=datetime(2026, 1, 1, tzinfo=UTC),
        plugin_id=PLUGIN, version="1.2.0", oci_digest=DIGEST, manifest=_manifest(),
    )
    for key, value in overrides.items():
        target = {"grant": grant, "instance": instance, "artifact": artifact}[key.split("__", 1)[0]]
        setattr(target, key.split("__", 1)[1], value)
    return InstallationRows(
        grant=grant, instance=instance, artifact=artifact, enabled=True
    )


_VALID_SCOPE = object()


def _scope(scope=_VALID_SCOPE):
    if scope is _VALID_SCOPE:
        return InstallationScope(
            company_id=COMPANY,
            plugin_instance_id="inst_1",
            cabinet_id=CABINET,
            project_id=PROJECT,
        )
    return scope


def _service(
    rows, *, scope=_VALID_SCOPE, company_active: bool = True
) -> tuple[PluginBootstrapService, FakeRepository]:
    repository = FakeRepository(rows, scope=_scope(scope), company_active=company_active)
    return _FakeRepositoryService(lambda: _SessionCtx(repository)), repository


class _FakeRepositoryService(PluginBootstrapService):
    def _repository(self, session):
        return session


class _SessionCtx:
    def __init__(self, repository) -> None:
        self._repository = repository

    async def __aenter__(self):
        return self._repository

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _static_origin(monkeypatch):
    monkeypatch.setattr(
        "prodavan.application.plugins.bootstrap.service.settings.plugin_static_origin",
        "https://plugin-origin.agentscale.dev",
    )
    monkeypatch.setattr(
        "prodavan.application.plugins.bootstrap.service.settings.plugin_shell_origin",
        "https://agentscale.dev",
    )


@pytest.mark.asyncio
async def test_private_bootstrap_happy_path():
    service, repository = _service(_rows())
    employee = SimpleNamespace(id="emp_1")
    result = await service.private(employee=employee, company_id=COMPANY, plugin_id=PLUGIN)
    assert result.plugin_id == PLUGIN
    assert result.manifest_version == "1.2.0"
    assert result.artifact_digest == DIGEST
    assert result.static_origin == "https://plugin-origin.agentscale.dev"
    assert result.plugin_shell_origin == "https://agentscale.dev"
    assert result.scope.company_id == COMPANY
    assert result.scope.cabinet_id == CABINET
    assert result.scope.project_id == PROJECT
    assert result.grant_expires_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert result.route_path == "/"
    assert repository.membership_calls == [("emp_1", COMPANY)]
    # Private: all manifest operations are visible, grant capabilities preserved.
    assert result.capabilities == ["hello.greetings@1.0"]
    assert len(result.rpc_operations) == 2


@pytest.mark.asyncio
async def test_disabled_installation_is_not_connected():
    rows = _rows()
    rows = InstallationRows(grant=rows.grant, instance=rows.instance, artifact=rows.artifact, enabled=False)
    service, _ = _service(rows)
    with pytest.raises(AppError) as exc:
        await service.private(employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN)
    assert exc.value.code == "PLUGIN_NOT_CONNECTED"
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_public_route_requires_public_page():
    service, _ = _service(_rows())
    with pytest.raises(AppError) as exc:
        await service.public(company_id=COMPANY, plugin_id=PLUGIN, route_path="/admin")
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_public_bootstrap_only_public_operations():
    service, _ = _service(_rows())
    result = await service.public(company_id=COMPANY, plugin_id=PLUGIN, route_path="/public")
    assert result.route_path == "/public"
    assert [op.operation for op in result.rpc_operations] == ["list"]
    assert result.capabilities == ["hello.greetings@1.0"]
    assert result.scope.company_id == COMPANY
    assert result.scope.cabinet_id is None
    assert result.scope.project_id is None


@pytest.mark.asyncio
async def test_private_bootstrap_rejects_cross_tenant_scope():
    service, _ = _service(
        _rows(),
        scope=InstallationScope(
            company_id="co_other",
            plugin_instance_id="inst_1",
            cabinet_id=CABINET,
            project_id=PROJECT,
        ),
    )
    with pytest.raises(AppError) as exc:
        await service.private(
            employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN
        )
    assert exc.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_private_bootstrap_rejects_scope_from_other_instance():
    service, _ = _service(
        _rows(),
        scope=InstallationScope(
            company_id=COMPANY,
            plugin_instance_id="inst_other",
            cabinet_id=CABINET,
            project_id=PROJECT,
        ),
    )
    with pytest.raises(AppError) as exc:
        await service.private(
            employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN
        )
    assert exc.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        None,
        InstallationScope(company_id=COMPANY, plugin_instance_id="inst_1", cabinet_id=CABINET),
        InstallationScope(company_id=COMPANY, plugin_instance_id="inst_1", project_id=PROJECT),
        InstallationScope(company_id=COMPANY, plugin_instance_id="inst_1", cabinet_id="", project_id=PROJECT),
        InstallationScope(company_id=COMPANY, plugin_instance_id="inst_1", cabinet_id=CABINET, project_id=" pr_123 "),
    ],
)
async def test_private_bootstrap_missing_required_scope_is_fail_closed(scope):
    service, _ = _service(_rows(), scope=scope)
    with pytest.raises(AppError) as exc:
        await service.private(
            employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN
        )
    assert exc.value.code == "PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE"
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_manifest_version_fallback_is_supported():
    rows = _rows()
    manifest = _manifest()
    manifest.pop("version")
    manifest["manifest_version"] = "1.2.0"
    rows.artifact.manifest = manifest
    service, _ = _service(rows)
    result = await service.private(employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN)
    assert result.manifest_version == "1.2.0"


@pytest.mark.asyncio
async def test_digest_mismatch_is_bootstrap_invalid():
    rows = _rows()
    rows.artifact.oci_digest = "sha256:" + "b" * 64
    service, _ = _service(rows)
    with pytest.raises(AppError) as exc:
        await service.private(employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN)
    assert exc.value.code == "PLUGIN_BOOTSTRAP_INVALID"
    assert exc.value.status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shell_origin",
    [
        "",
        "http://agentscale.dev",
        "https://*.agentscale.dev",
        "https://agentscale.dev/path",
        "https://agentscale.dev/?token=x",
        "https://plugin-origin.agentscale.dev",
    ],
)
async def test_bootstrap_missing_wrong_or_shared_shell_origin_is_fail_closed(
    monkeypatch, shell_origin: str
):
    monkeypatch.setattr(
        "prodavan.application.plugins.bootstrap.service.settings.plugin_shell_origin",
        shell_origin,
    )
    service, _ = _service(_rows())
    with pytest.raises(AppError) as exc:
        await service.private(
            employee=SimpleNamespace(id="e"), company_id=COMPANY, plugin_id=PLUGIN
        )
    assert exc.value.code == "PLUGIN_BOOTSTRAP_INVALID"
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_public_requires_active_company():
    service, _ = _service(_rows(), company_active=False)
    with pytest.raises(AppError) as exc:
        await service.public(company_id=COMPANY, plugin_id=PLUGIN)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.status == 404
