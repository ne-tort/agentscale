"""Resolve auth event payload to presence principal (employee or company)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.identity.service import EntitlementService
from prodavan.application.metrics.presence_store import PresenceKind
from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE


@dataclass(frozen=True, slots=True)
class ResolvedPresence:
    kind: PresenceKind
    entity_id: str


class PresenceResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._entitlements = EntitlementService(session)

    async def resolve_auth_payload(self, payload: dict[str, Any]) -> ResolvedPresence | None:
        """Map auth.login/logout payload to Redis presence key target."""
        sub = str(payload.get("sub") or "").strip()
        if not sub:
            return None
        roles = {str(r) for r in (payload.get("roles") or [])}
        username = str(payload.get("username") or "").strip()
        employee_id = str(payload.get("employee_id") or "").strip()

        if employee_id:
            return ResolvedPresence(kind="employee", entity_id=employee_id)

        if ROLE_COMPANY in roles:
            login = username or sub
            if not login:
                return None
            return ResolvedPresence(kind="company", entity_id=login)

        if ROLE_EMPLOYEE in roles or not roles:
            # Logout often omits roles — resolve employee by sub as fallback.
            emp = await self._entitlements.get_employee_by_sub(sub)
            if emp is not None:
                return ResolvedPresence(kind="employee", entity_id=emp.id)
            if ROLE_EMPLOYEE in roles:
                return None

        if not roles:
            login = username or sub
            return ResolvedPresence(kind="company", entity_id=login)

        return None
