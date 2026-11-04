"""Fixtures: an in-memory database with the mirrored dynamic-app tables.

The schema comes from :mod:`tests.migration.dynamic_app_gateway.sqlite_mirror`;
the ports under test are the **production** classes, constructed exactly the way
``build_legacy_gateway_ports`` constructs them (a session factory plus an
optional clock).  Nothing here substitutes a port.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.dynamic_app_gateway import (
    SqlCapabilityScopePort,
    SqlDynamicPluginRepository,
)

from .sqlite_mirror import create_schema, make_engine


@dataclass(slots=True)
class GatewayHarness:
    """One in-memory database plus the ports bound to it."""

    session_factory: async_sessionmaker[AsyncSession]
    repository: SqlDynamicPluginRepository
    scope: SqlCapabilityScopePort

    def session(self) -> AsyncSession:
        return self.session_factory()

    async def add_all(self, *rows: object) -> None:
        async with self.session_factory() as session:
            session.add_all(list(rows))
            await session.commit()


@asynccontextmanager
async def gateway_with(
    *,
    without_unique: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> AsyncIterator[GatewayHarness]:
    """A harness over a schema that may deliberately lack constraint(s).

    ``without_unique`` exists only for the ambiguity tests: the ports refuse to
    pick a row when a query answers twice, and that state cannot exist while the
    unique constraint is installed.  ``now`` pins the scope port's clock so an
    expiry decision is exact rather than wall-clock dependent.
    """

    engine = make_engine()
    try:
        await create_schema(engine, without_unique=without_unique)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        pinned = now if now is not None else datetime.now(UTC)
        yield GatewayHarness(
            session_factory=factory,
            repository=SqlDynamicPluginRepository(session_factory=factory),
            scope=SqlCapabilityScopePort(
                session_factory=factory, clock=lambda: pinned
            ),
        )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def gateway() -> AsyncIterator[GatewayHarness]:
    async with gateway_with() as harness:
        yield harness


__all__ = ["GatewayHarness", "gateway", "gateway_with"]
