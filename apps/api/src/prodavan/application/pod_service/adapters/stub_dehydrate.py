"""Stub dehydrate — no Pod FS; local materialize tree is already object-store SoT."""

from __future__ import annotations

import logging

from prodavan.application.pod_service.ports.dehydrate import DehydrateResult

logger = logging.getLogger(__name__)


class StubDehydrateAdapter:
    async def dehydrate(self, *, workspace_key: str, runtime_ref: str) -> DehydrateResult:
        logger.debug(
            "dehydrate stub no-op workspace_key=%s runtime_ref=%s",
            workspace_key,
            runtime_ref,
        )
        return DehydrateResult(uploaded=0, deleted=0)
