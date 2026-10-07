"""Tenant Infra Cache service — rewrite, quotas, TTL, key index, purge."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_CACHE, PodBridgeClaims
from prodavan.application.tenant_infra.adapters.memory_cache import (
    get_shared_memory_tenant_cache,
)
from prodavan.application.tenant_infra.adapters.redis_cache import RedisTenantCache
from prodavan.application.tenant_infra.keys import cache_index_key, rewrite_cache_key
from prodavan.application.tenant_infra.ports.cache import TenantCachePort
from prodavan.application.tenant_infra.publish import emit_cache_op_metric, emit_tenant_infra_event
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService, enforce_ops_rate, quota_exceeded
from prodavan.domain.errors import AppError


def _default_cache() -> TenantCachePort:
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is not None and mgr.enabled:
        return RedisTenantCache()
    return get_shared_memory_tenant_cache()


class TenantInfraService:
    def __init__(self, cache: TenantCachePort | None = None) -> None:
        self._cache: TenantCachePort = cache if cache is not None else _default_cache()

    def _require_bridge(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_CACHE)

    async def _quota(self, bridge: PodBridgeClaims, session: AsyncSession | None):
        return await TenantInfraQuotaService(session).get_quota(bridge.company_id)

    async def get(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="cache",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.cache_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        value = await self._cache.get(physical)
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "cache.get", "hit": value is not None},
        )
        await emit_cache_op_metric(
            session=session,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            op="get",
        )
        return {"key": key, "value": value, "found": value is not None}

    async def set(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        value: str,
        ttl_sec: int | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="cache",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.cache_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        if len(value.encode("utf-8")) > quota.cache_max_value_bytes:
            raise quota_exceeded(f"cache value exceeds {quota.cache_max_value_bytes} bytes")
        effective_ttl = int(ttl_sec) if ttl_sec is not None else int(quota.cache_default_ttl_sec)
        if effective_ttl < 1:
            effective_ttl = int(quota.cache_default_ttl_sec)
        if effective_ttl > quota.cache_max_ttl_sec:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"ttl_sec exceeds max {quota.cache_max_ttl_sec}",
            )
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        index = cache_index_key(company_id=bridge.company_id, project_id=bridge.project_id)
        exists = await self._cache.get(physical)
        if exists is None:
            card = await self._cache.index_card(index)
            if card >= quota.cache_max_keys:
                raise quota_exceeded(f"cache max keys {quota.cache_max_keys} exceeded")
        ok = await self._cache.set(physical, value, ttl_sec=effective_ttl)
        if not ok:
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="tenant cache unavailable",
            )
        await self._cache.index_add(index, physical)
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "cache.set", "ttl_sec": effective_ttl},
        )
        await emit_cache_op_metric(
            session=session,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            op="set",
        )
        return {"key": key, "ok": True, "ttl_sec": effective_ttl}

    async def delete(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="cache",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.cache_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        index = cache_index_key(company_id=bridge.company_id, project_id=bridge.project_id)
        await self._cache.delete(physical)
        await self._cache.index_remove(index, physical)
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "cache.del"},
        )
        await emit_cache_op_metric(
            session=session,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            op="del",
        )
        return {"key": key, "ok": True}

    async def incr(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        amount: int = 1,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="cache",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.cache_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        index = cache_index_key(company_id=bridge.company_id, project_id=bridge.project_id)
        existed = await self._cache.get(physical)
        if existed is None:
            card = await self._cache.index_card(index)
            if card >= quota.cache_max_keys:
                raise quota_exceeded(f"cache max keys {quota.cache_max_keys} exceeded")
        value = await self._cache.incr(
            physical, amount=amount, ttl_sec=quota.cache_default_ttl_sec
        )
        if value is None:
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="tenant cache unavailable",
            )
        await self._cache.index_add(index, physical)
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "cache.incr", "amount": amount},
        )
        await emit_cache_op_metric(
            session=session,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            op="incr",
        )
        return {"key": key, "value": value}

    async def purge_project(
        self,
        *,
        company_id: str,
        project_id: str,
    ) -> int:
        index = cache_index_key(company_id=company_id, project_id=project_id)
        members = await self._cache.index_members(index)
        deleted = await self._cache.purge_keys(members + [index])
        return deleted
