"""Reference catalog application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow

CATALOG_AI_HTTP_PROVIDERS = "ai.http_providers"

_AI_HTTP_SEED: list[dict[str, Any]] = [
    {
        "id": "openai",
        "title": "OpenAI",
        "subtitle": "api.openai.com",
        "icon_name": "smart_toy_outlined",
        "sort_order": 10,
        "payload": {
            "api_kind": "openai_api",
            "agent_provider": "codex",
            "base_url": "https://api.openai.com",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "anthropic",
        "title": "Anthropic",
        "subtitle": "api.anthropic.com",
        "icon_name": "psychology_outlined",
        "sort_order": 20,
        "payload": {
            "api_kind": "anthropic_api",
            "agent_provider": "claude_code",
            "base_url": "https://api.anthropic.com",
            "openai_compatible": False,
            "auth_scheme": "x-api-key",
            "chat_completions_path": "/v1/messages",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "openrouter",
        "title": "OpenRouter",
        "subtitle": "openrouter.ai",
        "icon_name": "hub_outlined",
        "sort_order": 30,
        "payload": {
            "api_kind": "openrouter",
            "agent_provider": "codex",
            "base_url": "https://openrouter.ai/api/v1",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/chat/completions",
            "models_path": "/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "cursor",
        "title": "Cursor (Dashboard API)",
        "subtitle": "api.cursor.com",
        "icon_name": "terminal_outlined",
        "sort_order": 40,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "cursor",
            "base_url": "https://api.cursor.com",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "cursor_workos",
        "title": "Cursor (WorkOS token)",
        "subtitle": "api2.cursor.sh",
        "icon_name": "terminal_outlined",
        "sort_order": 41,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "cursor",
            "base_url": "https://api2.cursor.sh",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            # WorkOS access token (eyJ… JWT) — gateway api2.cursor.sh does NOT
            # expose a public list-models endpoint. Probe falls back to a
            # 1-token chat completion to validate the token; no models page.
            "supports_models_list": False,
        },
    },
    {
        "id": "ollama",
        "title": "Ollama",
        "subtitle": "localhost:11434",
        "icon_name": "smart_toy_outlined",
        "sort_order": 50,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "codex",
            "base_url": "http://127.0.0.1:11434/v1",
            "openai_compatible": True,
            "auth_scheme": "none",
            "chat_completions_path": "/chat/completions",
            "models_path": "/models",
            "supports_models_list": True,
        },
    },
]


def _public(row: ReferenceCatalogEntryRow) -> dict[str, Any]:
    return {
        "catalog_id": row.catalog_id,
        "id": row.id,
        "title": row.title,
        "subtitle": row.subtitle,
        "icon_name": row.icon_name,
        "payload": row.payload or {},
        "seeded": row.seeded,
        "sort_order": row.sort_order,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_seeded(self, catalog_id: str) -> None:
        if catalog_id != CATALOG_AI_HTTP_PROVIDERS:
            return
        existing = await self._session.execute(
            select(ReferenceCatalogEntryRow.id).where(
                ReferenceCatalogEntryRow.catalog_id == catalog_id,
                ReferenceCatalogEntryRow.seeded.is_(True),
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return
        for spec in _AI_HTTP_SEED:
            self._session.add(
                ReferenceCatalogEntryRow(
                    catalog_id=catalog_id,
                    id=spec["id"],
                    title=spec["title"],
                    subtitle=spec.get("subtitle"),
                    icon_name=spec.get("icon_name"),
                    payload=dict(spec.get("payload") or {}),
                    seeded=True,
                    sort_order=int(spec.get("sort_order") or 0),
                )
            )
        await self._session.commit()

    async def list_entries(self, catalog_id: str, *, include_archived: bool = False) -> list[dict]:
        await self.ensure_seeded(catalog_id)
        q = select(ReferenceCatalogEntryRow).where(ReferenceCatalogEntryRow.catalog_id == catalog_id)
        if not include_archived:
            q = q.where(ReferenceCatalogEntryRow.archived_at.is_(None))
        q = q.order_by(ReferenceCatalogEntryRow.sort_order.asc(), ReferenceCatalogEntryRow.title.asc())
        rows = (await self._session.execute(q)).scalars().all()
        return [_public(r) for r in rows]

    async def create_entry(
        self,
        catalog_id: str,
        *,
        title: str,
        id: str | None = None,
        subtitle: str | None = None,
        icon_name: str | None = None,
        payload: dict[str, Any] | None = None,
        sort_order: int = 100,
    ) -> dict:
        await self.ensure_seeded(catalog_id)
        entry_id = (id or title.strip().lower().replace(" ", "_"))[:64]
        clash = await self._session.execute(
            select(ReferenceCatalogEntryRow).where(
                ReferenceCatalogEntryRow.catalog_id == catalog_id,
                ReferenceCatalogEntryRow.id == entry_id,
                ReferenceCatalogEntryRow.archived_at.is_(None),
            )
        )
        if clash.scalar_one_or_none() is not None:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="entry id exists")
        row = ReferenceCatalogEntryRow(
            catalog_id=catalog_id,
            id=entry_id,
            title=title.strip(),
            subtitle=subtitle,
            icon_name=icon_name,
            payload=dict(payload or {}),
            seeded=False,
            sort_order=sort_order,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def patch_entry(
        self,
        catalog_id: str,
        entry_id: str,
        **fields: Any,
    ) -> dict:
        row = await self._require(catalog_id, entry_id)
        if "title" in fields and fields["title"] is not None:
            row.title = str(fields["title"]).strip()
        if "subtitle" in fields:
            row.subtitle = fields["subtitle"]
        if "icon_name" in fields:
            row.icon_name = fields["icon_name"]
        # Seeded preset entries (catalog presets shipped by migrations) are
        # immutable: their payload (base_url / auth_scheme / paths) is the
        # platform default and must not be edited through the catalog API —
        # a user who wants a different endpoint creates a *custom* entry. This
        # prevents the accidental "edit ollama to point at cheapai" footgun
        # that a later reseed migration would silently revert anyway.
        if row.seeded:
            if "payload" in fields and fields["payload"] is not None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="seeded catalog entries are immutable; create a custom entry instead",
                )
            if "sort_order" in fields and fields["sort_order"] is not None:
                row.sort_order = int(fields["sort_order"])
        else:
            if "payload" in fields and fields["payload"] is not None:
                row.payload = dict(fields["payload"])
            if "sort_order" in fields and fields["sort_order"] is not None:
                row.sort_order = int(fields["sort_order"])
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def delete_entry(self, catalog_id: str, entry_id: str, *, hard: bool = False) -> dict:
        row = await self._require(catalog_id, entry_id)
        if hard:
            await self._session.delete(row)
            await self._session.commit()
            return {"id": entry_id, "deleted": True}
        row.archived_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def _require(self, catalog_id: str, entry_id: str) -> ReferenceCatalogEntryRow:
        q = await self._session.execute(
            select(ReferenceCatalogEntryRow).where(
                ReferenceCatalogEntryRow.catalog_id == catalog_id,
                ReferenceCatalogEntryRow.id == entry_id,
            )
        )
        row = q.scalar_one_or_none()
        if row is None or row.archived_at is not None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="catalog entry not found")
        return row
