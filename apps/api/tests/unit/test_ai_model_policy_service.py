"""Unit tests — effective AI model policy resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prodavan.application.ai_models.policy_service import AiModelPolicyService
from prodavan.domain.admin import CompanyAgentRuntimePolicy


@pytest.mark.asyncio
async def test_resolve_empty_ceiling_allows_key_bindings() -> None:
    session = AsyncMock()
    svc = AiModelPolicyService(session)
    policy = CompanyAgentRuntimePolicy(model_allowlist=[])

    result = await svc.resolve_for_key(
        company_id="co_1",
        key_id=None,
        api_kind="cursor_sdk",
        company_policy=policy,
    )
    assert result.allowed_models == []
    assert result.default_model is None


@pytest.mark.asyncio
async def test_resolve_ceiling_without_key_uses_first_ceiling_model() -> None:
    session = AsyncMock()
    svc = AiModelPolicyService(session)
    policy = CompanyAgentRuntimePolicy(model_allowlist=["gpt-5", "claude-sonnet"])

    result = await svc.resolve_for_key(
        company_id="co_1",
        key_id=None,
        api_kind="cursor_sdk",
        company_policy=policy,
    )
    assert result.allowed_models == ["gpt-5", "claude-sonnet"]
    assert result.default_model == "gpt-5"


def test_assert_model_allowed_empty_ceiling_allows_any_when_no_key_models() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=[])
    from prodavan.application.ai_models.policy_service import EffectiveModelPolicy

    out = svc.assert_model_allowed(
        model="composer-2.5",
        policy=EffectiveModelPolicy(allowed_models=[], default_model=None),
        company_policy=policy,
    )
    assert out == "composer-2.5"


def test_assert_model_allowed_rejects_outside_ceiling() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=["gpt-5"])
    from prodavan.application.ai_models.policy_service import EffectiveModelPolicy
    from prodavan.domain.errors import AppError

    with pytest.raises(AppError) as ei:
        svc.assert_model_allowed(
            model="claude-sonnet",
            policy=EffectiveModelPolicy(allowed_models=[], default_model=None),
            company_policy=policy,
        )
    assert ei.value.code == "MODEL_NOT_ALLOWED"
