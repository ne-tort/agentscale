"""Public company-only bootstrap and fail-closed private scope boundaries."""
from __future__ import annotations

import httpx
import pytest

from prodavan.application.plugins.installation_scope.repository import SqlInstallationScopeRepository
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.installation_scope import PluginInstallationScopeRow

from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


def _employee_headers(world: EntrypointWorld) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {world.employee_token}",
        "Accept": "application/json",
    }


async def test_public_bootstrap_is_company_only_and_exposes_no_private_scope(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.get(
        world.api_url
        + f"/api/v1/public/plugins/{world.config.company_id}/{world.config.plugin_id}/bootstrap",
        params={"route_path": "/"},
    )
    assert response.status_code == 200, response.text[:800]
    payload = response.json()
    assert payload["plugin_id"] == world.config.plugin_id
    assert payload["manifest_version"] == world.config.version
    assert payload["artifact_digest"] == world.artifact.record.digest
    assert payload["scope"] == {
        "company_id": world.config.company_id,
        "cabinet_id": None,
        "project_id": None,
        "session_id": None,
    }
    assert payload["public_pages"] == ["/"]
    # Production job declarations deliberately pin public=false RPC operations;
    # public bootstrap must not expose any private operation.
    assert payload["rpc_operations"] == []
    assert payload["static_origin"] == "https://static.acceptance.test"
    assert payload["plugin_shell_origin"] == "https://shell.acceptance.test"
    for forbidden in ("token", "dsn", "webhook_secret_ref", "admin_receipt"):
        assert forbidden not in payload


async def test_private_bootstrap_returns_exact_source_backed_scope(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.get(
        world.api_url
        + f"/api/v1/companies/{world.config.company_id}/plugins/{world.config.plugin_id}/bootstrap",
        params={"route_path": "/"},
        headers=_employee_headers(world),
    )
    assert response.status_code == 200, response.text[:800]
    payload = response.json()
    assert payload["scope"] == {
        "company_id": world.config.company_id,
        "cabinet_id": world.config.cabinet_id,
        "project_id": world.config.project_id,
        "session_id": None,
    }


async def test_source_backed_scope_mismatch_is_fail_closed(
    world: EntrypointWorld,
) -> None:
    repository = SqlInstallationScopeRepository(
        session_factory=world.postgres.session_factory
    )
    with pytest.raises(AppError) as mismatch:
        await repository.get(
            company_id=world.config.foreign_company_id,
            plugin_instance_id=world.config.plugin_instance_id,
        )
    assert mismatch.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"


async def test_private_bootstrap_without_parent_binding_fails_closed(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    async with world.postgres.session_factory() as session:
        row = await session.get(
            PluginInstallationScopeRow, world.config.plugin_instance_id
        )
        assert row is not None
        await session.delete(row)
        await session.commit()
    response = await http_client.get(
        world.api_url
        + f"/api/v1/companies/{world.config.company_id}/plugins/{world.config.plugin_id}/bootstrap",
        params={"route_path": "/"},
        headers=_employee_headers(world),
    )
    assert response.status_code == 503, response.text[:800]
    payload = response.json()
    assert payload.get("code") == "PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE"
    assert payload.get("decision") != "allow"
    assert payload.get("scope", {}).get("cabinet_id") is None
    assert payload.get("scope", {}).get("project_id") is None


async def test_cross_tenant_private_bootstrap_is_typed_forbidden(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.get(
        world.api_url
        + f"/api/v1/companies/{world.config.foreign_company_id}/plugins/{world.config.plugin_id}/bootstrap",
        params={"route_path": "/"},
        headers=_employee_headers(world),
    )
    assert response.status_code == 403, response.text[:800]
    payload = response.json()
    assert payload.get("code") == "FORBIDDEN"
    assert payload.get("decision") != "allow"
