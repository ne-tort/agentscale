"""AI model default resolution — UI and send-time picks from live SDK lists."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prodavan.domain.errors import AppError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def resolve_ui_default(effective: list[str], catalog_default: str | None) -> str | None:
    """Pick chat default: catalog flag, SDK ``default`` id, else first live model."""
    if catalog_default and catalog_default in effective:
        return catalog_default
    if "default" in effective:
        return "default"
    return effective[0] if effective else None


class AiModelResolutionService:
    """Resolve model defaults without coupling agent session BC to bridge HTTP details."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_send_default(
        self,
        *,
        company_id: str,
        key_id: str,
        project_id: str,
    ) -> str | None:
        """Default model id for agent send when none was chosen explicitly."""
        from prodavan.application.ai_models.live_service import AiModelsLiveService

        try:
            body = await AiModelsLiveService(self._session).list_live_for_key(
                company_id=company_id,
                key_id=key_id,
                project_id=project_id,
            )
        except AppError:
            return None
        default = body.get("default_model")
        return str(default) if default else None
