"""Read path — batch online checks for list/metrics endpoints."""

from __future__ import annotations

from prodavan.application.metrics.presence_store import batch_is_online, count_online


class MetricsReadService:
    async def employees_online(self, employee_ids: list[str]) -> int:
        return await count_online("employee", employee_ids)

    async def batch_employee_online(self, employee_ids: list[str]) -> dict[str, bool]:
        return await batch_is_online("employee", employee_ids)

    async def batch_company_online(self, company_login_keys: list[str]) -> dict[str, bool]:
        return await batch_is_online("company", company_login_keys)

    async def is_employee_online(self, employee_id: str) -> bool:
        return (await self.batch_employee_online([employee_id])).get(employee_id, False)

    async def is_company_online(self, company_login_key: str) -> bool:
        return (await self.batch_company_online([company_login_key])).get(company_login_key, False)
