"""Live model list from agent-runtime bridge."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.application.agent.openclaw_bridge import (
    OpenClawBridgeBootstrap,
    _runtime_request_headers,
    api_kind_to_bridge_adapter,
)
from prodavan.application.ai_models.resolution import resolve_ui_default
from prodavan.application.ai_models.service import AiModelsService
from prodavan.config.settings import settings
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
    enabled_names_lower = {str(m["name"]).lower() for m in catalog if m.get("enabled")}
    if enabled_names_lower:
        effective = [mid for mid in live_ids if mid.lower() in enabled_names_lower]
    else:
        effective = list(live_ids)

    trimmed_ceiling = [str(m).strip() for m in ceiling if str(m).strip()]
    if trimmed_ceiling:
        ceiling_lower = {m.lower() for m in trimmed_ceiling}
        effective = [m for m in effective if m.lower() in ceiling_lower]
    return effective


def catalog_by_model_name(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in catalog:
        name = str(item.get("name") or "").strip()
        if name:
            out[name.lower()] = item
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
            key_id=key_id,
            api_kind=api_kind_to_bridge_adapter(key_row.api_kind),
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
        ui_default = next((str(m["name"]) for m in catalog if m.get("is_default")), None)
        if ui_default and ui_default not in effective and ui_default.lower() not in {m.lower() for m in effective}:
            ui_default = None
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
        key_id: str,
        api_kind: str,
        project_id: str | None = None,
    ) -> list[str]:
        if not settings.pod_agent_runtime_enabled:
            return []
        resolved_project_id = project_id or await self._any_running_project_for_company(company_id)
        if not resolved_project_id:
            return []
        bridge = OpenClawBridgeBootstrap(self._session)
        pod_ip = await bridge._resolve_pod_ip_for_project(resolved_project_id)  # noqa: SLF001
        if not pod_ip:
            return []
        pushed = await AgentCredentialBroker(self._session).push_lease_to_runtime(
            project_id=resolved_project_id,
            key_id=key_id,
        )
        if not pushed:
            return []
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    url,
                    params={"adapter": api_kind, "key_id": key_id},
                    headers=_runtime_request_headers(),
                )
                if response.status_code >= 400:
                    logger.warning(
                        "live models bridge status=%s body=%s",
                        response.status_code,
                        response.text[:200],
                    )
                    return []
                body = response.json()
                models = body.get("models") if isinstance(body, dict) else None
                if not isinstance(models, list):
                    return []
                out: list[str] = []
                for item in models:
                    if isinstance(item, str) and item.strip():
                        out.append(item.strip())
                    elif isinstance(item, dict):
                        mid = str(item.get("id") or item.get("name") or "").strip()
                        if mid:
                            out.append(mid)
                return out
        except Exception as exc:
            logger.debug("live models fetch failed: %s", exc)
            return []

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
