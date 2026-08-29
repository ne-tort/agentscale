"""Unified lifecycle helpers — pause / soft-delete / purge (two-axis model).

visibility: live | soft_deleted
runtime:    active | paused

inert = soft_deleted OR paused → freeze writes/agent + stop Pod.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement

from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import EmployeeStatus
from prodavan.domain.projects import ProjectStatus


def company_is_soft_deleted(row: Any) -> bool:
    return getattr(row, "deleted_at", None) is not None


def company_is_paused(row: Any) -> bool:
    return str(getattr(row, "status", "active") or "active") == "paused"


def company_is_inert(row: Any) -> bool:
    return company_is_soft_deleted(row) or company_is_paused(row)


def employee_is_soft_deleted(row: Any) -> bool:
    return getattr(row, "deleted_at", None) is not None


def employee_is_paused(row: Any) -> bool:
    """``disabled`` is the paused (visible) runtime state."""
    return str(getattr(row, "status", "") or "") == EmployeeStatus.DISABLED


def employee_is_inert(row: Any) -> bool:
    return employee_is_soft_deleted(row) or employee_is_paused(row)


def cabinet_is_soft_deleted(row: Any) -> bool:
    return str(getattr(row, "status", "") or "") == CabinetStatus.DELETED


def cabinet_is_paused(row: Any) -> bool:
    """``archived`` is the paused (visible) runtime state."""
    return str(getattr(row, "status", "") or "") == CabinetStatus.ARCHIVED


def cabinet_is_inert(row: Any) -> bool:
    return cabinet_is_soft_deleted(row) or cabinet_is_paused(row)


def project_is_soft_deleted(row: Any) -> bool:
    return str(getattr(row, "status", "") or "") == ProjectStatus.DELETED


def project_is_draft(row: Any) -> bool:
    return str(getattr(row, "status", "") or "") == ProjectStatus.DRAFT


def project_is_paused(row: Any) -> bool:
    return str(getattr(row, "status", "") or "") == ProjectStatus.PAUSED


def project_is_completed(row: Any) -> bool:
    return str(getattr(row, "status", "") or "") == ProjectStatus.COMPLETED


def project_is_inert(row: Any) -> bool:
    return project_is_soft_deleted(row) or project_is_paused(row) or project_is_completed(row)


def company_alive_clause(model: Any) -> ColumnElement[bool]:
    return model.deleted_at.is_(None)


def employee_alive_clause(model: Any) -> ColumnElement[bool]:
    return model.deleted_at.is_(None)


def cabinet_alive_clause(model: Any) -> ColumnElement[bool]:
    return model.status != CabinetStatus.DELETED


def project_alive_clause(model: Any) -> ColumnElement[bool]:
    return model.status != ProjectStatus.DELETED


def project_active_runtime_clause(model: Any) -> ColumnElement[bool]:
    return model.status == ProjectStatus.ACTIVE


def raise_if_soft_deleted(*, entity: str = "Entity") -> None:
    raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail=f"{entity} not found")


def raise_if_paused(*, code: str = "ENTITY_PAUSED", detail: str = "entity is paused") -> None:
    raise AppError(code=code, title="Paused", status=409, detail=detail)


def soft_deleted_at_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
