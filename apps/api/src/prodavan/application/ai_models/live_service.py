"""Live model list from agent-runtime bridge."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.application.agent.openclaw_bridge import (
    _runtime_request_headers,
    api_kind_to_bridge_adapter,
)
from prodavan.application.agent.runtime_transport import resolve_runtime_endpoint
from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.ai_models.resolution import resolve_ui_default
from prodavan.application.ai_models.service import AiModelsService
from prodavan.config.settings import settings
from prodavan.domain.ai_keys import is_http_probe_kind
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_METADATA_KEYS = (
    "input_price_usd_per_mtok",
    "output_price_usd_per_mtok",
    "max_context_tokens",
    "publisher",
    "released_at",
)


def filter_effective_live_ids(
    live_ids: list[str],
    catalog: list[dict[str, Any]],
    ceiling: list[str],
) -> list[str]:
    """Filter live probe ids down to those enabled on the key.

    A key enables a catalog model via AiKeyModelBindingRow.enabled=true; the
    catalog model exposes its provider ids as `model_ids` (aliases). A live id
    is effective iff it matches (case-insensitively) any alias of an enabled
    catalog model. If no bindings are enabled, all live ids pass (implicit
    all-on — fresh key before the user toggles anything).
    """
    enabled_aliases_lower: set[str] = set()
    for m in catalog:
        if not m.get("enabled"):
            continue
        for alias in (m.get("model_ids") or []):
            s = str(alias).strip().lower()
            if s:
                enabled_aliases_lower.add(s)
        # Backward compat: also match by catalog name (seed models with empty
        # aliases that were not yet backfilled).
        name = str(m.get("name") or "").strip().lower()
        if name:
            enabled_aliases_lower.add(name)
    if enabled_aliases_lower:
        effective = [mid for mid in live_ids if mid.lower() in enabled_aliases_lower]
    else:
        effective = list(live_ids)

    trimmed_ceiling = [str(m).strip() for m in ceiling if str(m).strip()]
    if trimmed_ceiling:
        ceiling_lower = {m.lower() for m in trimmed_ceiling}
        effective = [m for m in effective if m.lower() in ceiling_lower]
    return effective


def catalog_by_model_name(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Lookup live id → catalog item by alias (preferred) or name."""
    out: dict[str, dict[str, Any]] = {}
    for item in catalog:
        for alias in (item.get("model_ids") or []):
            s = str(alias).strip().lower()
            if s:
                out[s] = item
        name = str(item.get("name") or "").strip().lower()
        if name and name not in out:
            out[name] = item
    return out


