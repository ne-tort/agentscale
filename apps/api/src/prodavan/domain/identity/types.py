"""Identity domain types (L01) — no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EmployeeStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipRole(StrEnum):
    """Soft link Employee ↔ Company (human roles inside org)."""

    COMPANY_ADMIN = "company.admin"
    MEMBER = "member"


class Contour(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    COMPANY = "company"
    EMPLOYEE = "employee"


# Keycloak realm roles (independent principals)
ROLE_PLATFORM_ADMIN = "platform.admin"
ROLE_COMPANY = "company"
ROLE_EMPLOYEE = "employee"

# Legacy / interim: DB membership role that also unlocks Company contour for a human Employee
ROLE_COMPANY_ADMIN = "company.admin"


@dataclass(frozen=True, slots=True)
class Principal:
    """OIDC subject after JWKS validation. Cabinets/projects never live here."""

    sub: str
    roles: frozenset[str] = field(default_factory=frozenset)
    email: str | None = None
    # preferred_username — for company login username == company_id
    username: str | None = None

    @property
    def is_platform_admin(self) -> bool:
        return ROLE_PLATFORM_ADMIN in self.roles

    @property
    def is_company_principal(self) -> bool:
        """Org Keycloak user (Company entity), not an Employee row."""
        return ROLE_COMPANY in self.roles


@dataclass(frozen=True, slots=True)
class WorkContext:
    """Request-scoped context from headers — not from JWT."""

    cabinet_id: str | None = None
    project_id: str | None = None
