"""Resolve and enforce CompanyTenantInfraQuota."""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_runtime_cache import (
    get_cached_tenant_infra_quota,
    invalidate_company_runtime_cache,
    set_cached_tenant_infra_quota,
)
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key, rate_limit_enforce
from prodavan.domain.admin import DEFAULT_TENANT_INFRA_QUOTA, CompanyTenantInfraQuota
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.admin import CompanyTenantInfraQuotaRow


def _settings_overlay(base: CompanyTenantInfraQuota) -> CompanyTenantInfraQuota:
    """Settings remain fallback defaults when company row absent."""
    return CompanyTenantInfraQuota(
        cache_ops_per_minute=int(settings.tenant_infra_cache_ops_per_minute or base.cache_ops_per_minute),
        cache_max_keys=base.cache_max_keys,
        cache_max_value_bytes=int(
            settings.tenant_infra_cache_max_value_bytes or base.cache_max_value_bytes
        ),
        cache_default_ttl_sec=base.cache_default_ttl_sec,
        cache_max_ttl_sec=base.cache_max_ttl_sec,
        docs_ops_per_minute=base.docs_ops_per_minute,
        docs_max_collections=base.docs_max_collections,
        docs_max_docs_per_collection=base.docs_max_docs_per_collection,
        docs_max_doc_bytes=base.docs_max_doc_bytes,
        userdb_ops_per_minute=base.userdb_ops_per_minute,
        userdb_max_tables=base.userdb_max_tables,
        userdb_max_rows_per_table=base.userdb_max_rows_per_table,
        userdb_max_row_bytes=base.userdb_max_row_bytes,
        kafka_ops_per_minute=base.kafka_ops_per_minute,
        kafka_max_payload_bytes=base.kafka_max_payload_bytes,
        kafka_max_backlog=base.kafka_max_backlog,
        kafka_retention_sec=base.kafka_retention_sec,
        objects_ops_per_minute=base.objects_ops_per_minute,
        objects_max_per_project=base.objects_max_per_project,
        objects_max_bytes=base.objects_max_bytes,
    )


class TenantInfraQuotaService:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_quota(self, company_id: str) -> CompanyTenantInfraQuota:
        cached = await get_cached_tenant_infra_quota(company_id)
        if cached is not None:
            return cached
        if self._session is None:
            return _settings_overlay(DEFAULT_TENANT_INFRA_QUOTA)
        row = await self._session.get(CompanyTenantInfraQuotaRow, company_id)
        quota = row.to_domain() if row is not None else _settings_overlay(DEFAULT_TENANT_INFRA_QUOTA)
        await set_cached_tenant_infra_quota(company_id, quota)
        return quota

    async def set_quota(self, company_id: str, quota: CompanyTenantInfraQuota) -> CompanyTenantInfraQuota:
        if self._session is None:
            raise AppError(code="INTERNAL", title="Internal", status=500, detail="session required")
        quota.validate()
        row = await self._session.get(CompanyTenantInfraQuotaRow, company_id)
        data = asdict(quota)
        if row is None:
            row = CompanyTenantInfraQuotaRow(company_id=company_id, **data)
            self._session.add(row)
        else:
            for key, value in data.items():
                setattr(row, key, value)
        await self._session.commit()
        await self._session.refresh(row)
        await invalidate_company_runtime_cache(company_id)
        await set_cached_tenant_infra_quota(company_id, row.to_domain())
        return row.to_domain()


async def enforce_ops_rate(
    *,
    plane: str,
    company_id: str,
    project_id: str,
    limit: int,
    acting_employee_id: str | None = None,
) -> None:
    if limit < 1:
        return
    await rate_limit_enforce(
        cache_key("rl", "tenant_infra", plane, project_id),
        limit=limit,
        window_sec=60,
        detail=f"tenant infra {plane} rate limit exceeded",
    )
    if acting_employee_id:
        # Soft secondary cap: half of project limit per acting employee.
        emp_limit = max(1, limit // 2)
        await rate_limit_enforce(
            cache_key("rl", "tenant_infra", plane, project_id, "emp", acting_employee_id),
            limit=emp_limit,
            window_sec=60,
            detail=f"tenant infra {plane} employee rate limit exceeded",
        )


def quota_exceeded(detail: str) -> AppError:
    return AppError(
        code="TENANT_INFRA_QUOTA",
        title="Tenant Infra Quota Exceeded",
        status=429,
        detail=detail,
    )
