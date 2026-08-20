"""Platform → cabinet event helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from prodavan.cabinets.registry import get_module_for_profile
from prodavan.cabinets.spi import PlatformEvent, SpiContext


async def emit_platform_event(
    *,
    profile_id: str,
    event_type: str,
    tenant_id: UUID,
    cabinet_id: UUID,
    data: dict[str, Any],
    project_id: str | None = None,
    actor_user_id: UUID | None = None,
) -> None:
    module = get_module_for_profile(profile_id)
    event = PlatformEvent(
        event_id=uuid4(),
        type=event_type,
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        data=data,
    )
    await module.on_platform_event(event)


def spi_context(
    *,
    tenant_id: UUID,
    cabinet_id: UUID,
    user_id: UUID | None = None,
    project_id: str | None = None,
) -> SpiContext:
    return SpiContext(
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        user_id=user_id,
        project_id=project_id,
    )
