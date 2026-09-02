"""Effective model allowlist resolution for agent runtime."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.infrastructure.persistence.models.ai_models import (
    AiKeyModelBindingRow,
    AiModelRow,
    AiModelSdkBindingRow,
)


@dataclass(frozen=True)
class EffectiveModelPolicy:
    allowed_models: list[str]
    default_model: str | None


class AiModelPolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_for_key(
        self,
        *,
        company_id: str,
        key_id: str | None,
        api_kind: str,
        company_policy: CompanyAgentRuntimePolicy | None = None,
    ) -> EffectiveModelPolicy:
        policy = company_policy or await AdminCompanyService(self._session).get_agent_policy(company_id)
        ceiling = [m for m in (policy.model_allowlist or []) if str(m).strip()]

        if not key_id:
            if ceiling:
                return EffectiveModelPolicy(allowed_models=ceiling, default_model=ceiling[0])
            return EffectiveModelPolicy(allowed_models=[], default_model=None)

        q = await self._session.execute(
            select(AiModelRow.name, AiKeyModelBindingRow.enabled, AiKeyModelBindingRow.is_default)
            .join(AiKeyModelBindingRow, AiKeyModelBindingRow.model_id == AiModelRow.id)
            .join(AiModelSdkBindingRow, AiModelSdkBindingRow.model_id == AiModelRow.id)
            .where(
                AiKeyModelBindingRow.key_id == key_id,
                AiKeyModelBindingRow.enabled.is_(True),
                AiModelSdkBindingRow.api_kind == api_kind,
            )
            .order_by(AiModelRow.name)
        )
        rows = list(q.all())
        allowed = [str(name) for name, _, _ in rows]
        if ceiling:
            ceiling_set = set(ceiling)
            allowed = [m for m in allowed if m in ceiling_set]
        default_model = None
        for name, _, is_default in rows:
            if is_default and (not ceiling or name in ceiling):
                default_model = str(name)
                break
        if default_model is None and allowed:
            default_model = allowed[0]
        if not allowed and ceiling:
            return EffectiveModelPolicy(allowed_models=ceiling, default_model=ceiling[0])
        return EffectiveModelPolicy(allowed_models=allowed, default_model=default_model)

    def assert_model_allowed(
        self,
        *,
        model: str | None,
        policy: EffectiveModelPolicy,
        company_policy: CompanyAgentRuntimePolicy,
    ) -> str | None:
        if model is None:
            return policy.default_model
        ceiling = [m for m in (company_policy.model_allowlist or []) if str(m).strip()]
        if ceiling and model not in ceiling:
            from prodavan.domain.errors import AppError

            raise AppError(
                code="MODEL_NOT_ALLOWED",
                title="Model not allowed",
                status=403,
                detail=f"model {model!r} not in company model_allowlist",
            )
        if policy.allowed_models and model not in policy.allowed_models:
            from prodavan.domain.errors import AppError

            raise AppError(
                code="MODEL_NOT_ALLOWED",
                title="Model not allowed",
                status=403,
                detail=f"model {model!r} not enabled for AI key",
            )
        return model
