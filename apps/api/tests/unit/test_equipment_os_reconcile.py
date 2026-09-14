"""Unit tests — equipment catalog OS reconcile + project_allowed."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE,
    catalog_os_index_name,
    reconcile_orphan_equipment_indexes,
)
from prodavan.application.tenant_infra.equipment_catalog_search_service import (
    _project_allowed,
)
from prodavan.domain.search_index.types import physical_index


def test_project_allowed_empty_means_all() -> None:
    assert _project_allowed({}, "p1") is True
    assert _project_allowed({"project_ids": []}, "p1") is True
    assert _project_allowed({"project_ids": ["p1", "p2"]}, "p1") is True
    assert _project_allowed({"project_ids": ["p2"]}, "p1") is False


@pytest.mark.asyncio
async def test_reconcile_deletes_orphan_indexes() -> None:
    live_row = SimpleNamespace(
        row_id="row_live",
        body={"index_name": physical_index(OS_NAMESPACE, catalog_os_index_name("row_live"))},
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [live_row]))
    )

    orphan = physical_index(OS_NAMESPACE, catalog_os_index_name("row_orphan"))
    live = physical_index(OS_NAMESPACE, catalog_os_index_name("row_live"))

    svc = MagicMock()
    svc.list_indexes = AsyncMock(return_value=[live, orphan])
    svc.delete_index = AsyncMock(return_value=True)
    svc._store = MagicMock()
    svc._store.delete_index = AsyncMock(return_value=True)

    with patch(
        "prodavan.application.modules.equipment_catalog_opensearch.get_search_index_service",
        return_value=svc,
    ):
        result = await reconcile_orphan_equipment_indexes(session=session, company_id="co1")

    assert orphan in result["deleted"]
    assert result["kept"] == 1
    svc.delete_index.assert_awaited()
