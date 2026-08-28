"""Unit tests for project_service access policy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal
from prodavan.domain.projects import ProjectVisibilityMode


def _project(**kwargs):
    row = MagicMock()
    row.id = kwargs.get("id", "proj_1")
    row.cabinet_id = kwargs.get("cabinet_id", "cab_1")
    row.company_id = kwargs.get("company_id", "co_1")
    row.visibility_mode = kwargs.get("visibility_mode", ProjectVisibilityMode.CABINET_SHARED)
    row.status = kwargs.get("status", "active")
    return row


def _deny_company_admin():
    return patch(
        "prodavan.application.identity.service.EntitlementService.require_company_admin",
        new=AsyncMock(side_effect=AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="no")),
    )


@pytest.mark.asyncio
async def test_shared_project_visible_with_cabinet_assignment():
    session = AsyncMock()
    policy = ProjectAccessPolicy(session)
    project = _project()
    employee = MagicMock(id="emp_1")
    principal = Principal(sub="sub1")

    with (
        _deny_company_admin(),
        patch.object(policy._relations, "has_cabinet_assignment", new=AsyncMock(return_value=True)),
        patch.object(policy._relations, "has_project_assignment", new=AsyncMock(return_value=False)),
    ):
        assert await policy.can_view_project(project=project, principal=principal, employee=employee)


@pytest.mark.asyncio
async def test_restricted_project_hidden_without_assignment():
    session = AsyncMock()
    policy = ProjectAccessPolicy(session)
    project = _project(visibility_mode=ProjectVisibilityMode.RESTRICTED)
    employee = MagicMock(id="emp_1")
    principal = Principal(sub="sub1")

    with (
        _deny_company_admin(),
        patch.object(policy._relations, "has_cabinet_assignment", new=AsyncMock(return_value=True)),
        patch.object(policy._relations, "has_project_assignment", new=AsyncMock(return_value=False)),
    ):
        assert not await policy.can_view_project(project=project, principal=principal, employee=employee)


@pytest.mark.asyncio
async def test_restricted_project_visible_with_assignment():
    session = AsyncMock()
    policy = ProjectAccessPolicy(session)
    project = _project(visibility_mode=ProjectVisibilityMode.RESTRICTED)
    employee = MagicMock(id="emp_1")
    principal = Principal(sub="sub1")

    with (
        _deny_company_admin(),
        patch.object(policy._relations, "has_cabinet_assignment", new=AsyncMock(return_value=True)),
        patch.object(policy._relations, "has_project_assignment", new=AsyncMock(return_value=True)),
    ):
        assert await policy.can_view_project(project=project, principal=principal, employee=employee)


@pytest.mark.asyncio
async def test_platform_admin_bypasses_restricted():
    session = AsyncMock()
    policy = ProjectAccessPolicy(session)
    project = _project(visibility_mode=ProjectVisibilityMode.RESTRICTED)
    principal = Principal(sub="admin", roles=frozenset({ROLE_PLATFORM_ADMIN}))

    assert await policy.can_view_project(project=project, principal=principal, employee=None)
