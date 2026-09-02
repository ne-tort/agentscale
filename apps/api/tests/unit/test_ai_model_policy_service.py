"""Unit tests — effective AI model policy resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prodavan.application.ai_models.policy_service import AiModelPolicyService, EffectiveModelPolicy
from prodavan.domain.admin import CompanyAgentRuntimePolicy
from prodavan.domain.errors import AppError


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
    assert result.ui_default_model is None


@pytest.mark.asyncio
async def test_resolve_ceiling_without_key_skips_ceiling_for_cursor_sdk() -> None:
    session = AsyncMock()
    svc = AiModelPolicyService(session)
    policy = CompanyAgentRuntimePolicy(model_allowlist=["gpt-5", "claude-sonnet"])

    result = await svc.resolve_for_key(
        company_id="co_1",
        key_id=None,
        api_kind="cursor_sdk",
        company_policy=policy,
    )
    assert result.allowed_models == []
    assert result.ui_default_model is None


@pytest.mark.asyncio
async def test_resolve_ceiling_without_key_keeps_ceiling_filter_for_http() -> None:
    session = AsyncMock()
    svc = AiModelPolicyService(session)
    policy = CompanyAgentRuntimePolicy(model_allowlist=["gpt-5", "claude-sonnet"])

    result = await svc.resolve_for_key(
        company_id="co_1",
        key_id=None,
        api_kind="openrouter",
        company_policy=policy,
    )
    assert result.allowed_models == ["gpt-5", "claude-sonnet"]
    assert result.ui_default_model is None


def test_assert_model_allowed_empty_when_model_not_provided() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=[])
    out = svc.assert_model_allowed(
        model=None,
        policy=EffectiveModelPolicy(allowed_models=[], ui_default_model="composer-2.5"),
        company_policy=policy,
    )
    assert out is None


def test_assert_model_allowed_allows_explicit_when_no_key_models() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=[])
    out = svc.assert_model_allowed(
        model="composer-2.5",
        policy=EffectiveModelPolicy(allowed_models=[], ui_default_model=None),
        company_policy=policy,
    )
    assert out == "composer-2.5"


def test_assert_model_allowed_passes_default_id() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=[])
    out = svc.assert_model_allowed(
        model="default",
        policy=EffectiveModelPolicy(allowed_models=[], ui_default_model=None),
        company_policy=policy,
    )
    assert out == "default"


def test_assert_model_allowed_rejects_outside_ceiling() -> None:
    svc = AiModelPolicyService(AsyncMock())
    policy = CompanyAgentRuntimePolicy(model_allowlist=["gpt-5"])

    with pytest.raises(AppError) as ei:
        svc.assert_model_allowed(
            model="claude-sonnet",
            policy=EffectiveModelPolicy(allowed_models=[], ui_default_model=None),
            company_policy=policy,
        )
    assert ei.value.code == "MODEL_NOT_ALLOWED"
