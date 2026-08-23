"""Effective agent tool policy from L04 company preset (L08)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.domain.agent import AgentToolPolicy, CreateOpts, default_tool_policy
from prodavan.domain.ai_keys import ResolvedCredential
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def filter_mcp_servers(
    servers: dict[str, Any],
    policy: AgentToolPolicy,
) -> dict[str, Any]:
    if policy.mcp == "deny":
        return {}
    if policy.mcp == "manifest_only":
        return servers
    if policy.mcp == "allowlist":
        allowed = set(policy.mcp_allowlist)
        packages = servers.get("packages") if isinstance(servers.get("packages"), list) else []
        filtered = [p for p in packages if isinstance(p, dict) and p.get("name") in allowed]
        return {**servers, "packages": filtered}
    return servers


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

        model = model_override
        allow = [m for m in (company_policy.model_allowlist or []) if str(m).strip()]
        if model is None and allow:
            model = allow[0]
        elif model is not None and allow and model not in allow:
            from prodavan.domain.errors import AppError

            raise AppError(
                code="MODEL_NOT_ALLOWED",
                title="Model not allowed",
                status=403,
                detail=f"model {model!r} not in company model_allowlist",
            )

        budget: dict[str, int] | None = None
        if company_policy.max_tokens_per_run is not None:
            budget = {"max_tokens": company_policy.max_tokens_per_run}

        return CreateOpts(
            cwd=cwd,
            model=model or project.agent_provider,
            mcp_servers=mcp_filtered,
            api_key=credential.secret,
            api_kind=credential.api_kind,
            provider=credential.provider,
            tool_policy=tool_policy,
            budget=budget,
        )
