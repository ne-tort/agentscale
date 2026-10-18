"""WAVE16: удаление строки виртуальной таблицы (Поиск/Мастер-прайс) из OS."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.modules.equipment_master_price import delete_virtual_row


class _FakeSearchSvc:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    async def delete_document(self, *, namespace, index, doc_id, company_id=None, **kw):
        self.deleted.append((index, doc_id))
        return doc_id == "abc123"


class _FakeSession:
    async def execute(self, q):
        class _R:
            def scalar_one_or_none(self):
                return "co_1"
        return _R()


@pytest.mark.asyncio
async def test_delete_virtual_row_hits_os() -> None:
    svc = _FakeSearchSvc()
    with (
        patch(
            "prodavan.core.infra.opensearch_manager.get_search_index_service",
            return_value=svc,
        ),
        patch(
            "prodavan.application.modules.equipment_master_price.MasterPriceService._ready_indexes",
            AsyncMock(return_value=["c_row_1", "c_row_2"]),
        ),
    ):
        out = await delete_virtual_row(_FakeSession(), cabinet_id="cab_1", row_id="os:abc123")
    assert out == {"ok": True, "deleted": 2}
    assert svc.deleted == [("c_row_1", "abc123"), ("c_row_2", "abc123")]


@pytest.mark.asyncio
async def test_delete_virtual_row_rejects_bad_id() -> None:
    with pytest.raises(Exception):
        await delete_virtual_row(_FakeSession(), cabinet_id="cab_1", row_id="row_1")


@pytest.mark.asyncio
async def test_delete_virtual_row_404_when_missing() -> None:
    class _Svc(_FakeSearchSvc):
        async def delete_document(self, **kw):
            return False

    with (
        patch(
            "prodavan.core.infra.opensearch_manager.get_search_index_service",
            return_value=_Svc(),
        ),
        patch(
            "prodavan.application.modules.equipment_master_price.MasterPriceService._ready_indexes",
            AsyncMock(return_value=["c_row_1"]),
        ),
    ):
        with pytest.raises(Exception):
            await delete_virtual_row(_FakeSession(), cabinet_id="cab_1", row_id="os:missing")
