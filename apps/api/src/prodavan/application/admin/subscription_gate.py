"""Company subscription gate for runtime (L04 → L07/L08)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.admin import subscription_read_model
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow

_SUBSCRIPTION_TRANSITION_EVENTS = frozenset({"company.suspended", "company.reactivated"})


class CompanySubscriptionGate:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _company_row(self, company_id: str) -> CompanyRow:
        company = await self._session.get(CompanyRow, company_id)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return company

    def _read_model(self, company: CompanyRow, *, now: datetime | None = None) -> dict[str, object]:
        return subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=now or datetime.now(UTC),
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )

    async def _latest_subscription_transition(self, company_id: str) -> str | None:
        q = await self._session.execute(
            select(PlatformEventRow.event_type)
            .where(
                PlatformEventRow.company_id == company_id,
                PlatformEventRow.event_type.in_(_SUBSCRIPTION_TRANSITION_EVENTS),
            )
            .order_by(PlatformEventRow.created_at.desc())
            .limit(1)
        )
        return q.scalar_one_or_none()

    async def _emit_suspended(
        self,
        company_id: str,
        subscription: dict[str, object],
        *,
        principal: Principal | None = None,
    ) -> None:
        from prodavan.application.projects.pause_runtime import stop_company_runtime
        from prodavan.application.projects.platform_event_service import PlatformEventService

        # Stop in-flight agent runtime (same surface as project pause).
        cancelled = await stop_company_runtime(self._session, company_id=company_id)
        await PlatformEventService(self._session).emit(
            event_type="company.suspended",
            company_id=company_id,
            principal=principal,
            payload={
                "subscription_ends_at": subscription.get("subscription_ends_at"),
                "reason": "subscription_expired",
                "sessions_cancelled": cancelled,
            },
        )

    async def _emit_reactivated(
        self,
        company_id: str,
        subscription: dict[str, object],
        *,
        principal: Principal | None = None,
    ) -> None:
        from prodavan.application.projects.platform_event_service import PlatformEventService

        await PlatformEventService(self._session).emit(
            event_type="company.reactivated",
            company_id=company_id,
            principal=principal,
            payload={
                "subscription_ends_at": subscription.get("subscription_ends_at"),
                "subscription_lifetime": subscription.get("subscription_lifetime"),
                "reason": "subscription_renewed",
            },
        )

    async def ensure_suspended_platform_event(
        self,
        company_id: str,
        subscription: dict[str, object],
        *,
        principal: Principal | None = None,
    ) -> bool:
        """Emit company.suspended once per expire cycle (admin PUT or natural expiry)."""
        if not subscription.get("subscription_expired"):
            return False
        if await self._latest_subscription_transition(company_id) == "company.suspended":
            return False
        await self._emit_suspended(company_id, subscription, principal=principal)
        # Persist even on read paths (GET project) — session has no auto-commit (SSE).
        await self._session.commit()
        return True

    async def emit_transition_events(
        self,
        company_id: str,
        *,
        was_expired: bool,
        now_expired: bool,
        subscription: dict[str, object],
        principal: Principal | None = None,
    ) -> None:
        if now_expired and not was_expired:
            await self._emit_suspended(company_id, subscription, principal=principal)
        elif not now_expired and was_expired:
            await self._emit_reactivated(company_id, subscription, principal=principal)

    async def subscription_state(
        self,
        company_id: str,
        *,
        principal: Principal | None = None,
    ) -> dict[str, object]:
        company = await self._company_row(company_id)
        state = self._read_model(company)
        await self.ensure_suspended_platform_event(company_id, state, principal=principal)
        return state

    async def require_active(self, company_id: str, *, principal: Principal | None = None) -> None:
        state = await self.subscription_state(company_id, principal=principal)
        if state.get("subscription_expired"):
            from prodavan.application.projects.pause_runtime import stop_company_runtime

            # Idempotent: cleans leftovers if suspend event already existed before cancel wiring.
            await stop_company_runtime(self._session, company_id=company_id)
            raise AppError(
                code="COMPANY_SUSPENDED",
                title="Company suspended",
                status=403,
                detail="company subscription expired",
            )
