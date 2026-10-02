"""Employee login handles — «{name}@{company}.local».

The employee handle is globally unique by construction (company slug is
unique), so the plain `login` column keeps its UNIQUE constraint; the local
part (employee name) is unique within a company by the same constraint.
Keycloak username and email of an employee are the full handle.
"""

from __future__ import annotations

from prodavan.domain.companies.login import validate_login_username
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def employee_login_handle(login_local: str, company_login_slug: str) -> str:
    """Full login handle: `{local}@{company_slug}.local` (company slug from
    the creating company; stable afterwards even if the company renames)."""
    local = validate_login_username(login_local)
    slug = (company_login_slug or "").strip().lower()
    if not slug:
        raise ValueError("company login slug required for employee handle")
    return f"{local}@{slug}.local"


def employee_kc_email(login_local: str, company_login_slug: str) -> str:
    """Keycloak email for an employee — the full handle."""
    return employee_login_handle(login_local, company_login_slug)


def employee_effective_login(employee: EmployeeRow) -> str:
    """Login username for Keycloak and UI — the full handle (login column
    stores it since the handles migration)."""
    return (employee.login or "").strip() or employee.id
