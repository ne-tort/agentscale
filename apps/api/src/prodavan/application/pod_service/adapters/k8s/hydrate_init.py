"""Hydrate via initContainer — post-create noop."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class K8sInitHydrateAdapter:
    """Workspace sync runs in Pod initContainer before main container starts."""

    async def hydrate(self, *, workspace_key: str, runtime_ref: str) -> None:
        logger.debug(
            "hydrate k8s-init workspace_key=%s runtime_ref=%s (initContainer completed)",
            workspace_key,
            runtime_ref,
        )
