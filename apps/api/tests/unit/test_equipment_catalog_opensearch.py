"""Unit tests — equipment catalog → OpenSearch helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE,
    catalog_doc_id,
    catalog_os_index_name,
    column_map_ready,
    enqueue_or_run_index_equipment_catalog,
)


def test_catalog_os_index_name_stable() -> None:
    assert catalog_os_index_name("row-ABC").startswith("c_")
    assert catalog_os_index_name("123") == "c_r_123" or catalog_os_index_name("123").startswith(
        "c_"
    )


def test_catalog_doc_id_deterministic() -> None:
    a = catalog_doc_id("cat1", "pn:SSD-1")
    b = catalog_doc_id("cat1", "pn:SSD-1")
    c = catalog_doc_id("cat1", "pn:OTHER")
    assert a == b
    assert a != c
    assert len(a) == 40


def test_column_map_ready_requires_title_price() -> None:
    assert not column_map_ready({"column_map": {"title": "Name"}})
    assert column_map_ready(
        {"column_map": {"title": "Name", "price": "Cost", "brand": "Brand"}}
    )


def test_enqueue_inline_when_celery_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.infra.worker_manager.get_worker_manager",
        lambda: None,
    )
    out = enqueue_or_run_index_equipment_catalog(
        instance_id="inst1",
        row_id="row1",
        company_id="co1",
    )
    assert out["inline"] is True
    assert out["enqueued"] is False


@pytest.mark.asyncio
async def test_delete_equipment_catalog_index_calls_service(monkeypatch) -> None:
    from prodavan.application.modules import equipment_catalog_opensearch as mod

    svc = MagicMock()
    svc.delete_index = AsyncMock(return_value=True)
    monkeypatch.setattr(mod, "get_search_index_service", lambda: svc)
    ok = await mod.delete_equipment_catalog_index(
        row_id="abc", company_id="co1", project_id="p1"
    )
    assert ok is True
    svc.delete_index.assert_awaited_once()
    kwargs = svc.delete_index.await_args.kwargs
    assert kwargs["namespace"] == OS_NAMESPACE
    assert kwargs["index"] == catalog_os_index_name("abc")
