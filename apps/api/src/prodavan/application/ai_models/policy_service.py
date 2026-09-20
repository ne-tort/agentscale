"""Effective model allowlist resolution for agent runtime."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.domain.admin.types import CompanyAgentRuntimePolicy
from prodavan.domain.ai_keys import ApiKind
from prodavan.infrastructure.persistence.models.ai_models import (
    AiKeyModelBindingRow,
    AiModelRow,
    AiModelSdkBindingRow,
)

_SDK_KINDS = frozenset(
    {
        ApiKind.CURSOR_SDK,
        ApiKind.CODEX_SDK,
        ApiKind.CLAUDE_AGENT_SDK,
    }
)


@dataclass(frozen=True)
class EffectiveModelPolicy:
    """Runtime filter + UI-only default (never auto-injected into agent config)."""

    allowed_models: list[str]
    ui_default_model: str | None

    @property
    def default_model(self) -> str | None:
        """Backward-compatible alias for UI preselection."""
        return self.ui_default_model


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
            if ceiling and api_kind not in _SDK_KINDS:
                return EffectiveModelPolicy(allowed_models=ceiling, ui_default_model=None)
            return EffectiveModelPolicy(allowed_models=[], ui_default_model=None)

        # For SDK api_kinds (cursor_sdk/codex_sdk/claude_agent_sdk) the SDK
        # binding decides which models the vendor SDK can serve — keep the
        # api_kind filter. For HTTP api_kinds (openai_api / anthropic_api /
        # openrouter / custom) any enabled catalog model is reachable through
        # the HTTP endpoint, so the SDK-binding api_kind filter must NOT drop
        # models whose only binding is e.g. cursor_sdk (seeded that way) — that
        # caused MODEL_NOT_ALLOWED for 'gemini-3.7-flash' on an openai_api key.
        sdk_kind_filter = [] if api_kind not in _SDK_KINDS else [
            AiModelSdkBindingRow.api_kind == api_kind,
        ]
        q = await self._session.execute(
            select(AiModelRow, AiKeyModelBindingRow.enabled, AiKeyModelBindingRow.is_default)
            .join(AiKeyModelBindingRow, AiKeyModelBindingRow.model_id == AiModelRow.id)
            .join(AiModelSdkBindingRow, AiModelSdkBindingRow.model_id == AiModelRow.id)
            .where(
                AiKeyModelBindingRow.key_id == key_id,
                AiKeyModelBindingRow.enabled.is_(True),
                *sdk_kind_filter,
            )
            .order_by(AiModelRow.name)
        )
        rows = list(q.all())
        # Build the allowed id set from the catalog entry's machine ids
        # (key_aliases / model_ids) plus the human-readable name for backward
        # compat. Runtime/probe/live paths pass machine ids ("composer-2.5"),
        # so matching by name alone (now human-readable after the catalog
        # rename) would reject every valid model with MODEL_NOT_ALLOWED.
        allowed: list[str] = []
        allowed_lower: set[str] = set()
        ui_default_model: str | None = None
        for row, enabled, is_default in rows:
            if not enabled:
                continue
            ids_for_row: list[str] = []
            aliases = row.key_aliases if isinstance(row.key_aliases, list) else []
            for alias in aliases:
                s = str(alias).strip()
                if s and s.lower() not in allowed_lower:
                    ids_for_row.append(s)
                    allowed_lower.add(s.lower())
            name = str(row.name or "").strip()
            if name and name.lower() not in allowed_lower:
                ids_for_row.append(name)
                allowed_lower.add(name.lower())
            if is_default and ui_default_model is None:
                ui_default_model = ids_for_row[0] if ids_for_row else name
            allowed.extend(ids_for_row)
        if ceiling:
            ceiling_lower = {str(m).strip().lower() for m in ceiling if str(m).strip()}
            allowed = [m for m in allowed if m.lower() in ceiling_lower]
            if ui_default_model is not None and ui_default_model.lower() not in ceiling_lower:
                ui_default_model = None
        if not allowed and ceiling:
            if api_kind in _SDK_KINDS:
                return EffectiveModelPolicy(allowed_models=[], ui_default_model=None)
            return EffectiveModelPolicy(allowed_models=ceiling, ui_default_model=None)
        return EffectiveModelPolicy(allowed_models=allowed, ui_default_model=ui_default_model)

    def assert_model_allowed(
        self,
        *,
        model: str | None,
        policy: EffectiveModelPolicy,
        company_policy: CompanyAgentRuntimePolicy,
    ) -> str | None:
        if model is None:
            return None
        explicit = str(model).strip()
        if not explicit:
            return None
        ceiling = [m for m in (company_policy.model_allowlist or []) if str(m).strip()]
        if ceiling and explicit not in ceiling:
            from prodavan.domain.errors import AppError

            raise AppError(
                code="MODEL_NOT_ALLOWED",
                title="Model not allowed",
                status=403,
                detail=f"model {explicit!r} not in company model_allowlist",
            )
        if policy.allowed_models and explicit not in policy.allowed_models:
            from prodavan.domain.errors import AppError

            raise AppError(
                code="MODEL_NOT_ALLOWED",
                title="Model not allowed",
                status=403,
                detail=f"model {explicit!r} not enabled for AI key",
            )
        return explicit
