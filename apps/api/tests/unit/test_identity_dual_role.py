"""Unit tests — dual-role platform.admin resolves employee by sub (L01)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.identity.service import EntitlementService
from prodavan.domain.identity import EmployeeStatus, Principal


@pytest.mark.asyncio
async def test_ensure_active_employee_admin_loads_by_sub_without_email() -> None:
    session = AsyncMock()
    svc = EntitlementService(session)
    emp = MagicMock()
    emp.status = EmployeeStatus.ACTIVE
    emp.id = "emp_1"
    svc.get_employee_by_sub = AsyncMock(return_value=emp)  # type: ignore[method-assign]
    svc._bind_invited_by_email = AsyncMock(return_value=None)  # type: ignore[method-assign]

    principal = Principal(sub="admin-sub", email=None, roles=frozenset({"platform.admin"}))
    # is_platform_admin derived from roles
    assert principal.is_platform_admin

    got = await svc.ensure_active_employee(principal)
    assert got is emp
    svc.get_employee_by_sub.assert_awaited_once_with("admin-sub")
    svc._bind_invited_by_email.assert_not_awaited()
