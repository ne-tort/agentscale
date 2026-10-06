"""Unit tests — equipment catalog → OpenSearch helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.equipment_catalog_opensearch import (
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


# ------------------------------------------------- remote source projection


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeCursor:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)


class _FakeStmt:
    def __init__(self, sql, rows) -> None:
        self.sql = sql
        self.rows = rows
        self.prefetch = None

    def cursor(self, prefetch=None):
        self.prefetch = prefetch
        return _FakeCursor(self.rows)


class _FakeRemoteConn:
    def __init__(self, rows=None, columns=None, raise_on_fetch=False) -> None:
        self.rows = rows or []
        self.columns = columns
        self.raise_on_fetch = raise_on_fetch
        self.prepared: list = []

    async def prepare(self, sql):
        stmt = _FakeStmt(sql, self.rows)
        self.prepared.append(stmt)
        return stmt

    def transaction(self):
        return _FakeTx()

    async def fetch(self, sql, *args):
        if self.raise_on_fetch:
            raise RuntimeError("boom")
        return self.columns or []


@pytest.mark.asyncio
async def test_remote_source_projects_columns_and_bigger_prefetch() -> None:
    from prodavan.application.modules.equipment_catalog_opensearch import (
        _REMOTE_CURSOR_PREFETCH,
        _RemoteSqlSource,
    )

    conn = _FakeRemoteConn(rows=[{"title": "a"}, {"title": "b"}])
    source = _RemoteSqlSource(conn, '"public"."t"', 2, columns=['"title"', '"price"'])
    out = [row async for row in source.aiter_rows()]
    assert len(out) == 2
    # проекция вместо SELECT * и укрупнённый prefetch (NAT round-trips)
    assert conn.prepared[0].sql == 'SELECT "title", "price" FROM "public"."t"'
    assert conn.prepared[0].prefetch == _REMOTE_CURSOR_PREFETCH
    assert _REMOTE_CURSOR_PREFETCH > 500


@pytest.mark.asyncio
async def test_remote_source_star_without_columns() -> None:
    from prodavan.application.modules.equipment_catalog_opensearch import _RemoteSqlSource

    conn = _FakeRemoteConn(rows=[{"title": "a"}])
    source = _RemoteSqlSource(conn, '"t"', 1)
    _ = [row async for row in source.aiter_rows()]
    assert conn.prepared[0].sql == 'SELECT * FROM "t"'


@pytest.mark.asyncio
async def test_projection_columns_intersection_quoted() -> None:
    from prodavan.application.modules.equipment_catalog_opensearch import (
        _remote_projection_columns,
    )

    conn = _FakeRemoteConn(columns=[("title",), ("price",), ("extra",)])
    cols = await _remote_projection_columns(
        conn, schema="public", table="t", wanted=["title", "price", "missing"], quote=lambda s: f'"{s}"'
    )
    assert cols == ['"title"', '"price"']


@pytest.mark.asyncio
async def test_projection_columns_fallback_to_star() -> None:
    from prodavan.application.modules.equipment_catalog_opensearch import (
        _remote_projection_columns,
    )

    q = lambda s: s  # noqa: E731
    # information_schema недоступен → None (SELECT *)
    conn = _FakeRemoteConn(raise_on_fetch=True)
    assert await _remote_projection_columns(conn, schema=None, table="t", wanted=["title"], quote=q) is None
    # таблица не найдена → None
    conn2 = _FakeRemoteConn(columns=[])
    assert await _remote_projection_columns(conn2, schema=None, table="t", wanted=["title"], quote=q) is None
    # пустой column_map → None
    conn3 = _FakeRemoteConn(columns=[("title",)])
    assert await _remote_projection_columns(conn3, schema=None, table="t", wanted=[], quote=q) is None


@pytest.mark.asyncio
async def test_projection_includes_currency_like_columns() -> None:
    """currency-heal в apply_column_map сканирует все исходные колонки —
    проекция обязана сохранять currency-подобные, даже если они не в map."""
    from prodavan.application.modules.equipment_catalog_opensearch import (
        _remote_projection_columns,
    )

    conn = _FakeRemoteConn(columns=[("name",), ("price",), ("currency",), ("extra",)])
    cols = await _remote_projection_columns(
        conn, schema="public", table="t", wanted=["name", "price"], quote=lambda s: f'"{s}"'
    )
    assert cols == ['"name"', '"price"', '"currency"']


@pytest.mark.asyncio
async def test_projection_uses_map_values_semantics() -> None:
    """column_map = {canonical → source}: в проекцию идут ЗНАЧЕНИЯ (name/pn),
    а не канонические ключи (title/part_number) — регрессия пустого индекса."""
    from prodavan.application.modules.equipment_catalog_opensearch import (
        _normalize_column_map,
        _remote_projection_columns,
    )

    cmap = _normalize_column_map({"title": "name", "price": "price", "part_number": "pn"})
    assert set(cmap.values()) == {"name", "price", "pn"}
    conn = _FakeRemoteConn(columns=[("name",), ("price",), ("pn",), ("title",)])
    cols = await _remote_projection_columns(
        conn, schema=None, table="t", wanted=cmap.values(), quote=lambda s: f'"{s}"'
    )
    assert cols is not None
    assert '"name"' in cols and '"pn"' in cols


@pytest.mark.asyncio
async def test_run_index_aborts_when_row_deleted(monkeypatch) -> None:
    """Каталог удалён посреди индексации: задача прерывается и НЕ воскрешает
    строку upsert'ом (регрессия «удаление не работает»)."""
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
    # первый get (старт) — строка жива; после первого батча — удалена
    inst.get_data_row = AsyncMock(side_effect=[row, row, None, None, None])
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
    monkeypatch.setattr(mod, "get_search_index_service", lambda: svc)

    out = await mod.run_index_equipment_catalog(
        session, instance_id="inst1", row_id="row1", company_id="co1"
    )
    assert out["ok"] is False
    assert out["error"] == "row_deleted"
    # после удаления ни одного upsert'а (строка не воскрешена), статус ready не пишется
    assert all(s.get("status") != "ready" for s in snapshots)


def test_revoke_catalog_index_task_survives_without_celery() -> None:
    """В тестах/локалке celery_app может быть None — revoke не должен падать."""
    from prodavan.application.modules.equipment_catalog_opensearch import (
        revoke_catalog_index_task,
    )

    revoke_catalog_index_task("row_x")  # no raise
