"""Public DTO helpers for project_service BC."""

from __future__ import annotations

from prodavan.domain.ai_keys import AiProvider
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

_ALLOWED_PROVIDERS = frozenset(p.value for p in AiProvider)


def agent_provider_from_key_row(row: AiProviderKeyRow) -> str:
    """Map selected AI key → project agent_provider for launch/runtime."""
    provider = (row.provider or "").strip()
    if provider in _ALLOWED_PROVIDERS:
        return provider
    api_kind = (row.api_kind or "").strip()
    if api_kind == "anthropic_api":
        return AiProvider.CLAUDE_CODE.value
    return AiProvider.CODEX.value


def normalize_agent_provider(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip()
    if normalized not in _ALLOWED_PROVIDERS:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"agent_provider must be one of: {', '.join(sorted(_ALLOWED_PROVIDERS))}",
        )
    return normalized


def project_public(
    row: ProjectRow,
    *,
    limits: dict | None = None,
    company_subscription: dict | None = None,
    created_by_login: str | None = None,
) -> dict:
    visibility = getattr(row, "visibility_mode", None) or "cabinet_shared"
    out = {
        "id": row.id,
        "company_id": row.company_id,
        "cabinet_id": row.cabinet_id,
        "owner_employee_id": row.owner_employee_id,
        "created_by_employee_id": row.owner_employee_id,
        "created_by_login": created_by_login,
        "name": row.name,
        "slug": row.slug,
        "status": row.status,
        "visibility_mode": visibility,
        "workspace_key": row.workspace_key,
        "container_ref": row.container_ref,
        "agent_provider": row.agent_provider,
        "about": getattr(row, "about", None),
        "resolved_ai_key_id": getattr(row, "resolved_ai_key_id", None),
        "budget_tokens": getattr(row, "budget_tokens", None),
        "workspace_outdated_at": (
            row.workspace_outdated_at.isoformat() if row.workspace_outdated_at else None
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if limits is not None:
        out["limits"] = limits
    if company_subscription is not None:
        out["company_subscription"] = company_subscription
    return out
