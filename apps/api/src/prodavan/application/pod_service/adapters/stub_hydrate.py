"""Stub HydratePort — workspace materialize is pre-provisioned in object-ws."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class StubHydrateAdapter:
    async def hydrate(self, *, workspace_key: str, runtime_ref: str) -> None:
        logger.debug(
            "hydrate stub workspace_key=%s runtime_ref=%s (workspace already materialized)",
            workspace_key,
            runtime_ref,
        )
