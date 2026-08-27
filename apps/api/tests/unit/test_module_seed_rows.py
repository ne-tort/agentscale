"""Unit — module seed_rows parse + install applies seeds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.modules.module_materialize_service import (
    ModuleMaterializeService,
    _parse_seed_items,
)


def test_parse_seed_items_object_and_list() -> None:
    items = _parse_seed_items(
        {
            "items": [
                {"table_slug": "notes", "row_id": "seed_1", "body": {"title": "Hi"}},
                {"table_slug": "BAD", "row_id": "x", "body": {}},
                {"table_slug": "notes", "row_id": "bad id!", "body": {}},
            ]
        }
    )
    assert items == [{"table_slug": "notes", "row_id": "seed_1", "body": {"title": "Hi"}}]
    bare = _parse_seed_items(
        [{"table_slug": "sku", "row_id": "seed_a", "body": {"sku": "1"}}]
    )
    assert len(bare) == 1


@pytest.mark.asyncio
async def test_install_applies_seed_rows() -> None:
    session = AsyncMock()
    inst = SimpleNamespace(schema_name="cab_inst_test")
    session.get = AsyncMock(return_value=inst)

    seed_q = MagicMock()
    seed_q.scalar_one_or_none.return_value = {
        "items": [{"table_slug": "notes", "row_id": "seed_1", "body": {"t": 1}}]
    }
    session.execute = AsyncMock(side_effect=[MagicMock(), seed_q, MagicMock()])

    svc = ModuleMaterializeService.__new__(ModuleMaterializeService)
    svc._session = session
    svc._provisioner = MagicMock()
    svc._provisioner.ensure_data_layer = AsyncMock()

    await svc.install(cabinet_id="cab_1", module_id="mod_1")

    assert session.execute.await_count == 3
    last_sql = str(session.execute.await_args_list[-1].args[0])
    assert "module_data_rows" in last_sql
    assert "ON CONFLICT" in last_sql
