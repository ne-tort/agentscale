"""Unit tests — agent policy model allowlist (L08)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.agent.policy_service import AgentPolicyService
from prodavan.domain.admin import CompanyAgentRuntimePolicy
from prodavan.domain.ai_keys import ApiKind, ResolvedCredential
from prodavan.domain.errors import AppError


def _credential() -> ResolvedCredential:
    return ResolvedCredential(
        key_id="k1",
        provider="cursor",
        api_kind=ApiKind.CURSOR_SDK,
        secret="sk-test",
    )


@pytest.mark.asyncio
async def test_build_create_opts_omits_default_without_explicit_model() -> None:
    session = AsyncMock()
    svc = AgentPolicyService(session)
    project = SimpleNamespace(company_id="co_1", agent_provider=None, resolved_ai_key_id=None)

    policy = CompanyAgentRuntimePolicy(model_allowlist=["model-a", "model-b"])
    svc._session = session
    from prodavan.application.admin import company_service as cs

    original = cs.AdminCompanyService.get_agent_policy

    async def _fake_get(self, company_id: str):  # noqa: ANN001
        return policy

    cs.AdminCompanyService.get_agent_policy = _fake_get  # type: ignore[method-assign]
    try:
        opts = await svc.build_create_opts(
            project=project,
            cwd="/tmp",
            credential=_credential(),
            model_override=None,
        )
        assert opts.model is None
    finally:
        cs.AdminCompanyService.get_agent_policy = original  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_build_create_opts_uses_explicit_model_override() -> None:
    session = AsyncMock()
    svc = AgentPolicyService(session)
    project = SimpleNamespace(company_id="co_1", agent_provider=None, resolved_ai_key_id=None)

    policy = CompanyAgentRuntimePolicy(model_allowlist=["model-a", "model-b"])
    from prodavan.application.admin import company_service as cs

    original = cs.AdminCompanyService.get_agent_policy

    async def _fake_get(self, company_id: str):  # noqa: ANN001
        return policy

    cs.AdminCompanyService.get_agent_policy = _fake_get  # type: ignore[method-assign]
    try:
        opts = await svc.build_create_opts(
            project=project,
            cwd="/tmp",
            credential=_credential(),
            model_override="model-a",
        )
        assert opts.model == "model-a"
    finally:
        cs.AdminCompanyService.get_agent_policy = original  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_build_create_opts_rejects_model_outside_allowlist() -> None:
    session = AsyncMock()
    svc = AgentPolicyService(session)
    project = SimpleNamespace(company_id="co_1", agent_provider=None, resolved_ai_key_id=None)

    policy = CompanyAgentRuntimePolicy(model_allowlist=["model-a"])
    from prodavan.application.admin import company_service as cs

    original = cs.AdminCompanyService.get_agent_policy

    async def _fake_get(self, company_id: str):  # noqa: ANN001
        return policy

    cs.AdminCompanyService.get_agent_policy = _fake_get  # type: ignore[method-assign]
    try:
        with pytest.raises(AppError) as ei:
            await svc.build_create_opts(
                project=project,
                cwd="/tmp",
                credential=_credential(),
                model_override="model-x",
            )
        assert ei.value.code == "MODEL_NOT_ALLOWED"
    finally:
        cs.AdminCompanyService.get_agent_policy = original  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_build_create_opts_omits_agent_provider_as_model() -> None:
    session = AsyncMock()
    svc = AgentPolicyService(session)
    project = SimpleNamespace(company_id="co_1", agent_provider="cursor", resolved_ai_key_id=None)

    policy = CompanyAgentRuntimePolicy(model_allowlist=[])
    from prodavan.application.admin import company_service as cs

    original = cs.AdminCompanyService.get_agent_policy

    async def _fake_get(self, company_id: str):  # noqa: ANN001
        return policy

    cs.AdminCompanyService.get_agent_policy = _fake_get  # type: ignore[method-assign]
    try:
        opts = await svc.build_create_opts(
            project=project,
            cwd="/tmp",
            credential=_credential(),
            model_override=None,
        )
        assert opts.model is None
    finally:
        cs.AdminCompanyService.get_agent_policy = original  # type: ignore[method-assign]
