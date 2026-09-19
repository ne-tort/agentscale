"""Resolve provider HTTP endpoint + auth scheme for an api_kind (PROBE-P1).

Reads the catalog of HTTP providers (`ai.http_providers`) seeded by
CatalogService. For SDK api_kinds (cursor_sdk / codex_sdk / claude_agent_sdk)
the catalog entry is matched by `agent_provider`, because those SDK tokens
are HTTP bearer/x-api-key tokens against the vendor's HTTP API.

Cursor has two token types — dashboard API key (crsr_…, works with
api.cursor.com /v1/models) and WorkOS access token (eyJ… JWT, works only
with api2.cursor.sh chat and does NOT expose list-models). The resolver
picks the dashboard entry when the secret looks like a crsr_ key, otherwise
the WorkOS entry (no models list — probe falls back to a 1-token chat).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.catalog.service import CATALOG_AI_HTTP_PROVIDERS
from prodavan.domain.ai_keys import ApiKind
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    """Resolved HTTP endpoint for a provider/api_kind."""

    catalog_id: str
    title: str
    base_url: str
    api_kind: str
    agent_provider: str
    auth_scheme: str  # "bearer" | "x-api-key" | "none"
    chat_completions_path: str
    models_path: str
    openai_compatible: bool
    supports_models_list: bool  # False for Cursor WorkOS gateway


# Map api_kind → default agent_provider to resolve catalog entry for SDK kinds.
# SDK api_kinds are validated against the vendor's HTTP API directly.
_SDK_PROVIDER_BY_KIND: dict[str, str] = {
    ApiKind.CURSOR_SDK: "cursor",
    ApiKind.CODEX_SDK: "codex",
    ApiKind.CLAUDE_AGENT_SDK: "claude_code",
}


def _is_cursor_dashboard_token(secret: str) -> bool:
    """Cursor dashboard API keys start with 'crsr_'."""
    return (secret or "").lstrip().lower().startswith("crsr_")


class ProviderResolver:
    """Resolve a provider HTTP endpoint from the catalog by api_kind/provider."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        api_kind: str,
        provider: str,
        secret: str | None = None,
    ) -> ProviderEndpoint | None:
        entries = await self._entries()
        if not entries:
            return None
        # Exact api_kind match first (openai_api / anthropic_api / openrouter / custom)
        for e in entries:
            if e.api_kind == api_kind and e.agent_provider == provider:
                return e
        # SDK kinds: match by agent_provider (token works against vendor HTTP API)
        sdk_provider = _SDK_PROVIDER_BY_KIND.get(api_kind)
        if sdk_provider:
            cursor_entries = [e for e in entries if e.agent_provider == "cursor"]
            if sdk_provider == "cursor" and cursor_entries:
                # Dashboard API key (crsr_…) works with api.cursor.com and
                # exposes /v1/models. WorkOS token (eyJ…) does not — pick the
                # matching entry based on the secret shape.
                if secret and _is_cursor_dashboard_token(secret):
                    dashboard = next(
                        (e for e in cursor_entries if e.supports_models_list),
                        None,
                    )
                    if dashboard:
                        return dashboard
                workos = next(
                    (e for e in cursor_entries if not e.supports_models_list),
                    None,
                )
                if workos:
                    return workos
                # Fallback: first cursor entry
                return cursor_entries[0]
            for e in entries:
                if e.agent_provider == sdk_provider and (
                    e.api_kind in (api_kind, ApiKind.CUSTOM, "openai_api", "anthropic_api") or sdk_provider == "cursor"
                ):
                    return e
        # Fallback: any entry with this api_kind
        for e in entries:
            if e.api_kind == api_kind:
                return e
        return None

    async def _entries(self) -> list[ProviderEndpoint]:
        q = await self._session.execute(
            select(ReferenceCatalogEntryRow)
            .where(
                ReferenceCatalogEntryRow.catalog_id == CATALOG_AI_HTTP_PROVIDERS,
                ReferenceCatalogEntryRow.archived_at.is_(None),
            )
            .order_by(ReferenceCatalogEntryRow.sort_order.asc())
        )
        out: list[ProviderEndpoint] = []
        for row in q.scalars().all():
            payload = row.payload or {}
            if not payload.get("base_url"):
                continue
            out.append(
                ProviderEndpoint(
                    catalog_id=row.id,
                    title=row.title or row.id,
                    base_url=str(payload["base_url"]).rstrip("/"),
                    api_kind=str(payload.get("api_kind") or ""),
                    agent_provider=str(payload.get("agent_provider") or ""),
                    auth_scheme=str(payload.get("auth_scheme") or "bearer"),
                    chat_completions_path=str(payload.get("chat_completions_path") or "/v1/chat/completions"),
                    models_path=str(payload.get("models_path") or "/v1/models"),
                    openai_compatible=bool(payload.get("openai_compatible", True)),
                    supports_models_list=bool(payload.get("supports_models_list", True)),
                )
            )
        return out
