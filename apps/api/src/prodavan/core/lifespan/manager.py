"""LifespanManager — central register for LifespanResource (P0)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)


class LifespanManager:
    """Registry + FastAPI lifespan context.

    Startup runs in registration order; shutdown in reverse.
    """

    def __init__(self) -> None:
        self._resources: list[LifespanResource] = []

    def register(self, resource: LifespanResource) -> LifespanManager:
        if any(r.name == resource.name for r in self._resources):
            raise ValueError(f"LifespanResource already registered: {resource.name}")
        self._resources.append(resource)
        return self

    def get(self, name: str) -> LifespanResource | None:
        for resource in self._resources:
            if resource.name == name:
                return resource
        return None

    def unregister(self, name: str) -> LifespanResource | None:
        """Remove one resource by name (composition rollback before startup).

        Counterpart of register(): plugin composition can fail after some
        resources were registered, and the rollback must not leave stale
        entries that would start/close owners whose app.state was already
        rolled back. Not intended for use after startup_all().
        """
        for index, resource in enumerate(self._resources):
            if resource.name == name:
                return self._resources.pop(index)
        return None

    @property
    def resources(self) -> tuple[LifespanResource, ...]:
        return tuple(self._resources)

    async def startup_all(self) -> None:
        for resource in self._resources:
            logger.info("lifespan startup: %s", resource.name)
            await resource.startup()
    async def shutdown_all(self) -> None:
        for resource in reversed(self._resources):
            logger.info("lifespan shutdown: %s", resource.name)
            try:
                await resource.shutdown()
            except Exception:
                logger.exception("lifespan shutdown failed: %s", resource.name)

    async def health_report(self) -> dict[str, bool | None]:
        report: dict[str, bool | None] = {}
        for resource in self._resources:
            try:
                report[resource.name] = await resource.health()
            except Exception:
                logger.exception("lifespan health failed: %s", resource.name)
                report[resource.name] = False
        return report

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        # Some resources need the FastAPI app itself (for example to publish a
        # runtime on ``app.state``).  ``startup()`` has no app argument, so bind
        # it here — before any resource starts — instead of forcing every caller
        # to subclass this manager.
        for resource in self._resources:
            bind = getattr(resource, "bind_app", None)
            if callable(bind):
                bind(app)
        await self.startup_all()
        try:
            yield
        finally:
            await self.shutdown_all()

    def as_fastapi_lifespan(self) -> Any:
        """Bound lifespan callable for ``FastAPI(lifespan=...)``."""
        return self.lifespan
