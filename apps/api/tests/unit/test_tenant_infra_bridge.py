"""Unit tests — Pod Identity Bridge + tenant infra cache + allowlist."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from prodavan.application.pod_identity.bridge import (
    SCOPE_INFRA_CACHE,
    bump_pod_bridge_generation,
    build_launch_scopes,
    mint_pod_bridge_token,
    module_rows_scope,
    verify_pod_bridge_token,
)
from prodavan.application.tenant_infra.adapters.memory_cache import InMemoryTenantCache
from prodavan.application.tenant_infra.keys import rewrite_cache_key
from prodavan.application.tenant_infra.service import TenantInfraService
from prodavan.domain.errors import AppError
from prodavan.main import create_app


@pytest.mark.asyncio
async def test_mint_verify_and_revoke(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    token, claims = await mint_pod_bridge_token(
        project_id="proj-1",
        cabinet_id="cab-1",
        company_id="co-1",
        pod_id="pod-1",
        scopes=build_launch_scopes(["mod-a"]),
    )
    assert SCOPE_INFRA_CACHE in claims.scopes
    assert module_rows_scope("mod-a") in claims.scopes
    verified = await verify_pod_bridge_token(token)
    assert verified.project_id == "proj-1"
    await bump_pod_bridge_generation("pod-1")
    with pytest.raises(AppError) as exc:
        await verify_pod_bridge_token(token)
    assert exc.value.status == 401


def test_rewrite_denies_platform_prefix() -> None:
    key = rewrite_cache_key(company_id="c", project_id="p", user_key="foo")
    assert key == "tenant:c:proj:p:foo"
    with pytest.raises(AppError):
        rewrite_cache_key(company_id="c", project_id="p", user_key="prodavan:x")


@pytest.mark.asyncio
async def test_cache_service_roundtrip(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(settings, "tenant_infra_cache_ops_per_minute", 0)
    token, bridge = await mint_pod_bridge_token(
        project_id="proj-1",
        cabinet_id="cab-1",
        company_id="co-1",
        pod_id="pod-cache",
        scopes=[SCOPE_INFRA_CACHE],
    )
    assert token
    svc = TenantInfraService(cache=InMemoryTenantCache())
    await svc.set(bridge=bridge, project_id="proj-1", key="k1", value="v1", session=None)
    got = await svc.get(bridge=bridge, project_id="proj-1", key="k1", session=None)
    assert got["value"] == "v1"
    with pytest.raises(AppError):
        await svc.get(bridge=bridge, project_id="other", key="k1", session=None)


@pytest.mark.asyncio
async def test_pod_surface_allowlist_blocks_admin(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "shared-pod-token")
    token, _ = await mint_pod_bridge_token(
        project_id="proj-1",
        cabinet_id="cab-1",
        company_id="co-1",
        pod_id="pod-mw",
        scopes=[SCOPE_INFRA_CACHE],
    )
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/api/v1/admin/document-store/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        r2 = await client.get(
            "/api/v1/admin/document-store/health",
            headers={"Authorization": "Bearer shared-pod-token"},
        )
        assert r2.status_code == 403


@pytest.mark.asyncio
async def test_shared_token_cannot_use_infra(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "shared-pod-token")
    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(settings, "tenant_infra_cache_ops_per_minute", 0)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.put(
            "/api/v1/projects/proj-1/infra/cache/k",
            headers={"Authorization": "Bearer shared-pod-token"},
            json={"value": "x"},
        )
        assert r.status_code == 403
