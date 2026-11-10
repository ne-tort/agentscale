"""Real-SQLite regression for regrant UPDATE ... RETURNING CAS guard.

Covers the consolidated integration failure: result.rowcount is unavailable on
SQLite ChunkedIteratorResult, so the guard must use authoritative returned rows
(scalars().all(), exactly one) instead of a rowcount attribute.  Uses a real
in-memory aiosqlite engine and the production repository/model/mapping; no
PostgreSQL-only semantics are asserted here (the real-PG run is separate).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import delete

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.infrastructure.persistence.models.plugin_platform import CapabilityGrantRow
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositoryUnavailable,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)

from .test_sql_route_registry import NOW, _database

pytestmark = pytest.mark.integration


async def _revoke(factory, grant_id: str) -> None:
    async with factory() as session:
        row = await session.get(CapabilityGrantRow, grant_id)
        assert row is not None
        row.status = "revoked"
        row.revoked_at = NOW
        await session.commit()


@pytest.mark.asyncio
async def test_regrant_success_on_real_sqlite_uses_returned_row_not_rowcount() -> None:
    async with _database() as factory:
        await _revoke(factory, "pcg_a")
        mapper = SqlCapabilityGrantRepository(session_factory=factory, now=lambda: NOW)
        async with factory() as session:
            previous = mapper.to_record(await session.get(CapabilityGrantRow, "pcg_a"))
        assert previous.status is GrantStatus.REVOKED
        candidate = replace(
            previous,
            grant_id="pcg_regranted",
            status=GrantStatus.ACTIVE,
            revoked_at=None,
        )

        stored = await mapper.regrant(previous_grant_id="pcg_a", grant=candidate)

        assert stored.grant_id == "pcg_regranted"
        assert stored == candidate
        async with factory() as session:
            # The old PK is gone, exactly one ACTIVE replacement keeps the
            # tenant/plugin/instance binding, and no revoked row was resurrected.
            assert await session.get(CapabilityGrantRow, "pcg_a") is None
            new_row = await session.get(CapabilityGrantRow, "pcg_regranted")
            assert new_row is not None
            assert new_row.status == "active"
            assert new_row.revoked_at is None
            assert new_row.company_id == "co_a"
            assert new_row.plugin_id == "mod_equipment"
            assert new_row.plugin_instance_id == "pinst_a"


@pytest.mark.asyncio
async def test_regrant_zero_row_returning_is_typed_failure_and_rolls_back() -> None:
    async with _database() as factory:
        await _revoke(factory, "pcg_b")
        mapper = SqlCapabilityGrantRepository(session_factory=factory, now=lambda: NOW)
        async with factory() as session:
            previous = mapper.to_record(await session.get(CapabilityGrantRow, "pcg_b"))
        candidate = replace(
            previous,
            grant_id="pcg_regranted_b",
            status=GrantStatus.ACTIVE,
            revoked_at=None,
        )

        session = factory()
        original_execute = session.execute
        statements = 0

        async def racy_execute(statement, *args, **kwargs):
            nonlocal statements
            statements += 1
            if statements == 2:
                # Lose the row between SELECT ... FOR UPDATE and UPDATE: the
                # RETURNING set is now empty, which must not read as success.
                await original_execute(
                    delete(CapabilityGrantRow).where(CapabilityGrantRow.id == "pcg_b")
                )
            return await original_execute(statement, *args, **kwargs)

        session.execute = racy_execute  # type: ignore[method-assign]
        repository = SqlCapabilityGrantRepository(session=session, now=lambda: NOW)

        with pytest.raises(PluginRepositoryUnavailable, match="замену revoked записи"):
            await repository.regrant(previous_grant_id="pcg_b", grant=candidate)
        await session.close()

        async with factory() as check:
            # The injected race DELETE was rolled back: the revoked original is
            # intact and no candidate row was committed.
            restored = await check.get(CapabilityGrantRow, "pcg_b")
            assert restored is not None
            assert restored.status == "revoked"
            assert restored.revoked_at is not None
            assert await check.get(CapabilityGrantRow, "pcg_regranted_b") is None


__all__ = [
    "test_regrant_success_on_real_sqlite_uses_returned_row_not_rowcount",
    "test_regrant_zero_row_returning_is_typed_failure_and_rolls_back",
]
