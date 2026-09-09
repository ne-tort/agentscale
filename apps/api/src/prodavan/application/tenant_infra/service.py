"""Tenant Infra Cache service — rewrite, quotas, events."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_CACHE, PodBridgeClaims
from prodavan.application.tenant_infra.adapters.memory_cache import InMemoryTenantCache
from prodavan.application.tenant_infra.adapters.redis_cache import RedisTenantCache
from prodavan.application.tenant_infra.keys import rewrite_cache_key
from prodavan.application.tenant_infra.ports.cache import TenantCachePort
from prodavan.application.tenant_infra.publish import emit_cache_op_metric, emit_tenant_infra_event
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key, rate_limit_enforce
from prodavan.domain.errors import AppError


def _default_cache() -> TenantCachePort:
    from prodavan.core.infra.redis_manager import get_redis_manager

    mgr = get_redis_manager()
    if mgr is not None and mgr.enabled:
        return RedisTenantCache()
    return InMemoryTenantCache()


class TenantInfraService:
    def __init__(self, cache: TenantCachePort | None = None) -> None:
        self._cache: TenantCachePort = cache if cache is not None else _default_cache()

    def _require_bridge(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_CACHE)

    async def _enforce_quota(self, bridge: PodBridgeClaims) -> None:
        limit = int(settings.tenant_infra_cache_ops_per_minute or 0)
        if limit < 1:
            return
        await rate_limit_enforce(
            cache_key("rl", "tenant_infra", "cache", bridge.project_id),
            limit=limit,
            window_sec=60,
            detail="tenant infra cache rate limit exceeded",
        )

    def _check_value_size(self, value: str) -> None:
        max_b = int(settings.tenant_infra_cache_max_value_bytes or 0)
        if max_b > 0 and len(value.encode("utf-8")) > max_b:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"cache value exceeds {max_b} bytes",
            )

    async def get(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        await self._enforce_quota(bridge)
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
        await self._enforce_quota(bridge)
        self._check_value_size(value)
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        ok = await self._cache.set(physical, value, ttl_sec=ttl_sec)
        if not ok:
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="tenant cache unavailable",
            )
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "cache.set", "ttl_sec": ttl_sec},
        )
        await emit_cache_op_metric(
            session=session,
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            op="set",
        )
        return {"key": key, "ok": True}

    async def delete(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        key: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_bridge(bridge, project_id)
        await self._enforce_quota(bridge)
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        await self._cache.delete(physical)
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
        await self._enforce_quota(bridge)
        physical = rewrite_cache_key(
            company_id=bridge.company_id, project_id=bridge.project_id, user_key=key
        )
        value = await self._cache.incr(physical, amount=amount)
        if value is None:
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="tenant cache unavailable",
            )
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
