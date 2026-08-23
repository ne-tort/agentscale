"""Company cabinet quota read + enforce (L04 → L06)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.admin import DEFAULT_CABINET_QUOTA, CompanyCabinetQuota
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.admin import CompanyCabinetQuotaRow
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow


class CompanyQuotaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_quota(self, company_id: str) -> CompanyCabinetQuota:
        row = await self._session.get(CompanyCabinetQuotaRow, company_id)
        if row is None:
            return DEFAULT_CABINET_QUOTA
        return row.to_domain()

    async def count_active_cabinets(self, company_id: str) -> int:
        q = await self._session.execute(
            select(func.count())
            .select_from(CabinetInstanceRow)
            .where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return int(q.scalar_one() or 0)

    async def assert_can_create_cabinet(self, company_id: str) -> None:
        quota = await self.get_quota(company_id)
        active = await self.count_active_cabinets(company_id)
        if active >= quota.max_cabinets:
            raise AppError(
                code="CABINET_QUOTA",
                title="Cabinet quota exceeded",
                status=409,
                detail=f"max {quota.max_cabinets} active cabinets for company",
            )

    async def assert_can_add_package(self, company_id: str, *, active_count: int) -> None:
        quota = await self.get_quota(company_id)
        if active_count >= quota.max_packages_per_cabinet:
            raise AppError(
                code="PACKAGE_QUOTA",
                title="Package quota exceeded",
                status=409,
                detail=f"max {quota.max_packages_per_cabinet} active packages per cabinet",
            )

    async def assert_bundle_size(self, company_id: str, *, size_bytes: int) -> None:
        quota = await self.get_quota(company_id)
        limit_bytes = quota.max_bundle_import_mb * 1024 * 1024
        if size_bytes > limit_bytes:
            raise AppError(
                code="BUNDLE_QUOTA",
                title="Bundle size quota exceeded",
                status=413,
                detail=f"max {quota.max_bundle_import_mb} MB bundle import for company",
            )
