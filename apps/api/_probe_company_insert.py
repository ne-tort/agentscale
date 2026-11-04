"""Scratch probe (not a test): reproduce the sibling suite's companies insert failure."""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from prodavan.infrastructure.persistence.models.identity import CompanyRow  # noqa: E402
from tests.migration.dynamic_app_gateway.sqlite_mirror import (  # noqa: E402
    create_schema,
    make_engine,
)

QUERY = "select sql from sqlite_master where name='companies'"


async def main() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(CompanyRow(id="comp_x", name="x", login_slug="slug-x", status="active"))
        try:
            await session.commit()
            print("INSERT OK")
        except Exception as exc:  # noqa: BLE001
            print("FAILED:", type(exc).__name__, str(exc).splitlines()[0])
    async with engine.connect() as connection:
        print((await connection.execute(sa.text(QUERY))).scalar())


asyncio.run(main())
