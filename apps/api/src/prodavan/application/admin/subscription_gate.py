"""Company subscription gate for runtime (L04 → L07/L08)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.admin import subscription_read_model
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import CompanyRow


class CompanySubscriptionGate:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def subscription_state(self, company_id: str) -> dict[str, object]:
        company = await self._session.get(CompanyRow, company_id)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=datetime.now(UTC),
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )

    async def require_active(self, company_id: str) -> None:
        state = await self.subscription_state(company_id)
        if state.get("subscription_expired"):
            raise AppError(
                code="COMPANY_SUSPENDED",
                title="Company suspended",
                status=403,
                detail="company subscription expired",
            )
