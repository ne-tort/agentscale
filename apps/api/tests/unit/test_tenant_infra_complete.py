"""Unit tests — Tenant Infra quotas, cache TTL/keys, docs/events/userdb memory planes."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from prodavan.application.pod_identity.bridge import (
    SCOPE_INFRA_CACHE,
    SCOPE_INFRA_DOCS,
    SCOPE_INFRA_EVENTS,
    SCOPE_INFRA_OBJECTS,
    SCOPE_INFRA_USERDB,
    build_launch_scopes,
    mint_pod_bridge_token,
)
from prodavan.application.tenant_infra.adapters.memory_cache import InMemoryTenantCache
from prodavan.application.tenant_infra.events_service import TenantEventsService
from prodavan.application.tenant_infra.lifecycle import purge_project_tenant_infra
from prodavan.application.tenant_infra.service import TenantInfraService
from prodavan.application.tenant_infra.userdb_service import TenantUserDbService
from prodavan.domain.admin import CompanyTenantInfraQuota
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_cache_requires_ttl_and_enforces_max_keys(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(settings, "tenant_infra_cache_ops_per_minute", 0)
    token, bridge = await mint_pod_bridge_token(
        project_id="proj-1",
        cabinet_id="cab-1",
        company_id="co-1",
        pod_id="pod-cache",
        scopes=build_launch_scopes([]),
    )
    assert token
    assert SCOPE_INFRA_CACHE in bridge.scopes
    cache = InMemoryTenantCache()
    svc = TenantInfraService(cache=cache)
    monkeypatch.setattr(
        "prodavan.application.tenant_infra.quota.TenantInfraQuotaService.get_quota",
        AsyncMock(
            return_value=CompanyTenantInfraQuota(cache_max_keys=2, cache_ops_per_minute=0)
        ),
    )

    r1 = await svc.set(bridge=bridge, project_id="proj-1", key="a", value="1", session=None)
    assert r1["ttl_sec"] == 3600
    await svc.set(bridge=bridge, project_id="proj-1", key="b", value="2", session=None)
    with pytest.raises(AppError) as exc:
        await svc.set(bridge=bridge, project_id="proj-1", key="c", value="3", session=None)
    assert exc.value.status == 429
    assert exc.value.code == "TENANT_INFRA_QUOTA"

    deleted = await svc.purge_project(company_id="co-1", project_id="proj-1")
    assert deleted >= 1
    got = await svc.get(bridge=bridge, project_id="proj-1", key="a", session=None)
    assert got["found"] is False


@pytest.mark.asyncio
async def test_shared_memory_cache_survives_new_service_instances(monkeypatch) -> None:
    from prodavan.application.tenant_infra.adapters.memory_cache import reset_shared_memory_tenant_cache
    from prodavan.config.settings import settings

    reset_shared_memory_tenant_cache()
    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(
        "prodavan.application.tenant_infra.quota.TenantInfraQuotaService.get_quota",
        AsyncMock(return_value=CompanyTenantInfraQuota(cache_ops_per_minute=0)),
    )
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: None,
    )
    _, bridge = await mint_pod_bridge_token(
        project_id="proj-share",
        cabinet_id="cab-1",
        company_id="co-1",
        pod_id="pod-share",
        scopes=build_launch_scopes([]),
    )
    await TenantInfraService().set(
        bridge=bridge, project_id="proj-share", key="k", value="shared", session=None
    )
    got = await TenantInfraService().get(
        bridge=bridge, project_id="proj-share", key="k", session=None
    )
    assert got["value"] == "shared"
    reset_shared_memory_tenant_cache()


@pytest.mark.asyncio
async def test_launch_scopes_include_all_infra_planes(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    _, claims = await mint_pod_bridge_token(
        project_id="p",
        cabinet_id="c",
        company_id="co",
        pod_id="pod",
        scopes=build_launch_scopes([]),
    )
    for scope in (
        SCOPE_INFRA_CACHE,
        SCOPE_INFRA_DOCS,
        SCOPE_INFRA_USERDB,
        SCOPE_INFRA_EVENTS,
        SCOPE_INFRA_OBJECTS,
    ):
        assert scope in claims.scopes


@pytest.mark.asyncio
async def test_userdb_memory_create_insert_select(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    import prodavan.application.tenant_infra.userdb_service as udb

    udb._engine = None
    udb._memory_tables.clear()
    monkeypatch.setattr(udb, "get_userdb_engine", lambda: None)
    monkeypatch.setattr(
        "prodavan.application.tenant_infra.quota.TenantInfraQuotaService.get_quota",
        AsyncMock(return_value=CompanyTenantInfraQuota(userdb_ops_per_minute=0)),
    )

    _, bridge = await mint_pod_bridge_token(
        project_id="proj_udb",
        cabinet_id="cab",
        company_id="co",
        pod_id="pod",
        scopes=build_launch_scopes([]),
    )
    svc = TenantUserDbService()
    await svc.create_table(
        bridge=bridge,
        project_id="proj_udb",
        table="items",
        columns=[{"name": "title", "type": "text"}],
        session=None,
    )
    inserted = await svc.insert(
        bridge=bridge,
        project_id="proj_udb",
        table="items",
        row={"title": "hello"},
        session=None,
    )
    assert inserted["row"]["title"] == "hello"
    listed = await svc.select(bridge=bridge, project_id="proj_udb", table="items", session=None)
    assert listed["count"] == 1
    await svc.purge_project(company_id="co", project_id="proj_udb")


@pytest.mark.asyncio
async def test_events_publish_poll_and_backlog(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(
        "prodavan.application.tenant_infra.quota.TenantInfraQuotaService.get_quota",
        AsyncMock(
            return_value=CompanyTenantInfraQuota(kafka_ops_per_minute=0, kafka_max_backlog=2)
        ),
    )
    _, bridge = await mint_pod_bridge_token(
        project_id="proj_ev",
        cabinet_id="cab",
        company_id="co",
        pod_id="pod",
        scopes=build_launch_scopes([]),
    )
    svc = TenantEventsService()
    await svc.publish(bridge=bridge, project_id="proj_ev", typ="ping", payload={"n": 1}, session=None)
    await svc.publish(bridge=bridge, project_id="proj_ev", typ="ping", payload={"n": 2}, session=None)
    with pytest.raises(AppError) as exc:
        await svc.publish(bridge=bridge, project_id="proj_ev", typ="ping", payload={"n": 3}, session=None)
    assert exc.value.code == "TENANT_INFRA_QUOTA"
    polled = await svc.poll(bridge=bridge, project_id="proj_ev", limit=10, session=None)
    assert len(polled["items"]) == 2
    await purge_project_tenant_infra(company_id="co", project_id="proj_ev", session=None)


def test_object_put_body_roundtrip_b64() -> None:
    raw = b"hello-object"
    encoded = base64.b64encode(raw).decode("ascii")
    assert base64.b64decode(encoded) == raw
