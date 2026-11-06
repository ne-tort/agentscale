"""Ports for resource/content persistence and resolution."""

from __future__ import annotations

from typing import Protocol

from prodavan.application.resources.contracts import (
    ContentResource,
    ResourceBinding,
    ResourceScope,
    ResourceVersion,
)


class ResourceRepository(Protocol):
    """Persistence boundary; implementations may use SQLAlchemy or another store."""

    async def get(self, resource_id: str) -> ContentResource | None: ...

    async def get_version(self, resource_id: str, number: int) -> ResourceVersion | None: ...

    async def list_bindings(self, target: ResourceScope) -> list[ResourceBinding]: ...

    async def save_resource(self, resource: ContentResource) -> ContentResource: ...

    async def save_version(self, version: ResourceVersion) -> ResourceVersion: ...

    async def save_binding(self, binding: ResourceBinding) -> ResourceBinding: ...
