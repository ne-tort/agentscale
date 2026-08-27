"""Unit tests — cabinet hard-delete preconditions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_hard_delete_requires_soft_deleted_for_employee(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    svc = CabinetInstanceService(session)
    inst = SimpleNamespace(
        id="cab_x",
        status=CabinetStatus.ACTIVE,
        schema_name="cab_inst_x",
    )

    async def _access(**_k):
        return inst

    monkeypatch.setattr(svc._access, "require_access", _access)
    with pytest.raises(AppError) as ei:
        await svc.hard_delete(
            cabinet_id="cab_x",
            principal=SimpleNamespace(is_platform_admin=False),
            employee=SimpleNamespace(id="emp_1"),
        )
    assert ei.value.code == "CABINET_NOT_SOFT_DELETED"
    assert ei.value.status == 409
