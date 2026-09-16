"""Effective agent tool policy from L04 company preset (L08)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.ai_models.policy_service import AiModelPolicyService
from prodavan.application.projects.openclaw_config_materializer import filter_mcp_packages_by_policy
from prodavan.domain.agent import AgentToolPolicy, CreateOpts, default_tool_policy
from prodavan.domain.ai_keys import ResolvedCredential
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def filter_mcp_servers(
    servers: dict[str, Any],
    policy: AgentToolPolicy,
) -> dict[str, Any]:
    """Apply MCP allowlist to the ``mcp.json`` packages list (CLAW-P1b).

    Delegates to ``filter_mcp_packages_by_policy`` so ``mcp.json`` and the
    OpenClaw ``config.yaml`` servers map enforce the same allowlist before
    rendering — no asymmetric policy between the two config formats.
    """
    if policy.mcp == "deny":
        return {}
    packages = servers.get("packages") if isinstance(servers.get("packages"), list) else []
    filtered = filter_mcp_packages_by_policy(packages, policy)
    return {**servers, "packages": filtered}


def load_mcp_servers_from_workspace(cwd: str) -> dict[str, Any]:
    path = Path(cwd) / "mcp.json"
    if not path.is_file():
        return {"version": 1, "packages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "packages": []}
    return data if isinstance(data, dict) else {"version": 1, "packages": []}


class AgentPolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build_create_opts(
        self,
        *,
        project: ProjectRow,
        cwd: str,
        credential: ResolvedCredential,
        model_override: str | None = None,
    ) -> CreateOpts:
        company_policy = await AdminCompanyService(self._session).get_agent_policy(project.company_id)
        tool_policy = default_tool_policy(company_policy.tool_preset)
        raw_mcp = load_mcp_servers_from_workspace(cwd)
        mcp_filtered = filter_mcp_servers(raw_mcp, tool_policy)

        model_policy = await AiModelPolicyService(self._session).resolve_for_key(
            company_id=project.company_id,
            key_id=getattr(project, "resolved_ai_key_id", None),
            api_kind=credential.api_kind,
            company_policy=company_policy,
        )
        model = AiModelPolicyService(self._session).assert_model_allowed(
            model=model_override,
            policy=model_policy,
            company_policy=company_policy,
        )

        budget: dict[str, int] | None = None
        if company_policy.max_tokens_per_run is not None:
            budget = {"max_tokens": company_policy.max_tokens_per_run}

        return CreateOpts(
            cwd=cwd,
            model=model,
            mcp_servers=mcp_filtered,
            api_key=credential.secret,
            api_kind=credential.api_kind,
            provider=credential.provider,
            tool_policy=tool_policy,
            budget=budget,
        )

    async def validate_send_model(
        self,
        *,
        project: ProjectRow,
        credential_api_kind: str,
        model: str | None,
    ) -> str | None:
        if model is None or not str(model).strip():
            return None
        company_policy = await AdminCompanyService(self._session).get_agent_policy(project.company_id)
        model_policy = await AiModelPolicyService(self._session).resolve_for_key(
            company_id=project.company_id,
            key_id=getattr(project, "resolved_ai_key_id", None),
            api_kind=credential_api_kind,
            company_policy=company_policy,
        )
        return AiModelPolicyService(self._session).assert_model_allowed(
            model=model,
            policy=model_policy,
            company_policy=company_policy,
        )
