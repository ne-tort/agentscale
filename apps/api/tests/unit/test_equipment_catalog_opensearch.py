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
async def test_run_index_emits_kafka_accepted_and_completed(monkeypatch) -> None:
    from prodavan.application.modules import equipment_catalog_opensearch as mod

    accepted: list[dict] = []
    completed: list[dict] = []

    async def _accepted(**kwargs):  # noqa: ANN003
        accepted.append(kwargs)

    async def _completed(**kwargs):  # noqa: ANN003
        completed.append(kwargs)

    monkeypatch.setattr(
        "prodavan.application.search_index.publish.emit_equipment_catalog_index_accepted",
        _accepted,
    )
    monkeypatch.setattr(
        "prodavan.application.search_index.publish.emit_equipment_catalog_index_completed",
        _completed,
    )

    body = {
        "name": "cat",
        "source_kind": "local",
        "column_map": {"title": "Name", "price": "Cost"},
        "source_file": {"blob_id": "b1"},
    }
    row = {"body": body}
    inst = MagicMock()
    inst.get_data_row = AsyncMock(return_value=row)
    inst.upsert_data_row = AsyncMock()
    session = MagicMock()
    session.commit = AsyncMock()

    monkeypatch.setattr(mod, "ModuleInstanceService", lambda _s: inst)
    monkeypatch.setattr(mod, "_index_local_rows", AsyncMock(return_value=3))

    svc = MagicMock()
    svc.delete_index = AsyncMock(return_value=True)
    svc.ensure_index = AsyncMock()
    monkeypatch.setattr(mod, "get_search_index_service", lambda: svc)

    out = await mod.run_index_equipment_catalog(
        session,
        instance_id="inst1",
        row_id="row1",
        company_id="co1",
        project_id="p1",
    )
    assert out["ok"] is True
    assert out["indexed"] == 3
    assert len(accepted) == 1
    assert accepted[0]["catalog_row_id"] == "row1"
    assert accepted[0]["index"] == catalog_os_index_name("row1")
    assert len(completed) == 1
    assert completed[0]["ok"] is True
    assert completed[0]["indexed"] == 3
    assert inst.upsert_data_row.await_count >= 2
    last_body = inst.upsert_data_row.await_args_list[-1].kwargs["body"]
    assert last_body["status"] == "ready"
