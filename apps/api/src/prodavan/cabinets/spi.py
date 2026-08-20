"""Cabinet SPI protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class SpiContext:
    tenant_id: UUID
    cabinet_id: UUID
    user_id: UUID | None = None
    # Project ids are opaque strings (e.g. prj_…), not UUIDs.
    project_id: str | None = None


@dataclass
class PlatformEvent:
    event_id: UUID
    type: str
    occurred_at: datetime
    tenant_id: UUID
    cabinet_id: UUID
    data: dict[str, Any]
    project_id: str | None = None
    actor_user_id: UUID | None = None


@dataclass
class HealthResponse:
    status: str
    pack_id: str
    pack_version: str


class CabinetModule(Protocol):
    pack_id: str
    pack_version: str

    def health(self) -> HealthResponse: ...

    async def manifest(self, ctx: SpiContext) -> dict[str, Any]: ...

    async def execute_command(
        self, ctx: SpiContext, name: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def execute_query(
        self, ctx: SpiContext, name: str, params: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def on_platform_event(self, event: PlatformEvent) -> None: ...

    async def migrate(self, ctx: SpiContext) -> dict[str, Any]: ...
