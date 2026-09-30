"""Unit tests — equipment catalog → OpenSearch helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.equipment_catalog_opensearch import (
    OS_NAMESPACE,
    PLATFORM_OS_COMPANY_ID,
    catalog_doc_id,
    catalog_os_index_name,
    column_map_ready,
    enqueue_or_run_index_equipment_catalog,
    resolve_equipment_catalog_tenancy,
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


@pytest.mark.asyncio
async def test_resolve_tenancy_platform_uses_sentinel() -> None:
    session = MagicMock()
    session.get = AsyncMock(
        return_value=MagicMock(owner_kind="platform", owner_id="platform")
    )
    company_id, cabinet_id, project_id = await resolve_equipment_catalog_tenancy(
        session, instance_id="inst_1"
    )
    assert company_id == PLATFORM_OS_COMPANY_ID
    assert cabinet_id is None
    assert project_id is None


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
    snapshots: list[dict] = []

    async def _upsert(**kwargs):  # noqa: ANN003
        snapshots.append(dict(kwargs.get("body") or {}))

    inst.upsert_data_row = AsyncMock(side_effect=_upsert)
    session = MagicMock()
    session.commit = AsyncMock()

    monkeypatch.setattr(mod, "ModuleInstanceService", lambda _s: inst)

    source = MagicMock()
    source.total_rows = 3
    source.aclose = AsyncMock()
    monkeypatch.setattr(mod, "_open_local_source", AsyncMock(return_value=source))
    monkeypatch.setattr(mod, "_index_opened_source", AsyncMock(return_value=(3, {"OCS"})))

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
    bodies = snapshots
    assert bodies[0]["status"] == "indexing"
    assert bodies[0]["indexed_count"] == 0
    assert bodies[0]["indexing_started_at"]
    # total_rows discovered at source open, before any bulk chunk
    assert bodies[1]["total_rows"] == 3
    # the ready snapshot (the suppliers auto-fill may append more bodies after)
    ready_bodies = [b for b in bodies if b.get("status") == "ready"]
    assert ready_bodies, bodies
    last_body = ready_bodies[-1]
    assert last_body["indexed_count"] == 3
    assert last_body["total_rows"] == 3


@pytest.mark.asyncio
async def test_run_index_source_failure_keeps_old_index(monkeypatch) -> None:
    """Source-first: unreachable source must NOT delete the existing index."""
    from prodavan.application.modules import equipment_catalog_opensearch as mod

    completed: list[dict] = []

    async def _accepted(**kwargs):  # noqa: ANN003
        pass

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

    row = {
        "body": {
            "name": "cat",
            "source_kind": "local",
            "column_map": {"title": "Name", "price": "Cost"},
            "source_file": {"storage_key": "k"},
        }
    }
    inst = MagicMock()
    inst.get_data_row = AsyncMock(return_value=row)
    inst.upsert_data_row = AsyncMock()
    session = MagicMock()
    session.commit = AsyncMock()
    monkeypatch.setattr(mod, "ModuleInstanceService", lambda _s: inst)

    async def _boom(_body):
        raise ValueError("source unreachable")

    monkeypatch.setattr(mod, "_open_local_source", _boom)

    svc = MagicMock()
    svc.delete_index = AsyncMock(return_value=True)
    svc.ensure_index = AsyncMock()
    monkeypatch.setattr(mod, "get_search_index_service", lambda: svc)

    out = await mod.run_index_equipment_catalog(
        session, instance_id="inst1", row_id="row1", company_id="co1"
    )
    assert out["ok"] is False
    # the old index is NOT touched when the source cannot be opened
    svc.delete_index.assert_not_awaited()
    svc.ensure_index.assert_not_awaited()
    last_body = inst.upsert_data_row.await_args_list[-1].kwargs["body"]
    assert last_body["status"] == "error"
    assert "source unreachable" in last_body["error"]
    assert completed and completed[0]["ok"] is False


@pytest.mark.asyncio
async def test_run_index_progress_heartbeats_persisted(monkeypatch) -> None:
    """on_progress persists indexed_count/total_rows after each bulk chunk."""
    from prodavan.application.modules import equipment_catalog_opensearch as mod

    row = {
        "body": {
            "name": "cat",
            "source_kind": "local",
            "column_map": {"title": "Name", "price": "Cost"},
            "source_file": {"storage_key": "k"},
        }
    }
    inst = MagicMock()
    inst.get_data_row = AsyncMock(return_value=row)
    snapshots: list[dict] = []

    async def _upsert(**kwargs):  # noqa: ANN003
        snapshots.append(dict(kwargs.get("body") or {}))

    inst.upsert_data_row = AsyncMock(side_effect=_upsert)
    session = MagicMock()
    session.commit = AsyncMock()
    monkeypatch.setattr(mod, "ModuleInstanceService", lambda _s: inst)

    monkeypatch.setattr(
        "prodavan.application.search_index.publish.emit_equipment_catalog_index_accepted",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "prodavan.application.search_index.publish.emit_equipment_catalog_index_completed",
        AsyncMock(),
    )

    class _Src:
        total_rows = 10

        async def aiter_rows(self):
            for i in range(10):
                yield {"Name": f"item {i}", "Cost": "1"}

        async def aclose(self):
            return None

    monkeypatch.setattr(mod, "_open_local_source", AsyncMock(return_value=_Src()))

    svc = MagicMock()

    async def _bulk(**kwargs):
        return MagicMock(indexed=len(kwargs.get("documents") or []))

    svc.bulk_index = AsyncMock(side_effect=_bulk)
    svc.delete_index = AsyncMock(return_value=True)
    svc.ensure_index = AsyncMock()
    svc.search = AsyncMock()
    monkeypatch.setattr(mod, "get_search_index_service", lambda: svc)

    out = await mod.run_index_equipment_catalog(
        session, instance_id="inst1", row_id="row1", company_id="co1"
    )
    assert out["ok"] is True
    assert out["indexed"] == 10
    bodies = snapshots
    # stamps: indexing + total discovery + final ready (batch smaller than
    # MAX_BULK_BATCH → single final heartbeat)
    assert bodies[0]["status"] == "indexing"
    assert bodies[-1]["status"] == "ready"
    assert bodies[-1]["indexed_count"] == 10
    assert bodies[-1]["total_rows"] == 10
    assert bodies[-1]["row_count"] == 10
