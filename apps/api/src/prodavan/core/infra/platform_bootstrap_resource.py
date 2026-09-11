"""Platform bootstrap on API startup (after DB + object store)."""

from __future__ import annotations

import logging

from prodavan.application.platform.bootstrap_service import PlatformBootstrapService
from prodavan.application.platform.seed_mcp_bootstrap_service import SeedMcpBootstrapService
from prodavan.core.lifespan.resource import LifespanResource
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)


class PlatformBootstrapResource(LifespanResource):
    @property
    def name(self) -> str:
        return "platform_bootstrap"

    async def startup(self) -> None:
        try:
            async with get_session_factory()() as session:
                result = await PlatformBootstrapService(session).ensure_bootstrapped()
                logger.info("platform_bootstrap: %s", result.get("status"))
        except Exception:
            logger.exception("platform_bootstrap failed")

        try:
            async with get_session_factory()() as session:
                seed = await SeedMcpBootstrapService(session).ensure_seed_mcp_packages()
                logger.info("seed_mcp_bootstrap: %s", seed)
        except Exception:
            logger.exception("seed_mcp_bootstrap failed")

    async def shutdown(self) -> None:
        return None
