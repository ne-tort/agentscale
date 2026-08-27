"""Employee login helpers."""

from __future__ import annotations

from prodavan.domain.companies.login import validate_login_username
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def employee_kc_email(login: str) -> str:
    return f"{validate_login_username(login)}@prodavan.local"


def employee_effective_login(employee: EmployeeRow) -> str:
    return (employee.login or "").strip() or employee.id
