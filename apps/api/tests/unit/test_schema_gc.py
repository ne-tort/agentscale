"""Unit tests — orphan cabinet schema GC helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.cabinets import schema_gc


@pytest.mark.asyncio
async def test_list_orphan_cabinet_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()

    schemata = MagicMock()
    schemata.all.return_value = [
        ("cab_inst_alive",),
        ("cab_inst_orphan",),
        ("public",),
        ("cab_inst_bad!",),
    ]

    live = MagicMock()
    live.scalars.return_value.all.return_value = ["cab_inst_alive"]

    session.execute = AsyncMock(side_effect=[schemata, live])
    orphans = await schema_gc.list_orphan_cabinet_schemas(session)
    assert orphans == ["cab_inst_orphan"]


@pytest.mark.asyncio
async def test_gc_orphan_dry_run_does_not_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()

    async def _list(_session):
        return ["cab_inst_a", "cab_inst_b"]

    monkeypatch.setattr(schema_gc, "list_orphan_cabinet_schemas", _list)
    prov = SimpleNamespace(drop_schema=AsyncMock())
    out = await schema_gc.gc_orphan_cabinet_schemas(session, dry_run=True, limit=10, provisioner=prov)
    assert out["dry_run"] is True
    assert out["orphans"] == ["cab_inst_a", "cab_inst_b"]
    assert out["dropped"] == []
    prov.drop_schema.assert_not_called()


@pytest.mark.asyncio
async def test_gc_orphan_drops_with_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()

    async def _list(_session):
        return ["cab_inst_a", "cab_inst_b", "cab_inst_c"]

    monkeypatch.setattr(schema_gc, "list_orphan_cabinet_schemas", _list)
    prov = SimpleNamespace(drop_schema=AsyncMock())
    out = await schema_gc.gc_orphan_cabinet_schemas(session, dry_run=False, limit=2, provisioner=prov)
    assert out["dropped"] == ["cab_inst_a", "cab_inst_b"]
    assert prov.drop_schema.await_count == 2
    session.commit.assert_awaited()
