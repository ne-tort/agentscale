"""Public DTO helpers for project_service BC."""

from __future__ import annotations

from prodavan.domain.ai_keys import AiProvider
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.projects import ProjectRow

_ALLOWED_PROVIDERS = frozenset(p.value for p in AiProvider)


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
) -> dict:
    visibility = getattr(row, "visibility_mode", None) or "cabinet_shared"
    out = {
        "id": row.id,
        "company_id": row.company_id,
        "cabinet_id": row.cabinet_id,
        "owner_employee_id": row.owner_employee_id,
        "created_by_employee_id": row.owner_employee_id,
        "name": row.name,
        "slug": row.slug,
        "status": row.status,
        "visibility_mode": visibility,
        "workspace_key": row.workspace_key,
        "container_ref": row.container_ref,
        "primary_runtime_unit_id": getattr(row, "primary_runtime_unit_id", None),
        "agent_provider": row.agent_provider,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if limits is not None:
        out["limits"] = limits
    if company_subscription is not None:
        out["company_subscription"] = company_subscription
    return out