def enrich_live_model(live_id: str, catalog_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cat = catalog_lookup.get(live_id.lower())
    item: dict[str, Any] = {
        "id": live_id,
        "label": live_id,
        "catalog_matched": cat is not None,
    }
    for key in _METADATA_KEYS:
        item[key] = cat.get(key) if cat else None
    return item


class AiModelsLiveService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_live_for_key(
        self,
        *,
        company_id: str,
        key_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        models_svc = AiModelsService(self._session)
        key_row = await models_svc.require_company_key(key_id, company_id)
        company_policy = await AdminCompanyService(self._session).get_agent_policy(company_id)
        catalog = await AiModelsService(self._session).list_key_models(company_id=company_id, key_id=key_id)
        live_ids = await self._fetch_live_model_ids(
            company_id=company_id,
            key_row=key_row,
            project_id=project_id,
        )
        if not live_ids:
            raise AppError(
                code="MODELS_UNAVAILABLE",
                title="Models unavailable",
                status=503,
                detail="live model list unavailable — ensure project container is running and AI key is valid",
            )

        ceiling = [m for m in (company_policy.model_allowlist or []) if str(m).strip()]
        effective = filter_effective_live_ids(live_ids, catalog, ceiling)

        if not effective:
            raise AppError(
                code="MODELS_UNAVAILABLE",
                title="Models unavailable",
                status=503,
                detail="no models remain after key/company filters",
            )

        lookup = catalog_by_model_name(catalog)
        # Resolve the catalog default model to its live id (any of its aliases
        # that appears in effective). Falls back to None if no match.
        ui_default = None
        default_entry = next((m for m in catalog if m.get("is_default")), None)
        if default_entry is not None:
            effective_lower = {m.lower() for m in effective}
            for alias in (default_entry.get("model_ids") or []):
                s = str(alias).strip()
                if s and s.lower() in effective_lower:
                    ui_default = s
                    break
            if ui_default is None:
                name = str(default_entry.get("name") or "").strip()
                if name and name.lower() in effective_lower:
                    ui_default = name
        ui_default = resolve_ui_default(effective, ui_default)

        return {
            "models": [enrich_live_model(name, lookup) for name in effective],
            "default_model": ui_default,
            "source": "live",
        }

    async def _fetch_live_model_ids(
        self,
        *,
        company_id: str,
        key_row,
        project_id: str | None = None,
    ) -> list[str]:
        if not settings.pod_agent_runtime_enabled:
            return []
        resolved_project_id = project_id or await self._any_running_project_for_company(company_id)
        if not resolved_project_id:
            return []
        runtime_endpoint = await resolve_runtime_endpoint(self._session, resolved_project_id)
        if runtime_endpoint is None:
            return []
        pushed = await AgentCredentialBroker(self._session).push_lease_to_runtime(
            project_id=resolved_project_id,
            key_id=key_row.id,
        )
        if not pushed:
            return []
        adapter = api_kind_to_bridge_adapter(key_row.api_kind)
        params: dict[str, str] = {"adapter": adapter, "key_id": key_row.id}
        # For HTTP provider api_kinds (openai_api / anthropic_api / openrouter /
        # custom) the pod agent-runtime /v1/models route needs the resolved
        # endpoint (base_url / models_path / auth_scheme) from the
        # ai.http_providers catalog — otherwise listHttpModels gets no baseUrl
        # and returns [] → 503 MODELS_UNAVAILABLE. SDK kinds (cursor_sdk etc.)
        # use the vendor SDK and need no endpoint params.
        if is_http_probe_kind(key_row.api_kind):
            secret = await self._resolve_key_secret(key_row.id)
            provider_endpoint = await ProviderResolver(self._session).resolve(
                api_kind=key_row.api_kind,
                provider=key_row.provider,
                secret=secret,
                catalog_entry_id=getattr(key_row, "catalog_entry_id", None),
            )
            if provider_endpoint is not None:
                params["base_url"] = provider_endpoint.base_url
                params["models_path"] = provider_endpoint.models_path
                params["auth_scheme"] = provider_endpoint.auth_scheme
        url = f"{runtime_endpoint.base_url}/v1/models"
        # Transient pod/bridge flaps (pod busy, cold conntrack, brief 5xx)
        # must not surface as 503 MODELS_UNAVAILABLE while the stack is
        # healthy: retry with short backoff. Retries cover network errors,
        # bridge >=500 and empty-but-200 answers (runtime warming up);
        # definitive 4xx fails fast.
        attempts = int(getattr(settings, "live_models_fetch_attempts", 3) or 3)
        backoffs = (0.5, 1.5)
        last_exc: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        url,
                        params=params,
                        headers=_runtime_request_headers(runtime_endpoint.headers),
                    )
                    if response.status_code >= 500:
                        logger.warning(
                            "live models bridge status=%s body=%s (attempt %s)",
                            response.status_code,
                            response.text[:200],
                            attempt + 1,
                        )
                        last_exc = RuntimeError(f"bridge status {response.status_code}")
                    elif response.status_code >= 400:
                        logger.warning(
                            "live models bridge status=%s body=%s",
                            response.status_code,
                            response.text[:200],
                        )
                        return []
                    else:
                        body = response.json()
                        models = body.get("models") if isinstance(body, dict) else None
                        if not isinstance(models, list):
                            models = []
                        out: list[str] = []
                        for item in models:
                            if isinstance(item, str) and item.strip():
                                out.append(item.strip())
                            elif isinstance(item, dict):
                                mid = str(item.get("id") or item.get("name") or "").strip()
                                if mid:
                                    out.append(mid)
                        if out:
                            return out
                        last_exc = RuntimeError("live models returned no models")
                if attempt + 1 < max(1, attempts):
                    await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
            except Exception as exc:
                last_exc = exc
                logger.debug("live models fetch failed (attempt %s): %s", attempt + 1, exc)
                if attempt + 1 < max(1, attempts):
                    await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        if last_exc is not None:
            logger.debug("live models fetch gave up: %s", last_exc)
        return []

    async def _resolve_key_secret(self, key_id: str) -> str | None:
        """Best-effort secret lookup for provider endpoint resolution (cursor token shape)."""
        try:
            return await AiKeysService(self._session).resolve_secret_for_key(key_id)
        except Exception:
            return None

    async def _any_running_project_for_company(self, company_id: str) -> str | None:
        from sqlalchemy import select

        q = await self._session.execute(
            select(ProjectRow.id)
            .join(ProjectPodRow, ProjectPodRow.project_id == ProjectRow.id)
            .where(ProjectRow.company_id == company_id, ProjectRow.status == "active")
            .limit(1)
        )
        row = q.scalar_one_or_none()
        return str(row) if row else None
