"""Garbage-collect orphan cabinet PG schemas (C-CABINET / P0).

Orphans appear when provision/hard-delete partially fails (schema exists without
``cabinet_instances`` row, or row deleted while schema remains).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow

logger = logging.getLogger(__name__)

_CAB_INST_SCHEMA = re.compile(r"^cab_inst_[a-z0-9_]+$")


async def list_orphan_cabinet_schemas(session: AsyncSession) -> list[str]:
    """Return ``cab_inst_*`` schemas not referenced by ``cabinet_instances``."""
    rows = await session.execute(
        text(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name LIKE 'cab_inst_%'
            """
        )
    )
    live = await session.execute(select(CabinetInstanceRow.schema_name))
    live_names = {str(n) for n in live.scalars().all() if n}
    orphans: list[str] = []
    for (name,) in rows.all():
        schema = str(name)
        if schema not in live_names and _CAB_INST_SCHEMA.match(schema):
            orphans.append(schema)
    return sorted(orphans)


async def gc_orphan_cabinet_schemas(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    limit: int = 50,
    provisioner: SchemaProvisioner | None = None,
) -> dict[str, Any]:
    """Drop orphan ``cab_inst_*`` schemas (or list them when ``dry_run``)."""
    prov = provisioner or SchemaProvisioner()
    orphans = await list_orphan_cabinet_schemas(session)
    capped = orphans[: max(0, int(limit))]
    dropped: list[str] = []
    if not dry_run:
        for schema_name in capped:
            try:
                await prov.drop_schema(session, schema_name=schema_name)
                dropped.append(schema_name)
            except Exception:
                logger.exception("orphan schema GC failed schema=%s", schema_name)
        await session.commit()
    return {
        "ok": True,
        "dry_run": dry_run,
        "orphans_found": len(orphans),
        "orphans": orphans,
        "considered": capped,
        "dropped": dropped,
    }
