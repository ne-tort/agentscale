"""Platform bootstrap on API startup (after DB)."""

from __future__ import annotations

import logging

from prodavan.application.platform.bootstrap_service import PlatformBootstrapService
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

    async def shutdown(self) -> None:
        return None
